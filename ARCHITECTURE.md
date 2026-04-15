# Architecture Deep Dive

This document explains the internal design of the LinkedIn → Substack Agent in detail — the data flow, each component's responsibilities, the key decisions made at each layer, and the reasoning behind them.

---

## Agent Workflow

```
        USER                        AGENT                          TOOLS
          │                           │                              │
          │  LinkedIn URL             │                              │
          │  (or pasted text)         │                              │
          ├──────────────────────────►│                              │
          │                           │── scrape_post(url) ────────► │Playwright
          │                           │◄── { text, images[] } ────── │+ Chromium
          │                           │                              │
          │                           │── rewrite(text, tone) ─────► │Mistral API
          │                           │◄── { title, subtitle,        │(JSON mode)
          │                           │      sections[] } ───────────│
          │                           │                              │
          │◄── generated draft ───────│                              │
          │                           │                              │
    ╔═════╧══════════════════╗        │                              │
    ║   HUMAN IN THE LOOP    ║        │                              │
    ║  ──────────────────    ║        │                              │
    ║  • edit title /        ║        │                              │
    ║    subtitle / body     ║        │                              │
    ║  • live preview        ║        │                              │
    ║  • regenerate with     ║────────┼─── rewrite() ──────────────► │Mistral API
    ║    new instructions    ║        │◄───────────────────────── ───│ (if regen)
    ║  • explicit approve ✓  ║        │                              |
    ╚═════╤══════════════════╝        │                              │
          │                           │                              │
          │  "Approve & Publish"      │                              │
          ├──────────────────────────►│                              │
          │                           │── download(img_url) ───────► │LinkedIn CDN
          │                           │◄── tmp_file ──────────────── │
          │                           │── upload_image(tmp) ───────► │Substack CDN
          │                           │◄── cdn_url ───────────────── │
          │                           │                              │
          │                           │── POST /api/v1/drafts ─────► │
          │                           │── GET  prepublish ─────────► │Substack
          │                           │── POST publish ────────────► │internal API
          │                           │◄── canonical_url ─────────── │
          │                           │                              │
          │                           │── send_confirmation() ─────► │Gmail SMTP
          │◄── live Substack URL ─────│                              │
          │                           │                              │
```

**Key design principle:** The agent acts, then stops. It does not publish autonomously. The human reviews and explicitly approves before any external state changes — no post is created, no image is uploaded, nothing touches Substack until Step 4.

---

## System Overview

The application is a 5-step pipeline with a deliberate pause at step 3 for human review. Each step produces output that feeds the next. The UI layer is thin — all business logic lives in the agents and tools.

```
User Input (LinkedIn post text + image URLs)
        │
        ▼
[ ContentAgent ] ──── Mistral API ────► structured JSON
        │                                (title, subtitle, sections[])
        ▼
[ HITL Review ] ◄──── user edits/approves
        │
        ▼
[ ImageHandler ] ──── download images ──► temp files
        │
        ▼
[ SubstackClient ] ── upload images ───► Substack CDN URLs
        │              create draft
        │              prepublish
        │              publish
        ▼
[ EmailSender ] ────── Gmail SMTP ─────► confirmation email
        │
        ▼
Live Substack URL returned to user
```

---

## State Machine & Data Flow

> **Note:** This app does not use LangGraph or any agent orchestration framework. The "agentic" flow is implemented as a hand-rolled Streamlit state machine controlled by `st.session_state.step` (integer 1–5). Each step is a discrete render function; transitions happen via `go_to(n)` + `st.rerun()`.

---

