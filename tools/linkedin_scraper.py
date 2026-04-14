"""
LinkedIn post scraper using Playwright with a saved browser session.

Safety design:
- Uses a real saved session (no programmatic login) — looks like a returning user
- headless=False by default — visible browser is harder to fingerprint as automated
- Single post per call, human-like delays between actions
- Graceful failure: raises ScraperError so the UI can fall back to manual paste
- Does NOT store credentials anywhere

Windows / Streamlit compatibility:
- sync_playwright() uses asyncio internally to launch browser subprocesses.
- Streamlit runs a SelectorEventLoop on Windows, which does not support
  subprocess creation (raises NotImplementedError).
- Fix: run the scrape inside a dedicated thread that sets up its own
  ProactorEventLoop before calling sync_playwright().
"""

import asyncio
import random
import sys
import threading
import time
from pathlib import Path

# On Windows, sync_playwright() creates a new event loop via asyncio.new_event_loop().
# The loop type is determined by the active policy. The default SelectorEventLoop
# does not support subprocess creation (raises NotImplementedError). Switch to
# ProactorEventLoop globally so Playwright's internal loop can launch the browser.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

SESSION_FILE = "session.json"

# LinkedIn CDN domain for real post images (not thumbnails/avatars)
LINKEDIN_IMAGE_CDN = "media.licdn.com"

# Selectors tried in order — LinkedIn changes these periodically
POST_TEXT_SELECTORS = [
    ".update-components-text",
    ".feed-shared-update-v2__description",
    ".feed-shared-text",
    ".feed-shared-text-view",
    "[data-test-id='main-feed-activity-card__commentary']",
    "article .break-words",
]

POST_IMAGE_SELECTORS = [
    ".update-components-image img",
    ".feed-shared-image__container img",
    ".feed-shared-image img",
    ".update-components-linkedin-video__thumbnail img",
]


class ScraperError(Exception):
    """Raised when scraping fails — UI should fall back to manual paste."""
    pass


def _human_delay(min_s: float = 1.2, max_s: float = 2.4):
    """Random sleep to mimic human reading/interaction time."""
    time.sleep(random.uniform(min_s, max_s))


def _scrape_impl(url: str, headless: bool) -> dict:
    """
    Core scraping logic. Must be called inside a thread that has a
    ProactorEventLoop set (on Windows) to allow subprocess creation.
    """
    session_path = Path(SESSION_FILE)
    if not session_path.exists():
        raise ScraperError(
            "LinkedIn session not found. "
            "Run  venv/Scripts/python setup_session.py  first."
        )

    text = ""
    images = []

    print(f"[scraper] starting | url={url} | headless={headless}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            print("[scraper] browser launched")

            context = browser.new_context(
                storage_state=str(session_path),
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )

            page = context.new_page()

            # Remove the webdriver property that flags automated browsers
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            # Navigate to the post
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                print(f"[scraper] page loaded | final_url={page.url}")
            except PWTimeout:
                raise ScraperError(f"Page load timed out: {url}")

            # Human-like pause after page load
            _human_delay(1.5, 2.5)

            # Check we're not on a login wall
            if "/login" in page.url or "/checkpoint" in page.url:
                raise ScraperError(
                    "LinkedIn redirected to login. "
                    "Session may have expired — re-run setup_session.py."
                )

            # Scroll slightly to trigger lazy-loaded content
            page.mouse.wheel(0, 300)
            _human_delay(0.8, 1.4)

            # ── Extract post text ────────────────────────────────────────────
            matched_selector = None
            for selector in POST_TEXT_SELECTORS:
                try:
                    locator = page.locator(selector).first
                    if locator.is_visible(timeout=2_000):
                        text = locator.inner_text(timeout=3_000).strip()
                        if text:
                            matched_selector = selector
                            break
                except Exception:
                    continue

            if text:
                print(f"[scraper] text extracted | selector='{matched_selector}' | chars={len(text)}")
            else:
                # Fallback: grab all visible text from the main content area
                print("[scraper] no selector matched — falling back to <main> text")
                try:
                    text = page.locator("main").first.inner_text(timeout=5_000).strip()
                    print(f"[scraper] fallback text | chars={len(text)}")
                except Exception:
                    pass

            if not text:
                raise ScraperError(
                    "Could not extract post text. "
                    "LinkedIn may have changed their DOM layout."
                )

            # ── Extract images ───────────────────────────────────────────────
            for selector in POST_IMAGE_SELECTORS:
                try:
                    imgs = page.locator(selector).all()
                    for img in imgs:
                        src = img.get_attribute("src") or ""
                        # Only include real content images from LinkedIn's CDN
                        if LINKEDIN_IMAGE_CDN in src and "profile" not in src:
                            images.append(src)
                except Exception:
                    continue

            # Deduplicate while preserving order
            seen = set()
            images = [x for x in images if not (x in seen or seen.add(x))]
            print(f"[scraper] images found = {len(images)}")
            for i, img_url in enumerate(images):
                print(f"[scraper]   [{i}] {img_url[:80]}...")

            browser.close()
            print("[scraper] browser closed")

    except ScraperError:
        raise
    except Exception as e:
        raise ScraperError(f"Scraping failed: {e}") from e

    return {"text": _clean_text(text), "images": images}


def scrape_post(url: str, headless: bool = False) -> dict:
    """
    Scrape a LinkedIn post by URL using a saved browser session.

    Runs _scrape_impl in a dedicated thread with a ProactorEventLoop (Windows)
    to avoid the NotImplementedError that occurs when sync_playwright tries to
    create a subprocess inside Streamlit's SelectorEventLoop.

    Args:
        url:      Full LinkedIn post URL
        headless: If False (default), opens a visible browser window.

    Returns:
        {"text": str, "images": list[str]}

    Raises:
        ScraperError: on any failure — caller should fall back to manual paste.
    """
    result = {}
    error = None

    def _thread_target():
        nonlocal result, error
        try:
            result = _scrape_impl(url, headless)
        except ScraperError as e:
            error = e
        except Exception as e:
            error = ScraperError(f"Scraping failed: {e}")

    t = threading.Thread(target=_thread_target, daemon=True)
    t.start()
    t.join()

    if error:
        raise error
    return result


def _clean_text(text: str) -> str:
    """Remove LinkedIn UI noise from extracted page text."""
    noise_phrases = [
        "Like", "Comment", "Repost", "Send", "Follow",
        "Connect", "More", "See more", "See less",
        "reactions", "comments", "reposts",
        "Report this post",
    ]
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and stripped not in noise_phrases:
            lines.append(stripped)

    return "\n".join(lines)
