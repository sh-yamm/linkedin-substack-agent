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

This is the most technically challenging part of the project. Substack has **no public API for publishing**. Everything here was discovered by reading network traffic and open-source library source code.

---

#### The Problem

Substack's official Developer API (launched 2025) is read-only — it returns public subscriber and post data but explicitly does not support creating or publishing posts. The only way to programmatically publish is to use the same internal REST endpoints that Substack's own web editor uses.

---

#### How We Found the Endpoints

1. Logged into Substack in Chrome, opened the post editor
2. Opened DevTools → Network tab → filtered by `api/v1`
3. Performed every action manually: create draft, type content, upload an image, click Publish
4. Captured every request: URL, method, headers, request body, response body

This revealed the full internal API surface:

```
GET   /api/v1/user/profile/self             ← get logged-in user's numeric ID
POST  /api/v1/drafts                        ← create a new draft
GET   /api/v1/drafts/{id}/prepublish        ← pre-publish validation check
POST  /api/v1/drafts/{id}/publish           ← publish the draft
POST  /api/v1/image                         ← upload image, returns CDN URL
```

---

#### The 3-Step Publish Flow

Publishing a post requires these 3 API calls in exact order:

```
1. POST /api/v1/drafts
   Body: { draft_title, draft_subtitle, draft_body (ProseMirror JSON), draft_bylines, audience }
   Response: { id: "12345" }

2. GET /api/v1/drafts/12345/prepublish
   Substack's own editor always calls this before enabling the Publish button.
   It validates audience settings, section assignment, etc.
   ⚠ Skipping this step causes the publish call to fail with a 400.

3. POST /api/v1/drafts/12345/publish
   Body: { send: false, share_automatically: false }
   Response: { slug: "post-title", canonical_url: "https://..." }
```

The `prepublish` step was not obvious — there is no documentation for it anywhere. We only discovered it by watching the network tab during a manual publish. Without it, the publish silently fails or returns incorrect settings.

---

#### The `draft_body` Format — ProseMirror JSON