### Step Transition Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│  st.session_state.step transitions — all navigation is explicit      │
└──────────────────────────────────────────────────────────────────────┘

  ┌───────────────────────────┐
  │      STEP 1: INGEST       │ ◄─────────────────────────── "Start Over"
  │  • URL scrape (optional)  │                                   │
  │  • Manual paste fallback  │                                   │
  │  Writes: linkedin_text,   │                              (resets all
  │          image_urls       │                            session_state)
  └─────────────┬─────────────┘
                │ "Next →"
                ▼
  ┌───────────────────────────┐
  │    STEP 2: CONFIGURE      │ ◄── "← Back" ──┐
  │  • Choose tone            │                │
  │  • Optional edit instrs.  │                │
  │  • Calls ContentAgent     │                │
  │  Writes: tone,            │                │
  │    generated_title,       │                │
  │    generated_subtitle,    │                │
  │    generated_sections     │                │
  └─────────────┬─────────────┘                │
                │ "Generate Draft →"            │
                ▼                               │
  ┌───────────────────────────┐                 │
  │   STEP 3: REVIEW (HITL)   │ ◄──── "← Back" ─┤
  │  • Edit title/subtitle    │                 │
  │  • Edit body text         │◄──┐             │
  │  • Live preview (iframe)  │   │ "Regenerate"│
  │  • Optional Regen call    │───┘ (self-loop) │
  │  Writes: generated_*      │                 │
  └─────────────┬─────────────┘                 │
                │ "Approve & Publish →"          │
                ▼                               │
  ┌───────────────────────────┐                 │
  │    STEP 4: PUBLISH        │ ◄── "← Back" ──┘
  │  • Confirm email address  │
  │  • ImageHandler: download │
  │  • SubstackClient: upload,│
  │    draft, prepublish,     │
  │    publish                │
  │  • EmailSender: confirm   │
  │  Writes: published_url    │
  └─────────────┬─────────────┘
                │ "Confirm & Publish →"
                ▼
  ┌───────────────────────────┐
  │      STEP 5: DONE         │
  │  • Show live URL          │
  │  • Email sent             │
  └─────────────┬─────────────┘
                │ "Start Over"
                └──────────────► reset all state → Step 1
