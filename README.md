# LinkedIn → Substack Agent

An agentic application that takes a LinkedIn post, rewrites it into a well-formatted Substack newsletter article using an LLM, and publishes it — with a human-in-the-loop editing step before anything goes live. A confirmation email is sent to the user after publishing.

---

## Demo

**Live Substack:** https://shyam274271.substack.com

---

## What It Does

1. **Ingest** — Paste a LinkedIn post URL (auto-scrape) or paste text manually
2. **Configure** — Select tone and provide optional edit instructions to the agent
3. **Review (HITL)** — Edit title, subtitle, body directly; live preview; regenerate with new instructions
4. **Publish** — One-click publish to Substack via its internal REST API
5. **Email** — Confirmation email with the live post link sent via Gmail SMTP

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│              Streamlit UI (app.py)               │
│  Step 1: Ingest → Step 2: Configure →           │
│  Step 3: Review [HITL] → Step 4: Publish →      │
│  Step 5: Done                                    │
└────────────┬──────────────────┬─────────────────┘
             │                  │
             ▼                  ▼
   ┌──────────────────┐  ┌──────────────────────┐
   │  ContentAgent    │  │   SubstackClient     │
   │  (Mistral API)   │  │  (Unofficial REST)   │
   └──────────────────┘  └──────────┬───────────┘
             │                      │
             ▼                      ▼
   ┌──────────────────┐  ┌──────────────────────┐
   │  Mistral         │  │  ImageHandler        │
   │  mistral-small   │  │  (download + upload) │
   └──────────────────┘  └──────────────────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │   EmailSender        │
                         │   (Gmail SMTP)       │
                         └──────────────────────┘
```

### File Structure

```
linkedin_agent/
├── app.py                    # Streamlit UI — 5-step state machine
├── setup_session.py          # One-time LinkedIn session saver (run once)
├── agents/
│   └── content_agent.py      # Mistral rewriting agent
├── tools/
│   ├── linkedin_scraper.py   # Playwright-based LinkedIn post scraper
│   ├── substack_client.py    # Substack unofficial API wrapper
│   ├── image_handler.py      # Image download + Substack CDN upload
│   └── email_sender.py       # Gmail SMTP confirmation email
├── prompts/
│   ├── system_prompts.py     # Base rewriter system prompt
│   └── tone_modifiers.py     # Per-tone prompt injections
├── config.py                 # Env var loading
├── session.json              # LinkedIn session state (gitignored, created by setup_session.py)
├── .env                      # Secrets (gitignored)
├── .env.example              # Template
└── requirements.txt
```

---

## Setup

### 1. Clone and create virtual environment

```bash
git clone <repo-url>
cd linkedin_agent
python -m venv venv
venv/Scripts/activate        # Windows
# source venv/bin/activate   # macOS/Linux
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure `.env`

Copy `.env.example` to `.env` and fill in all values:

```bash
cp .env.example .env
```

```env
# Mistral
MISTRAL_API_KEY=your_mistral_api_key
MISTRAL_MODEL=mistral-small-latest

# Substack
SUBSTACK_PUBLICATION_URL=https://yourname.substack.com
SUBSTACK_SID=your_substack_sid_cookie
SUBSTACK_LLI=your_substack_lli_cookie

# Email
EMAIL_SENDER=your@gmail.com
EMAIL_APP_PASSWORD=your_16_char_app_password
```

**Getting Substack cookies:**
1. Log into Substack in your browser
2. Open DevTools → Application → Cookies → `https://substack.com`
3. Copy the values for `substack.sid` and `substack.lli`

**Getting Gmail App Password:**
1. Google Account → Security → 2-Step Verification → App passwords
2. Create a new app password (Mail / Other)
3. Use the 16-character code — not your regular password

### 4. (Optional) Enable LinkedIn URL Scraping

URL scraping uses Playwright with a saved browser session. This is a one-time setup:

```bash
venv/Scripts/python setup_session.py
```

A visible Chromium browser will open. Log in to LinkedIn normally. Once you reach the home feed, the session is saved to `session.json` and the browser closes automatically. You will not need to repeat this unless you log out of LinkedIn or the session expires (~30 days).