The single most important implementation detail: `draft_body` is **not HTML**. It is a stringified ProseMirror document (Substack's editor is built on TipTap, which uses the ProseMirror schema).

```json
{
  "type": "doc",
  "content": [
    {
      "type": "heading",
      "attrs": { "level": 2 },
      "content": [{ "type": "text", "text": "Section Title" }]
    },
    {
      "type": "paragraph",
      "content": [{ "type": "text", "text": "Paragraph content here." }]
    },
    {
      "type": "paragraph",
      "content": [
        {
          "type": "image",
          "attrs": { "src": "https://substackcdn.com/...", "alt": null, "title": null }
        }
      ]
    }
  ]
}
```

This entire object is then `json.dumps()`'d and sent as the string value of `draft_body` in the POST body — it is stringified JSON inside a JSON object.

---

#### Authentication — Why the Raw Cookie Header

Two session cookies are required:

- `substack.sid` — main session, URL-encoded Connect.js session ID, format: `s%3A<id>.<signature>`
- `substack.lli` — "likely logged in" JWT, short-lived presence indicator

**The problem with using `requests`' cookie jar:**
`substack.sid` is already URL-encoded (contains `%3A`, `%2F`, etc.). The `requests` library's cookie jar decodes values before sending and then re-encodes them, which causes double-encoding — `%3A` becomes `%253A` — and Substack rejects the session as invalid.

**Fix:** Send as a raw `Cookie` header, bypassing the cookie jar entirely:

```python
self.session.headers.update({
    "Cookie": f"substack.sid={config.SUBSTACK_SID}; substack.lli={config.SUBSTACK_LLI}"
})
```

This sends the cookies exactly as copied from the browser — no re-encoding.

---

#### Image Pipeline — Three Problems, Three Fixes

Getting images from a LinkedIn post into a published Substack post required solving three separate problems in sequence.

**Problem 1 — LinkedIn CDN blocks direct access**

LinkedIn's CDN serves images with authentication. Simply putting a LinkedIn image URL into a Substack post body would mean Substack's servers (and readers' browsers) would get a 403 when trying to load it.

**Fix:** Download each image locally first, then re-upload to Substack's own CDN. The download requires a browser-like `Referer: https://www.linkedin.com/` header — without it, LinkedIn's CDN returns an HTML redirect instead of the image bytes.

---

**Problem 2 — Substack's image upload rejects standard multipart**

The first implementation used `requests`' standard file upload:

```python
# WRONG — what we tried first:
resp = session.post("/api/v1/image", files={"image": open(path, "rb")})
# → 400 {"errors":[{"param":"image","msg":"Invalid value"}]}
```

This returned `400 Invalid value` every time, with no explanation of what format was expected. We tried different field names, MIME types, and filenames — all 400.

After ruling out session issues and endpoint problems, we read the source code of the open-source `ma2za/python-substack` library on GitHub and found the actual format:

```python
# CORRECT — what Substack actually expects:
encoded = base64.b64encode(open(path, "rb").read())
data_uri = b"data:image/jpeg;base64," + encoded
resp = session.post("/api/v1/image", data={"image": data_uri})
# → 200 {"url": "https://bucketeer-....s3.amazonaws.com/..."}
```

Three things were wrong simultaneously: the encoding (raw binary vs. base64), the content type (multipart/form-data vs. form-urlencoded), and the value format (file object vs. data URI string). Substack's error message gives no hint about any of this.

---

**Problem 3 — Images stored in the draft but not rendering in the published post**

After fixing the upload, images appeared in the Substack editor draft when we opened it manually — but were completely absent from the published post HTML.

The first implementation used the `captionedImage` node type, which is what Substack's TipTap editor uses internally:

```json
{ "type": "captionedImage", "attrs": { "src": "...", "imageSize": "normal", ... } }
```

This node type is stored in the draft JSON correctly. But Substack's **server-side HTML renderer** — which runs when the post is published and generates the public-facing page — ignores `captionedImage` entirely. We confirmed this by fetching the published post's HTML and finding zero `captionedImage` references.

**Fix:** Use a standard `image` node inside a `paragraph` wrapper:

```json
{
  "type": "paragraph",
  "content": [
    { "type": "image", "attrs": { "src": "https://substackcdn.com/...", "alt": null } }
  ]
}
```

This is the node type Substack's renderer handles. Once switched, images rendered correctly in published posts.

---

**Problem 4 — Raw S3 URLs don't render**

The upload response returns a raw S3 URL: `https://bucketeer-....s3.amazonaws.com/...`

Using this URL directly in the post body resulted in broken images for readers (S3 bucket access restrictions). Substack's renderer expects CDN proxy URLs:

```python
def _to_cdn_url(s3_url):
    encoded = urllib.parse.quote(s3_url, safe="")
    return f"https://substackcdn.com/image/fetch/w_1456,c_limit,f_auto,q_auto:good,fl_progressive:steep/{encoded}"
```

The CDN URL wraps the S3 URL as a URL-encoded path parameter, and Substack's CDN proxy serves it with correct caching, resizing, and access control.

---

#### Rate Limiting Strategy

Every Substack API call is preceded by `time.sleep(1)`. This is deliberate:

- Community testing suggests Substack throttles aggressive clients at ~1 req/sec
- Our workflow makes 5 calls per publish: `get_user_id → create_draft → prepublish → publish` + 1 per image
- At 1s apart, the full publish takes ~5–6 seconds — imperceptible to the user
- A single human-triggered publish cannot be mistaken for bulk automation

---

#### Why Not Use the `python-substack` Library

The `ma2za/python-substack` library was evaluated. We chose direct HTTP calls because:

- The library uses the correct image upload format (this is how we discovered it) but its draft/publish flow doesn't match our ProseMirror body structure needs
- It requires Google OAuth handling for accounts that log in with Google (no password available)
- Direct HTTP gives full control over every header, cookie, and body field
- Fewer moving parts = faster debugging when something breaks (and a lot broke)

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
2. Upload to Substack's CDN via `POST /api/v1/image` → returns a raw S3 URL
3. Wrap the S3 URL into a `substackcdn.com/image/fetch/...` CDN proxy URL
4. Inject the CDN URL into the ProseMirror document as an `image` node inside a `paragraph`
5. Delete the temp file

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

### Content Limitations

| Limitation | Detail |
|---|---|
| **No video support** | LinkedIn video posts cannot be included. Videos are not downloadable from LinkedIn's CDN without authentication, and Substack's internal API has no video upload endpoint accessible via this method. Video content in a LinkedIn post is silently skipped. |
| **No carousel / document posts** | LinkedIn carousel posts (multi-slide PDFs) and document posts are not supported. The scraper extracts text from the post caption only — the slide content is not accessible in the page DOM without additional interaction. |
| **No image captions** | Images are embedded in the published post without captions. Substack's `captionedImage` node (the only node that supports captions) is ignored by the server-side renderer. The `image` node we use does not accept a caption field. |
| **Images only from LinkedIn CDN** | The scraper only collects images hosted on `media.licdn.com`. Externally linked images (e.g. images in a post that link to another site) are not captured. |
| **No GIF animation** | Animated GIFs lose their animation. The image is downloaded as a static file and re-uploaded to Substack CDN, which may convert it to a static WebP. |
| **No polls or reactions data** | LinkedIn poll results, emoji reactions, and engagement metrics are stripped from the post text by the scraper's noise filter. |

### Publishing Limitations

| Limitation | Detail |
|---|---|
| **Publishes immediately** | There is no scheduling support. Clicking Confirm & Publish makes the post live immediately. Substack's internal API has a schedule field but it is not wired up in this tool. |
| **Always published to everyone** | Posts are published with `audience: "everyone"`. Paid-subscriber-only posts are not supported. |
| **No email send** | `send: false` is hardcoded in the publish call. The post goes live on the Substack web page but is not sent as an email newsletter to subscribers. This is intentional to avoid spamming subscribers during testing. |
| **One post at a time** | The pipeline processes one LinkedIn post per session. There is no batch or queue mode. |
| **No draft persistence** | Generated content lives in Streamlit's in-memory session state. Closing the browser tab or refreshing the page loses all work. There is no save-draft-and-come-back flow. |

### Credential and Session Limitations

| Limitation | Detail |
|---|---|
| **LinkedIn session expires** | `session.json` typically expires in ~30 days. When it does, the scraper raises `ScraperError` and the UI falls back to manual paste. Re-run `setup_session.py` to refresh. |
| **LinkedIn DOM changes** | LinkedIn changes their CSS class names with frontend deployments. The scraper tries 6 selectors in order and falls back to `<main>` text extraction. If all fail, manual paste is always available. |
| **Substack cookies expire** | `substack.sid` and `substack.lli` expire in ~30 days. Refresh them by copying new values from browser DevTools → Application → Cookies → substack.com. |
| **Substack API is undocumented** | The internal `/api/v1` endpoints are not public, not versioned, and can change without notice. Any Substack frontend update could break the publish flow. |
| **Scraping respects account visibility** | The scraper only sees posts that your logged-in LinkedIn account can see. Posts from accounts you don't follow (if set to followers-only) will load a restricted view with no post text. |

### Infrastructure Limitations

| Limitation | Detail |
|---|---|
| **Windows only (as-is)** | The asyncio ProactorEventLoop fix in `linkedin_scraper.py` is Windows-specific. On Linux/macOS, this line is skipped and Playwright uses its default event loop — this should work fine but has not been tested. |
| **Localhost only** | Playwright's visible browser (`headless=False`) requires a display. This prevents deployment to headless cloud environments (Streamlit Community Cloud, Heroku, etc.) without additional Xvfb configuration. |
| **Mistral free tier rate limits** | Mistral's free tier is rate-limited. Rapid successive generation/regeneration calls may return `503 unreachable_backend`. Wait a few seconds and retry. |

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