```

---

### Session State Keys Per Step

| Key | Written by | Read by |
|---|---|---|
| `linkedin_text` | Step 1 | Step 2 (→ ContentAgent), Step 3 (Regen) |
| `image_urls` | Step 1 | Step 3 (preview), Step 4 (image transfer) |
| `tone` | Step 2 | Step 3 (Regen default) |
| `edit_mode` / `edit_instructions` | Step 2 | Step 3 (Regen) |
| `generated_title` | Step 2, Step 3 | Step 3 (edit field), Step 4 (publish) |
| `generated_subtitle` | Step 2, Step 3 | Step 3 (edit field), Step 4 (publish) |
| `generated_sections` | Step 2, Step 3 | Step 3 (edit area), Step 4 (publish) |
| `preview_image_data_uris` | Step 3 (`_image_data_uri` cache) | Step 3 (iframe preview) |
| `user_email` | Step 4 | Step 5 (display) |
| `published_url` | Step 4 | Step 5 (display) |
| `publish_error` | Step 4 | Step 4 (error display) |

---

### Full Data Flow Diagram

```
LinkedIn Post URL (optional)
         │
         ▼
  ┌──────────────────┐  scrape_post(url)   ┌─────────────────────┐
  │ linkedin_scraper │────────────────────►│  Chromium (headless  │
  │                  │◄─{text, images[]}───│  =False, Playwright) │
  └────────┬─────────┘                     └─────────────────────┘
           │ or manual paste
           │
           ▼
   linkedin_text (str)
   image_urls (str, newline-sep)
           │
           │ Step 2: ContentAgent.rewrite()
           ▼
  ┌──────────────────┐  prompt (text+tone) ┌─────────────────────┐
  │  ContentAgent    │────────────────────►│   Mistral API        │
  │                  │◄─JSON {title,       │   (mistral-small-    │
  └────────┬─────────┘   subtitle,         │    latest,           │
           │             sections[]}       │    json_object mode) │
           │                               └─────────────────────┘
           ▼
   generated_title (str)
   generated_subtitle (str)
   generated_sections (list[dict])
           │
           │  ◄──────────── HUMAN EDITS HERE (Step 3) ───────────────►
           │  title/subtitle/body editable in text inputs + text_area
           │  sections ↔ plain text serialised via sections_to_text()
           │                          / text_to_sections()
           │
           ├──► Step 3 preview: image_urls
           │          │
           │          ▼
           │   ┌──────────────────┐  GET (Referer: linkedin.com)  ┌──────────────┐
           │   │  ImageHandler    │──────────────────────────────►│  LinkedIn CDN │
           │   │  .download()     │◄── raw bytes ─────────────────│              │
           │   └────────┬─────────┘                               └──────────────┘
           │            │ base64 data URI → cached in preview_image_data_uris
           │            ▼
           │   <img src="data:image/jpeg;base64,..."/>  (embedded in iframe)
           │
           │ Step 4: publish pipeline
           ▼
  ┌──────────────────┐  GET (Referer: linkedin.com)  ┌──────────────┐
  │  ImageHandler    │──────────────────────────────►│  LinkedIn CDN│
  │  .download()     │◄── raw bytes → tmp file ──────│              │
  └────────┬─────────┘                               └──────────────┘
           │ tmp file path
           ▼
  ┌──────────────────┐  POST /api/v1/image            ┌──────────────┐
  │  SubstackClient  │  data={image: base64 data URI}►│  Substack    │
  │  .upload_image() │◄── {url: s3_url} ──────────────│  CDN (S3)    │
  └────────┬─────────┘                                └──────────────┘
           │ cdn_url = substackcdn.com/image/fetch/.../{encoded_s3_url}
           │
           ▼
  ┌──────────────────┐  POST /api/v1/drafts            ┌──────────────┐
  │  SubstackClient  │  {title, subtitle,              │  Substack    │
  │  ._create_draft()│  draft_body: ProseMirror JSON}►│  private API │
  │  ._prepublish()  │  GET  /api/v1/drafts/{id}/      │              │
  │  ._publish_draft │       prepublish                │              │
  │                  │  POST /api/v1/drafts/{id}/      │              │
  │                  │       publish                  ►│              │
  └────────┬─────────┘◄── {slug, canonical_url} ───────└──────────────┘
           │
           │ published_url
           ▼
  ┌──────────────────┐  SMTP SSL port 465              ┌──────────────┐
  │  EmailSender     │────────────────────────────────►│  Gmail SMTP  │
  │  .send_          │  HTML + plain-text multipart    │              │
  │  confirmation()  │                                 └──────────────┘
  └──────────────────┘
```

---

## Component Breakdown

### `app.py` — UI State Machine

The Streamlit app is structured as an explicit state machine using `st.session_state.step` (integer 1–5). Each step is a separate render function. State transitions happen via `go_to(n)` followed by `st.rerun()`.

```
step 1: step_ingest()     — collect linkedin_text, image_urls
step 2: step_configure()  — collect tone, edit_instructions
step 3: step_review()     — HITL: edit title/subtitle/body, preview, approve
step 4: step_publish()    — confirm email, trigger publish pipeline
step 5: step_done()       — show live URL, send email
```

**Why explicit state over Streamlit's tab/page routing:**
Tabs don't enforce linear progression. A user could jump to "Publish" before generating content. The step integer enforces the correct order and makes the progress bar trivial to implement.

**Session state keys:**
```python
step                  # int: current step
linkedin_text         # str: raw pasted post content
image_urls            # str: newline-separated image URLs
tone                  # str: selected tone
edit_mode             # bool: whether edit instructions are active
edit_instructions     # str: free-text instructions to the agent
generated_title       # str: editable after generation
generated_subtitle    # str: editable after generation
generated_sections    # list[dict]: editable via text_area round-trip
published_url         # str: live post URL after publish
user_email            # str: confirmation email recipient
publish_error         # str: last publish error message
```

**The preview rendering decision:**
The preview uses `st.components.v1.html(preview_html, height=520, scrolling=True)` rather than `st.markdown(unsafe_allow_html=True)`.

`st.markdown` with `unsafe_allow_html` renders HTML via Streamlit's markdown processor, which does not reliably support `overflow-y: auto` or scrollable divs inside column layouts. It also processes markdown syntax inside the HTML, which can corrupt content containing asterisks or underscores.

`st.components.v1.html()` renders in a sandboxed iframe with full CSS support. Scrolling works correctly and content is isolated from Streamlit's styling. All content is passed through `html.escape()` before insertion to prevent any XSS or rendering artifacts from smart quotes, em-dashes, or angle brackets in LLM output.

**The edit/preview round-trip:**
Generated sections are serialized to plain text for editing:
```
## Heading         →  {"type": "heading", "level": 2, "content": "Heading"}
Paragraph text     →  {"type": "paragraph", "content": "Paragraph text"}
```

The user edits the text freely. On preview and on publish, `text_to_sections()` re-parses the text back to the sections list. This approach was chosen over a rich text editor (Quill, ProseMirror in browser) because:
- No additional JavaScript dependencies
- The plain text format is immediately understandable
- Users familiar with markdown naturally use `##` for headings

