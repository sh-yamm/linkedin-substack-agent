"""
One-time LinkedIn session setup.

Run this ONCE before using the LinkedIn URL scraping feature:
    venv/Scripts/python setup_session.py

A visible Chromium browser will open. Log into LinkedIn normally.
The session is saved to session.json and reused by the app on every run.
You will NOT need to run this again unless you log out of LinkedIn
or the session expires (~30 days).
"""

import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

SESSION_FILE = "session.json"


def main():
    print("=" * 60)
    print("  LinkedIn Session Setup")
    print("=" * 60)
    print()
    print("A browser window will open. Please:")
    print("  1. Log into LinkedIn as you normally would")
    print("  2. Complete any 2FA or verification if prompted")
    print("  3. Wait until you see the LinkedIn home feed")
    print("  4. The browser will close automatically")
    print()
    print("Starting browser...")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()

        # Remove webdriver fingerprint
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page.goto("https://www.linkedin.com/login")

        print("Waiting for you to log in and reach the LinkedIn feed...")
        print("(You have 3 minutes)")
        print()

        try:
            # Wait until URL contains /feed/ — indicates successful login
            page.wait_for_url("**/feed/**", timeout=180_000)
        except Exception:
            # Also accept any post-login page (e.g. /mynetwork/, /jobs/, etc.)
            try:
                page.wait_for_function(
                    "window.location.hostname === 'www.linkedin.com' && "
                    "!window.location.pathname.includes('/login') && "
                    "!window.location.pathname.includes('/checkpoint')",
                    timeout=180_000,
                )
            except Exception:
                print("ERROR: Login timed out or was not detected.")
                print("Please try again.")
                browser.close()
                sys.exit(1)

        # Save full browser storage state (cookies + localStorage)
        context.storage_state(path=SESSION_FILE)
        browser.close()

    print(f"Session saved to {SESSION_FILE}")
    print()
    print("You can now use LinkedIn URL scraping in the app.")
    print("Re-run this script if scraping stops working (session expired).")


if __name__ == "__main__":
    main()