**Without this step:** URL scraping is disabled in the UI and the app falls back to manual paste — no errors, no broken flow.

**Re-run when scraping stops working** (session expired):
```bash
venv/Scripts/python setup_session.py
```

> `session.json` is gitignored — it contains your LinkedIn session cookies and must never be committed to a repository.

### 5. Run

```bash
venv/Scripts/python -m streamlit run app.py --server.headless true
```

Open http://localhost:8501

---

## Design Decisions

### LinkedIn Ingestion — Playwright with Saved Session + Manual Paste Fallback

**Decision:** Playwright-based URL scraping with a saved browser `storage_state`, with manual paste always available as a fallback.

**Why Playwright over requests/BeautifulSoup:**
LinkedIn's pages are JavaScript-rendered (React SPA). Static HTTP requests return a bare shell with no content. Even with a valid `li_at` session cookie, LinkedIn's servers detect non-browser clients via User-Agent, missing browser headers, and timing patterns and return challenge pages or empty feeds.

Playwright launches a real Chromium browser (the same engine Chrome uses), which executes JavaScript and loads content exactly as a real user would. Combined with a real saved session, this is indistinguishable from normal browsing.

**Why saved session over programmatic login:**
Programmatic login (automating the username/password form) is the most detectable pattern — LinkedIn's security systems flag it even with human-like delays. A saved session avoids login entirely: the user logs in manually once, the session state is persisted, and all subsequent scrape calls reuse it as a returning logged-in user with no login event to detect.

**Safety design:**
- `headless=False` by default — a visible browser window is significantly harder to fingerprint than a headless one. Headless browsers have distinct rendering artifacts detectable via JavaScript.
- `--disable-blink-features=AutomationControlled` — removes the `navigator.webdriver` flag that Chrome sets in automated contexts. Without this, any page can detect automation via `navigator.webdriver === true`.
- `webdriver` property removal via `add_init_script` — belt-and-suspenders approach: removes the flag at the JavaScript level even if the CLI flag doesn't fully suppress it.
- Human-like delays (`random.uniform(1.2, 2.4)` seconds) — real users don't trigger page loads and immediately query the DOM. The delay mimics natural reading/processing time.
- Single post per call — this tool is not a bulk scraper. One URL, one post, triggered by a human clicking a button.
- `ScraperError` fallback — any failure (login wall, DOM change, timeout, network error) raises `ScraperError`, which the UI catches and displays as a non-fatal warning. The manual paste path immediately becomes available with no disruption to the workflow.

**Why not the `joeyism/linkedin_scraper` library:**
That library uses `requests` + BeautifulSoup for scraping, which fails on LinkedIn's JS-rendered pages. It also requires `li_at` cookie handling that is more involved than Playwright's `storage_state`. Direct Playwright with our safety wrapper gives us full control over the browser and detection surface.

**CSS selector strategy:**
LinkedIn changes their DOM structure periodically. The scraper tries 5 different CSS selectors for post text in order of preference, falling back to the full `<main>` text if all specific selectors fail. This makes the scraper resilient to incremental DOM changes without a complete rewrite.

---

### LLM — Mistral `mistral-small-latest`

**Decision:** Mistral API via their free tier (`mistral-small-latest`).

**Why:**
- Free tier available with no credit card required — accessible for a demo assignment
- `mistral-small-latest` is capable of producing high-quality long-form content with structured JSON output
- Supports `response_format: {"type": "json_object"}` which forces valid JSON output, eliminating parsing fragility
- OpenAI-compatible API format — clean, simple integration

**Why not Claude (Anthropic):**
Mistral's free tier was available and sufficient. Claude would also be an excellent choice for this task (and is arguably stronger for long-form writing), but would require a paid API key.

**Output format decision:**
The agent is instructed to return structured JSON:
```json
{
  "title": "...",
  "subtitle": "...",
  "sections": [
    {"type": "paragraph", "content": "..."},
    {"type": "heading", "level": 2, "content": "..."}
  ]
}
```
This avoids parsing markdown or HTML from the LLM output, which is unreliable. Structured JSON maps directly to Substack's ProseMirror document format with no ambiguity.

---

### Substack Publishing — Reverse-Engineered Internal API