---

### `agents/content_agent.py` — The Rewriting Agent

Responsibility: take raw LinkedIn post text and return a structured Substack article.

**Prompt architecture:**
The prompt has two layers:
1. `SYSTEM_PROMPT` — invariant instructions: output format (JSON), structure requirements, word count target, what to remove (hashtags, @mentions, LinkedIn CTAs)
2. Tone modifier — appended per-request based on user selection

Five tones are supported, each with distinct stylistic guidance:
- **Professional** — formal, authoritative, industry language
- **Conversational** — warm, first-person, short sentences, no jargon
- **Analytical** — data-driven, frameworks, cause-effect reasoning
- **Storytelling** — narrative hook, tension, concrete details
- **Educational** — first principles, analogies, key takeaways

**Why JSON output format:**
`response_format={"type": "json_object"}` instructs Mistral to guarantee valid JSON output. Without this, the model sometimes wraps output in markdown code fences (` ```json `) which requires fragile string parsing. With JSON mode, `json.loads(response)` always succeeds on a valid response.

**Output structure:**
```json
{
  "title": "string",
  "subtitle": "string",
  "sections": [
    {"type": "paragraph", "content": "string"},
    {"type": "heading", "level": 2, "content": "string"}
  ]
}
```

This structure was chosen because it maps 1:1 to Substack's ProseMirror document format (see SubstackClient below), eliminating any conversion ambiguity. Plain text in `content` fields (no markdown) avoids double-processing issues.

**Edit instructions injection:**
When the user provides edit instructions in the HITL step and clicks Regenerate, the instructions are appended to the user message as a final paragraph:
```
Additional instructions from the author: make the intro shorter, emphasise the ROI angle
```
This keeps the system prompt stable and makes the edit instructions clearly attributable in the conversation context.

**Token usage:**
- Typical prompt: ~400-600 tokens
- Typical completion: ~600-800 tokens
- `finish_reason: stop` confirmed — no truncation on free tier
- No explicit `max_tokens` needed; Mistral's default handles the target length

---

### `tools/substack_client.py` — The Hard Part

This is the most technically interesting component. Substack has no public API. Here is the complete reverse-engineering journey and implementation.

#### How We Found the Endpoints

1. Logged into Substack in Chrome, opened the post editor
2. DevTools → Network → filtered XHR/Fetch requests by `/api/v1`
3. Created a draft, added content, uploaded an image, and published
4. Captured every request: URL, method, headers, request body, response body
5. Identified the 6 endpoints needed for our workflow

#### The 3-Step Publish Flow

Publishing a Substack post requires exactly 3 API calls in sequence:

```
1. POST /api/v1/drafts          → creates draft, returns {id: "..."}
2. GET  /api/v1/drafts/{id}/prepublish  → validation check (audience, section, etc.)
3. POST /api/v1/drafts/{id}/publish    → publishes, returns {slug: "...", canonical_url: "..."}
```

Step 2 (`prepublish`) is not obvious and is not documented anywhere. We discovered it by observing the network tab — Substack's editor always calls this endpoint before the publish button becomes active. Skipping it causes the publish call to return a 400 or to publish with incorrect settings. Including it ensures the publish behaves identically to a manual browser publish.

#### The `draft_body` Format — ProseMirror JSON

The single most important implementation detail: `draft_body` is **not HTML**. It is a stringified ProseMirror document:

```python
body = {
    "type": "doc",
    "content": [
        {
            "type": "heading",
            "attrs": {"level": 2},
            "content": [{"type": "text", "text": "Section Title"}]
        },
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": "Paragraph content here."}]
        },
        {
            "type": "paragraph",
            "content": [
                {
                    "type": "image",
                    "attrs": {
                        "src": "https://substackcdn.com/image/fetch/.../{encoded_s3}",
                        "alt": None,
                        "title": None,
                    }
                }
            ]
        }
        # NOTE: captionedImage is Substack's editor-only node type.
        # It is stored in the draft but ignored by the server-side HTML renderer.
        # Only "image" inside a "paragraph" renders in the published post.
    ]
}

