import base64
import html as html_lib
import mimetypes
import streamlit as st
import streamlit.components.v1 as components
from agents.content_agent import ContentAgent
from tools.substack_client import SubstackClient
from tools.image_handler import ImageHandler
from tools.email_sender import EmailSender
import config

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="LinkedIn → Substack Agent",
    page_icon="✍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Session state defaults ────────────────────────────────────────────────────
DEFAULTS = {
    "step": 1,
    "linkedin_text": "",
    "image_urls": "",
    "tone": "Professional",
    "edit_mode": False,
    "edit_instructions": "",
    "generated_title": "",
    "generated_subtitle": "",
    "generated_sections": [],
    "preview_image_data_uris": {},   # {url: "data:image/jpeg;base64,..."} cache
    "published_url": "",
    "user_email": "",
    "publish_error": "",
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ── Helpers ───────────────────────────────────────────────────────────────────
def go_to(step: int):
    st.session_state.step = step


def sections_to_text(sections: list) -> str:
    """Render sections as editable plain text (## prefix for headings)."""
    lines = []
    for s in sections:
        if s["type"] == "heading":
            prefix = "#" * s.get("level", 2)
            lines.append(f"{prefix} {s['content']}")
        else:
            lines.append(s["content"])
        lines.append("")
    return "\n".join(lines).strip()


def text_to_sections(text: str) -> list:
    """Parse edited plain text back into a sections list."""
    sections = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("### "):
            sections.append({"type": "heading", "level": 3, "content": line[4:]})
        elif line.startswith("## "):
            sections.append({"type": "heading", "level": 2, "content": line[3:]})
        elif line.startswith("# "):
            sections.append({"type": "heading", "level": 1, "content": line[2:]})
        else:
            sections.append({"type": "paragraph", "content": line})
    return sections


# ── Image preview helper ─────────────────────────────────────────────────────
def _image_data_uri(url: str) -> str | None:
    """
    Download a LinkedIn image server-side and return a base64 data URI.
    Results are cached in session state so re-renders don't re-download.
    Returns None if the download fails.
    """
    cache = st.session_state.preview_image_data_uris
    if url in cache:
        return cache[url]
    try:
        handler = ImageHandler()
        tmp_path = handler.download(url)
        mime, _ = mimetypes.guess_type(tmp_path)
        if not mime:
            mime = "image/jpeg"
        with open(tmp_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()
        handler.cleanup(tmp_path)
        data_uri = f"data:{mime};base64,{encoded}"
        cache[url] = data_uri
        return data_uri
    except Exception:
        return None


# ── Progress bar ──────────────────────────────────────────────────────────────
def show_progress():
    labels = ["Ingest", "Configure", "Review", "Publish", "Done"]
    cols = st.columns(5)
    for i, (col, label) in enumerate(zip(cols, labels), start=1):
        with col:
            if i < st.session_state.step:
                st.markdown(
                    f"<div style='text-align:center;color:#22c55e;font-weight:600;'>&#10003; {label}</div>",
                    unsafe_allow_html=True,
                )
            elif i == st.session_state.step:
                st.markdown(
                    f"<div style='text-align:center;color:#FF6719;font-weight:700;border-bottom:2px solid #FF6719;padding-bottom:4px;'>{label}</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"<div style='text-align:center;color:#aaa;'>{label}</div>",
                    unsafe_allow_html=True,
                )
    st.markdown("<br/>", unsafe_allow_html=True)


# ── STEP 1 — INGEST ───────────────────────────────────────────────────────────
def _session_exists() -> bool:
    from pathlib import Path
    return Path("session.json").exists()


def step_ingest():
    st.markdown("### Step 1 — Import LinkedIn Post")

    # ── URL scrape path ──────────────────────────────────────────────────────
    session_ready = _session_exists()

    if session_ready:
        st.caption("Session detected. Paste a LinkedIn post URL to auto-fill, or paste manually below.")
    else:
        st.caption(
            "No LinkedIn session found. "
            "Run `venv/Scripts/python setup_session.py` to enable URL scraping. "
            "Or paste manually below."
        )

    col_url, col_btn = st.columns([4, 1])
    with col_url:
        li_url = st.text_input(
            "LinkedIn Post URL",
            placeholder="https://www.linkedin.com/posts/...",
            disabled=not session_ready,
            key="_li_url_input",
        )
    with col_btn:
        st.markdown("<br/>", unsafe_allow_html=True)
        scrape_clicked = st.button(
            "Fetch",
            disabled=not session_ready or not li_url.strip(),
            help="Scrape post text and images automatically",
        )

    if scrape_clicked and li_url.strip():
        with st.spinner("Fetching post from LinkedIn..."):
            try:
                from tools.linkedin_scraper import scrape_post, ScraperError
                result = scrape_post(li_url.strip())
                st.session_state.linkedin_text = result["text"]
                st.session_state.image_urls = "\n".join(result["images"])
                st.success(
                    f"Fetched successfully — "
                    f"{len(result['images'])} image(s) found."
                )
            except ScraperError as e:
                st.warning(
                    f"Auto-fetch failed: {e}  \n"
                    "Please paste the post content manually below."
                )
            except Exception as e:
                st.warning(
                    f"Unexpected error during fetch: {e}  \n"
                    "Please paste the post content manually below."
                )

    st.divider()

    # ── Manual paste path (always available) ────────────────────────────────
    st.markdown("**Post Content**")
    linkedin_text = st.text_area(
        "Paste the full LinkedIn post text here",
        value=st.session_state.linkedin_text,
        height=220,
        placeholder="Paste the post text here...",
        label_visibility="collapsed",
    )

    image_urls = st.text_area(
        "Image URLs (one per line, optional)",
        value=st.session_state.image_urls,
        height=90,
        placeholder="https://media.licdn.com/dms/image/...",
    )

    st.markdown("<br/>", unsafe_allow_html=True)
    if st.button("Next →", type="primary", disabled=not linkedin_text.strip()):
        st.session_state.linkedin_text = linkedin_text.strip()
        st.session_state.image_urls = image_urls.strip()
        go_to(2)
        st.rerun()


# ── STEP 2 — CONFIGURE ───────────────────────────────────────────────────────
def step_configure():
    st.markdown("### Step 2 — Configure the Rewrite")

    col_left, col_right = st.columns(2)

    with col_left:
        tone = st.radio(
            "Select tone",
            ["Professional", "Conversational", "Analytical", "Storytelling", "Educational"],
            index=["Professional", "Conversational", "Analytical", "Storytelling", "Educational"].index(
                st.session_state.tone
            ),
        )

    with col_right:
        st.markdown("**Edit Instructions**")
        edit_mode = st.toggle("Give the agent specific instructions", value=st.session_state.edit_mode)
        edit_instructions = ""
        if edit_mode:
            edit_instructions = st.text_area(
                "What should the agent focus on or change?",
                value=st.session_state.edit_instructions,
                height=130,
                placeholder=(
                    "e.g. Make the intro more punchy, emphasise the business impact, "
                    "add a practical tips section, keep it under 800 words..."
                ),
            )

    st.markdown("<br/>", unsafe_allow_html=True)
    col_back, _, col_gen = st.columns([1, 2, 2])

    with col_back:
        if st.button("← Back"):
            go_to(1)
            st.rerun()

    with col_gen:
        if st.button("Generate Draft →", type="primary"):
            st.session_state.tone = tone
            st.session_state.edit_mode = edit_mode
            st.session_state.edit_instructions = edit_instructions

            with st.spinner("Generating your Substack article..."):
                try:
                    agent = ContentAgent()
                    result = agent.rewrite(
                        linkedin_text=st.session_state.linkedin_text,
                        tone=tone,
                        edit_instructions=edit_instructions if edit_mode else "",
                    )
                    st.session_state.generated_title = result.get("title", "")
                    st.session_state.generated_subtitle = result.get("subtitle", "")
                    st.session_state.generated_sections = result.get("sections", [])
                    go_to(3)
                    st.rerun()
                except Exception as e:
                    st.error(f"Generation failed: {e}")


# ── STEP 3 — REVIEW (HITL) ───────────────────────────────────────────────────
def step_review():
    st.markdown("### Step 3 — Review & Edit")
    st.caption("Edit the content directly or use Regenerate to try a different approach.")

    col_edit, col_preview = st.columns([1, 1], gap="large")

    with col_edit:
        st.markdown("**Edit Content**")
        title = st.text_input("Title", value=st.session_state.generated_title)
        subtitle = st.text_input("Subtitle", value=st.session_state.generated_subtitle)
        body_text = st.text_area(
            "Body  (use ## for section headings)",
            value=sections_to_text(st.session_state.generated_sections),
            height=380,
        )

        with st.expander("Regenerate with different instructions"):
            regen_tone = st.selectbox(
                "Tone",
                ["Professional", "Conversational", "Analytical", "Storytelling", "Educational"],
                index=["Professional", "Conversational", "Analytical", "Storytelling", "Educational"].index(
                    st.session_state.tone
                ),
            )
            regen_instructions = st.text_area(
                "New instructions",
                height=80,
                placeholder="e.g. Shorter intro, add a bullet-point summary at the end...",
            )
            if st.button("Regenerate"):
                with st.spinner("Regenerating..."):
                    try:
                        agent = ContentAgent()
                        result = agent.rewrite(
                            linkedin_text=st.session_state.linkedin_text,
                            tone=regen_tone,
                            edit_instructions=regen_instructions,
                        )
                        st.session_state.generated_title = result.get("title", "")
                        st.session_state.generated_subtitle = result.get("subtitle", "")
                        st.session_state.generated_sections = result.get("sections", [])
                        st.session_state.tone = regen_tone
                        st.rerun()
                    except Exception as e:
                        st.error(f"Regeneration failed: {e}")

    with col_preview:
        st.markdown("**Preview**")
        preview_sections = text_to_sections(body_text)
        body_html = ""
        for s in preview_sections:
            if s["type"] == "heading":
                lvl = s.get("level", 2)
                body_html += (
                    f"<h{lvl} style='font-family:Georgia,serif;margin-top:1.2rem;color:#1a1a1a;'>"
                    f"{html_lib.escape(s['content'])}</h{lvl}>"
                )
            else:
                body_html += (
                    f"<p style='font-family:Georgia,serif;line-height:1.75;"
                    f"margin-bottom:0.9rem;color:#333;'>"
                    f"{html_lib.escape(s['content'])}</p>"
                )

        # Embed images as base64 data URIs — downloaded server-side with the
        # correct Referer header so LinkedIn CDN doesn't block them.
        images_html = ""
        raw_image_urls = [
            u.strip()
            for u in st.session_state.image_urls.splitlines()
            if u.strip()
        ]
        for img_url in raw_image_urls:
            data_uri = _image_data_uri(img_url)
            if data_uri:
                images_html += (
                    f"<div style='margin:1.2rem 0;text-align:center;'>"
                    f"<img src='{data_uri}' style='max-width:100%;border-radius:4px;'/>"
                    f"</div>"
                )

        preview_html = f"""
        <html>
        <body style="font-family:Georgia,serif;padding:1.5rem;margin:0;
                     background:#fafafa;color:#333;font-size:15px;">
          <h1 style="font-size:1.5rem;margin-bottom:0.25rem;color:#1a1a1a;">
            {html_lib.escape(title)}
          </h1>
          <p style="color:#888;font-style:italic;margin-top:0;margin-bottom:1rem;">
            {html_lib.escape(subtitle)}
          </p>
          <hr style="border:none;border-top:1px solid #ddd;margin-bottom:1rem;"/>
          {body_html}
          {images_html}
        </body>
        </html>
        """
        components.html(preview_html, height=520, scrolling=True)

    st.markdown("<br/>", unsafe_allow_html=True)
    col_back, _, col_approve = st.columns([1, 3, 2])

    with col_back:
        if st.button("← Back"):
            go_to(2)
            st.rerun()

    with col_approve:
        if st.button("Approve & Publish →", type="primary"):
            st.session_state.generated_title = title
            st.session_state.generated_subtitle = subtitle
            st.session_state.generated_sections = text_to_sections(body_text)
            go_to(4)
            st.rerun()


# ── STEP 4 — PUBLISH ─────────────────────────────────────────────────────────
def step_publish():
    st.markdown("### Step 4 — Publish to Substack")

    st.info(
        f"**Ready to publish:** {st.session_state.generated_title}\n\n"
        f"Publishing to: {config.SUBSTACK_PUBLICATION_URL}"
    )

    user_email = st.text_input(
        "Send confirmation email to:",
        value=st.session_state.user_email or config.EMAIL_SENDER,
        placeholder="your@email.com",
    )

    if st.session_state.publish_error:
        st.error(st.session_state.publish_error)

    col_back, _, col_pub = st.columns([1, 3, 2])

    with col_back:
        if st.button("← Back"):
            st.session_state.publish_error = ""
            go_to(3)
            st.rerun()

    with col_pub:
        if st.button("Confirm & Publish", type="primary"):
            st.session_state.user_email = user_email
            st.session_state.publish_error = ""

            with st.spinner("Publishing to Substack..."):
                try:
                    image_handler = ImageHandler()
                    substack = SubstackClient()

                    # Transfer images to Substack CDN
                    substack_image_urls = []
                    raw_urls = [
                        u.strip()
                        for u in st.session_state.image_urls.splitlines()
                        if u.strip()
                    ]
                    for url in raw_urls:
                        try:
                            tmp_path = image_handler.download(url)
                            cdn_url = substack.upload_image(tmp_path)
                            substack_image_urls.append(cdn_url)
                            image_handler.cleanup(tmp_path)
                        except Exception as img_err:
                            st.warning(f"Skipped image (could not transfer): {img_err}")

                    # Publish
                    live_url = substack.publish(
                        title=st.session_state.generated_title,
                        subtitle=st.session_state.generated_subtitle,
                        sections=st.session_state.generated_sections,
                        image_urls=substack_image_urls,
                    )
                    st.session_state.published_url = live_url

                except Exception as pub_err:
                    st.session_state.publish_error = f"Publishing failed: {pub_err}"
                    st.rerun()
                    return

            # Send confirmation email (non-blocking)
            with st.spinner("Sending confirmation email..."):
                try:
                    EmailSender().send_confirmation(
                        to_email=user_email,
                        post_url=st.session_state.published_url,
                        post_title=st.session_state.generated_title,
                    )
                except Exception as mail_err:
                    st.warning(f"Post published but email failed: {mail_err}")

            go_to(5)
            st.rerun()


# ── STEP 5 — DONE ─────────────────────────────────────────────────────────────
def step_done():
    st.balloons()
    st.success("Your post is live on Substack!")

    st.markdown(
        f"""
        <div style="background:#f0fdf4;border:1px solid #86efac;border-radius:8px;padding:1.5rem;margin:1rem 0;">
          <p style="margin:0 0 8px 0;font-size:1rem;"><strong>Live link:</strong></p>
          <a href="{st.session_state.published_url}" target="_blank"
             style="color:#16a34a;font-size:1.1rem;word-break:break-all;">
            {st.session_state.published_url}
          </a>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(f"Confirmation email sent to **{st.session_state.user_email}**")

    st.markdown("<br/>", unsafe_allow_html=True)
    if st.button("Start Over"):
        for key in list(DEFAULTS.keys()):
            st.session_state[key] = DEFAULTS[key]
        st.rerun()


# ── Main ──────────────────────────────────────────────────────────────────────
st.markdown(
    "<h1 style='margin-bottom:0;'>LinkedIn → Substack Agent</h1>",
    unsafe_allow_html=True,
)
st.caption("Convert a LinkedIn post into a published Substack article — with human-in-the-loop editing.")
st.divider()

show_progress()

step = st.session_state.step
if step == 1:
    step_ingest()
elif step == 2:
    step_configure()
elif step == 3:
    step_review()
elif step == 4:
    step_publish()
elif step == 5:
    step_done()