This is the most interesting engineering challenge in the project. Here is exactly how we arrived at the solution.

**The problem:**
Substack has no public API for publishing posts. Their official Developer API (launched 2025) only returns public profile data and explicitly does not support programmatic publishing.

**How we reverse-engineered it:**
1. Opened Substack's web editor in Chrome
2. Opened DevTools → Network tab → filtered for `api/v1`
3. Performed actions manually (create post, add content, publish) and observed every HTTP request
4. Identified the internal REST endpoints Substack's own editor uses:

```
POST  /api/v1/drafts                        ← create draft
PUT   /api/v1/drafts/{id}                   ← update draft
GET   /api/v1/drafts/{id}/prepublish        ← pre-publish validation
POST  /api/v1/drafts/{id}/publish           ← publish
POST  /api/v1/image                         ← upload image to CDN
GET   /api/v1/user/profile/self             ← get user ID
```

5. Identified the authentication mechanism: session cookies (`substack.sid` and `substack.lli`) set when logged in via browser
6. Identified the request body format for draft creation — critically, `draft_body` is **not plain HTML** but a stringified ProseMirror JSON document:
```json
{
  "type": "doc",
  "content": [
    {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "..."}]},
    {"type": "paragraph", "content": [{"type": "text", "text": "..."}]}
  ]
}
```
7. Found that publishing requires a 3-step flow: `create_draft → prepublish → publish`. Skipping `prepublish` causes the publish to fail.

**Rate limiting strategy:**
We add a `time.sleep(1)` between every Substack API call. This is a deliberate, conservative choice:
- Community research suggests Substack's internal API rate-limits aggressive clients at approximately 1 request/second
- Our workflow makes exactly 5 API calls per publish (get_user_id → create_draft → prepublish → publish, + image upload per image)
- At 1 second apart, the full publish flow takes ~5 seconds — imperceptible to the user, zero risk of rate-limiting or account flagging
- This is nowhere near bulk automation; it's a single human-triggered action

**Authentication:**
Two session cookies are required:
- `substack.sid` — the main session cookie, URL-encoded Connect.js format
- `substack.lli` — "likely logged in" JWT, short-lived presence indicator

Both are sent as a raw `Cookie` header (not via the `requests` cookie jar) to avoid double-encoding the URL-encoded SID value. Cookie lifetime is approximately 30 days.

**Why not use the `python-substack` library:**
The `ma2za/python-substack` library was evaluated but we chose to write direct HTTP calls instead because:
- Full control over the exact request format and headers
- No dependency on a library that may change or break
- We already knew the exact endpoints from the reverse-engineering step
- The library's authentication with Google OAuth accounts (no password) requires additional handling
- Fewer moving parts = easier to debug

---

### UI — Streamlit

**Decision:** Streamlit over Gradio.

**Why:**
- Streamlit's `session_state` is purpose-built for multi-step stateful flows — each step persists data across rerenders cleanly
- The 5-step pipeline (ingest → configure → generate → review → publish) maps naturally to Streamlit's linear execution model
- `st.columns` gives a side-by-side edit + preview layout for the HITL step without custom CSS
- `st.components.v1.html()` provides an isolated iframe for the preview, necessary for reliable HTML rendering with proper scrolling

Gradio was considered but its interface model is better suited to single-function demos (input → output) than multi-step pipelines with persistent state and branching logic.

---

### Human-in-the-Loop (HITL) Design

**Where the pause happens:** After generation (Step 3), before publishing (Step 4).

**Why here and not earlier/later:**
- Too early (before generation): the user hasn't seen the content yet — no basis for decision
- Too late (after publishing): defeats the purpose
- Step 3 is the right control point: the user sees the full generated article, can edit any field directly, can regenerate with new instructions, and only then approves publishing

**What the user controls:**
1. **Direct editing** — title, subtitle, and body are all editable text fields
2. **Tone selection** — 5 tones (Professional, Conversational, Analytical, Storytelling, Educational) injected into the prompt
3. **Edit instructions** — free-text instructions to the agent ("make the intro shorter", "add a section on ROI")
4. **Regeneration** — full regeneration with new tone/instructions, as many times as needed
5. **Final approval** — explicit "Approve & Publish" button — nothing publishes without user confirmation