draft_body_field = json.dumps(body)  # stringified, not nested JSON
```

This was discovered by inspecting the request payload for a real Substack editor save. The format mirrors the open-source ProseMirror/TipTap document model that Substack's editor (TipTap-based) uses internally.

#### Authentication

Substack uses two session cookies:
- `substack.sid` — main session cookie, URL-encoded Connect.js session ID, format: `s%3A<id>.<signature>`
- `substack.lli` — "likely logged in" indicator, a short-lived JWT

Both cookies are passed as a raw `Cookie` header rather than via `requests`' cookie jar:
```python
self.session.headers.update({
    "Cookie": f"substack.sid={config.SUBSTACK_SID}; substack.lli={config.SUBSTACK_LLI}"
})
```

**Why raw header instead of cookie jar:**
The `substack.sid` value is URL-encoded (contains `%3A`, `%2F`, etc.). The `requests` cookie jar attempts to decode and re-encode cookie values, which causes double-encoding of the already-encoded SID. Passing it as a raw `Cookie` header sends exactly what the browser sends, bypassing this issue.

#### Rate Limiting

```python
time.sleep(1)  # before every API call
```

This 1-second delay between calls is deliberate and conservative:
- Community testing on Substack's API suggests they throttle aggressive clients at approximately 1 req/sec
- Our publish workflow makes 5 API calls total: get_user_id, create_draft, prepublish, publish, plus 1 per image
- At 1 second apart, the entire publish takes ~5-6 seconds — imperceptible for a human workflow
- This pattern ensures the tool could never be misidentified as a bulk automation bot

#### Image Upload

```python
# Upload local file to Substack CDN — base64 form-encoded, NOT multipart
with open(image_path, "rb") as f:
    raw_bytes = f.read()
encoded = base64.b64encode(raw_bytes)
data_uri = b"data:" + mime_type.encode() + b";base64," + encoded
resp = self.session.post(f"{self.base_url}/api/v1/image", data={"image": data_uri})
s3_url = resp.json()["url"]
cdn_url = _to_cdn_url(s3_url)  # → https://substackcdn.com/image/fetch/.../{encoded_s3}
```

**Critical format detail:** Substack's image endpoint expects `application/x-www-form-urlencoded` with the image as a base64 data URI string — NOT `multipart/form-data`. Using `files={"image": f}` (the natural `requests` approach) returns a `400 Invalid value`. This was discovered by reading the source code of the open-source `python-substack` library.

After upload, the raw S3 URL must be wrapped in a Substack CDN proxy URL for Substack's renderer to serve it correctly:
```python
def _to_cdn_url(s3_url: str) -> str:
    encoded = quote(s3_url, safe="")
    return f"https://substackcdn.com/image/fetch/w_1456,c_limit,f_auto,q_auto:good,fl_progressive:steep/{encoded}"
```

Images that fail to upload are skipped with a warning. Publishing is not blocked by image failures.

---

### `tools/image_handler.py` — Image Transfer Bridge

LinkedIn images cannot be directly referenced in Substack posts. CDN cross-origin restrictions and LinkedIn's auth requirements mean external image URLs from LinkedIn would fail to load for Substack readers.

The solution: download each image to a local temp file → upload to Substack CDN → use the Substack CDN URL in the post body.

```python
tmp_path = image_handler.download(linkedin_url)   # → /tmp/abc123.jpg
cdn_url  = substack.upload_image(tmp_path)         # → substackcdn.com/...
image_handler.cleanup(tmp_path)                    # → delete temp file
```

Content-type detection from the HTTP `Content-Type` response header determines the file extension. Falls back to `.jpg` for unknown types.

---

### `tools/email_sender.py` — Confirmation Email

Sends an HTML + plain-text multipart email via Gmail SMTP (SSL, port 465) after successful publishing.

The email is non-blocking relative to the publish step — if sending fails, the app shows a warning but does not roll back the published post. The post is already live regardless of email delivery.

**MIME structure:**
```
multipart/alternative
├── text/plain  — fallback for clients that don't render HTML
└── text/html   — styled email with Substack's orange (#FF6719) branding
```

Port 465 (SMTP_SSL) was chosen over port 587 (STARTTLS) because it establishes SSL from the connection start, which is more reliable across different network configurations and does not require explicit `server.starttls()` calls.

---

### `prompts/` — Prompt Architecture

**`system_prompts.py`:**
The system prompt is invariant across all requests. It specifies:
- The task (transform LinkedIn post to Substack article)
- Output format (JSON, exact schema)
- Content rules (expand to 600-1000 words, remove hashtags/mentions, no invented facts)
- Formatting rules (plain text in content fields, no markdown symbols)

Keeping the output format specification in the system prompt (not the user message) ensures it persists across regeneration calls with different instructions.

**`tone_modifiers.py`:**
Per-tone guidance is a single paragraph injected into the user message. It describes voice, sentence style, and structural approach for each tone. This is kept separate from the system prompt so it can be changed without affecting the base output schema.

---

## Data Flow Through the HITL Step

This is where the agent hands control to the human.

```
generated_sections (list[dict])
        │
        ▼  sections_to_text()
plain text in text_area
  "## Heading\n\nParagraph text\n\n..."
        │
        ▼  user edits freely
modified text in text_area
        │
        ▼  text_to_sections()
edited_sections (list[dict])
        │
        ├──► components.html()     ← live preview (iframe)
        │
        └──► SubstackClient.publish()  ← on "Approve & Publish"
```

The round-trip `sections_to_text → edit → text_to_sections` is the key mechanism that makes the HITL step work. The user sees and edits plain text; the system works with structured data. The serialization format (markdown-ish headings with `##`) is intuitive for non-technical users and produces clean diffs when content changes.

---

---

### `tools/linkedin_scraper.py` + `setup_session.py` — LinkedIn Post Ingestion

#### The Scraping Problem

LinkedIn's pages are JavaScript-rendered React SPAs. A plain `requests.get()` returns a bare HTML shell with no post content — the actual content is loaded asynchronously by JavaScript after the page mounts. This rules out requests + BeautifulSoup entirely.

Even with a valid session cookie, LinkedIn's servers reject non-browser clients via:
- User-Agent detection (any `python-requests/*` or `python-playwright/*` UA gets blocked)
- Missing browser fingerprint headers (`sec-ch-ua`, `sec-fetch-*`, `accept-encoding` profiles)
- The presence of `navigator.webdriver = true` in JavaScript context (automation flag)
- Headless-mode rendering artifacts detectable via canvas fingerprinting

#### The Solution: Playwright with Saved Session

Playwright launches a real Chromium browser binary — the same rendering engine as Google Chrome. Combined with a real saved session from a manual login, the browsing session is indistinguishable from a normal returning user.

**`setup_session.py` — one-time setup:**
```python
# Opens a visible browser, user logs in manually
page.goto("https://www.linkedin.com/login")
page.wait_for_url("**/feed/**", timeout=180_000)  # waits for successful login
context.storage_state(path="session.json")  # saves cookies + localStorage
```