---

### Email — Gmail SMTP

**Decision:** Gmail SMTP with App Password over SendGrid or other services.

**Why:**
- No new account or service required — uses an existing Gmail account
- Gmail App Passwords are Google's official mechanism for programmatic SMTP access — not a hack
- Gmail allows 500 emails/day on free accounts — more than sufficient for a demo
- SMTP_SSL (port 465) is simpler and more reliable than STARTTLS (port 587) for Windows environments

**Security note:** Gmail App Passwords require 2-Step Verification to be enabled. The app password is a 16-character token that can be revoked independently of the main account password.

---

### Image Pipeline

LinkedIn images cannot be embedded directly in Substack posts (cross-origin restrictions, LinkedIn CDN requires auth). The pipeline:

1. Download image from LinkedIn URL to a local temp file (`requests` with browser `User-Agent` + `Referer: https://www.linkedin.com/`)
2. Upload to Substack's CDN via `POST /api/v1/image` → returns a `substackcdn.com` URL
3. Inject the Substack CDN URL into the ProseMirror document as a `captionedImage` node
4. Delete the temp file

Images are transferred non-blockingly — if an image download or upload fails, a warning is shown but publishing continues with the remaining content.

**How we discovered the correct upload format (the hard way):**

The initial implementation used `requests`' `files=` parameter, which sends `multipart/form-data`. Substack returned `400 {"errors":[{"location":"body","param":"image","msg":"Invalid value"}]}` every time — no matter the field name, filename, or MIME type. After ruling out wrong endpoint, invalid downloaded content, and session issues, we inspected the `python-substack` open-source library and found the actual format Substack expects:

```python
# WRONG — what we tried first:
files={"image": ("image.jpg", file_obj, "image/jpeg")}   # multipart/form-data → 400

# CORRECT — what Substack actually wants:
data={"image": b"data:image/jpeg;base64," + base64.b64encode(file_bytes)}  # form-encoded base64 data URI
```

Three things were wrong simultaneously:
- **Encoding:** raw binary multipart vs. base64 data URI
- **Request type:** `files=` (`multipart/form-data`) vs. `data=` (`application/x-www-form-urlencoded`)
- Substack's error message (`"Invalid value"`) gives no hint about which of these is the issue

The correct format was confirmed by reading the source of `ma2za/python-substack` on GitHub.

---

## Known Limitations

| Limitation | Detail |
|---|---|
| LinkedIn scraping | Playwright-based; requires one-time `setup_session.py` run. Falls back to manual paste if session missing/expired. |
| LinkedIn session expires | `session.json` expires in ~30 days. Re-run `setup_session.py` to refresh. |
| LinkedIn DOM changes | LinkedIn changes CSS selectors periodically. Multiple fallback selectors are tried; worst case, manual paste is always available. |
| Substack cookies expire | `substack.sid` / `substack.lli` expire in ~30 days. Refresh by copying new values from browser DevTools. |
| Substack API stability | The internal API is undocumented and unsupported. Substack can change it without notice. |
| Mistral free tier | Rate-limited. Sufficient for demos and prototyping; not for high-volume use. |
| Image formats | Only JPEG, PNG, GIF, WebP supported. LinkedIn PDFs or video thumbnails are skipped. |
| No draft saving | Content is lost if the browser tab closes before publishing. Session state is in-memory only. |

---

## Dependencies

```
streamlit>=1.32.0      # UI framework
mistralai>=1.0.0       # Mistral LLM client
requests>=2.31.0       # HTTP for Substack API + image download
python-dotenv>=1.0.0   # .env loading
playwright>=1.40.0     # LinkedIn URL scraping (optional feature)
```

After installing `playwright`, install the browser binary once:
```bash
venv/Scripts/python -m playwright install chromium
```

---

## Security Notes

- All credentials live in `.env` which is gitignored — never committed to the repo
- `session.json` contains LinkedIn session cookies — gitignored, treat like a password, never commit
- Substack session cookies should be treated like passwords — rotate after the demo
- Gmail App Password can be revoked from Google Account settings at any time
- Mistral API key should be regenerated after the demo