`storage_state` captures the full browser state: all cookies (including `li_at`, `JSESSIONID`, `bcookie`, etc.), localStorage, and sessionStorage. This is exactly what the browser sends on every subsequent request.

**`scrape_post()` — per-scrape flow:**
```python
context = browser.new_context(
    storage_state="session.json",      # loads saved session
    viewport={"width": 1280, "height": 800},
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...",
)
page.add_init_script(
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
)
page.goto(url, wait_until="domcontentloaded")
_human_delay(1.5, 2.5)              # random sleep: 1.5–2.5s
page.mouse.wheel(0, 300)            # scroll to trigger lazy-load
```

#### Anti-Detection Measures

| Measure | Implementation | Why |
|---|---|---|
| Real browser engine | Playwright + Chromium | Not detectable as a bot at the browser level |
| Visible window | `headless=False` | Headless browsers have detectable rendering differences |
| `AutomationControlled` disabled | `--disable-blink-features=AutomationControlled` | Removes the Chrome automation flag |
| webdriver flag removed | `add_init_script` at page level | Belt-and-suspenders: clears `navigator.webdriver` from JS context |
| Real User-Agent | Chrome/120 on Windows 10 | Matches what the saved session was created with |
| Human delay | `random.uniform(1.2, 2.4)` seconds | Avoids pattern-detectable instant DOM queries |
| Scroll event | `page.mouse.wheel(0, 300)` | Triggers lazy-loaded content, also mimics human scroll |
| No programmatic login | Saved session only | Login automation is the most detectable pattern; we skip it |
| Single post per call | One URL per button click | Clearly not a bulk scraper; no rate-limit risk |

#### CSS Selector Fallback Strategy

LinkedIn changes their CSS class names with frontend deploys. The scraper tries 5 selectors in order:
```python
POST_TEXT_SELECTORS = [
    ".update-components-text",
    ".feed-shared-update-v2__description",
    ".feed-shared-text",
    ".feed-shared-text-view",
    "[data-test-id='main-feed-activity-card__commentary']",
    "article .break-words",
]
```

If none match, it falls back to `page.locator("main").inner_text()` — the full main content area. If that also fails, `ScraperError` is raised and the UI falls back to manual paste seamlessly.

#### Error Handling and Fallback

`ScraperError` is raised for:
- `session.json` not found (setup not run yet)
- LinkedIn redirects to `/login` or `/checkpoint` (session expired)
- Page load timeout (network issues)
- No post text found (DOM layout change)
- Any unexpected exception wraps to `ScraperError`

The UI catches `ScraperError` and `Exception` separately and shows a warning, leaving the manual paste path fully functional. The scraper is an enhancement, not a dependency — the app works correctly without it.

---

## Why This Stack vs. Alternatives

| Decision | Chosen | Rejected | Reason |
|---|---|---|---|
| UI | Streamlit | Gradio | Streamlit session_state is designed for multi-step flows; Gradio is input/output-oriented |
| LLM | Mistral free tier | Claude, GPT-4 | Free tier available; quality sufficient for the task |
| Output format | JSON (`json_object` mode) | Markdown, HTML | Guaranteed parseable; maps directly to Substack's format |
| Substack | Reverse-engineered REST | python-substack lib, Playwright | Direct HTTP = full control; Playwright = heavy dependency |
| LinkedIn scraping | Playwright + saved session | requests, joeyism library, Selenium | Only approach that works on JS-rendered pages with bot detection |
| LinkedIn auth | Saved storage_state | Programmatic login, `li_at` cookie only | Programmatic login is most detectable; storage_state includes all auth state |
| Substack auth | Cookie header | Library auth, email+password | No password (Google OAuth); cookie header avoids encoding issues |
| Email | Gmail SMTP | SendGrid, Mailgun | No new service; 500/day free limit is sufficient |
| Image | Download + re-upload | Direct URL reference | LinkedIn CDN rejects external embedding |
| Preview | `components.html()` | `st.markdown(unsafe_allow_html)` | Iframe isolates CSS; scrolling works reliably |
