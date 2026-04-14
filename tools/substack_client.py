import base64
import json
import mimetypes
import os
import time
from urllib.parse import quote
import requests
import config


def _to_cdn_url(s3_url: str) -> str:
    """
    Convert a raw Substack S3 upload URL to a substackcdn.com proxy URL.
    Substack's renderer expects CDN URLs in captionedImage src, not raw S3 URLs.
    """
    encoded = quote(s3_url, safe="")
    return (
        f"https://substackcdn.com/image/fetch/"
        f"w_1456,c_limit,f_auto,q_auto:good,fl_progressive:steep/{encoded}"
    )


def _build_prosemirror_body(sections: list, image_urls: list = None) -> dict:
    """Convert a sections list into Substack's ProseMirror JSON document format."""
    content = []

    for section in sections:
        if section["type"] == "heading":
            level = section.get("level", 2)
            content.append({
                "type": "heading",
                "attrs": {"level": level},
                "content": [{"type": "text", "text": section["content"]}],
            })
        else:
            content.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": section["content"]}],
            })

    if image_urls:
        for url in image_urls:
            # Standard TipTap/ProseMirror inline image wrapped in a paragraph.
            # captionedImage is editor-only and is ignored by Substack's HTML renderer.
            content.append({
                "type": "paragraph",
                "content": [
                    {
                        "type": "image",
                        "attrs": {
                            "src": url,
                            "alt": None,
                            "title": None,
                        },
                    }
                ],
            })

    return {"type": "doc", "content": content}


class SubstackClient:
    def __init__(self):
        self.base_url = config.SUBSTACK_PUBLICATION_URL.rstrip("/")
        self.session = requests.Session()

        # Send cookies exactly as captured from the browser — no re-encoding
        self.session.headers.update({
            "Cookie": f"substack.sid={config.SUBSTACK_SID}; substack.lli={config.SUBSTACK_LLI}",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/publish/home",
        })
        print(f"[substack] client initialised | publication={self.base_url}")

    def _get_user_id(self) -> int:
        """Return the logged-in user's numeric ID."""
        print("[substack] fetching user ID...")
        time.sleep(1)
        resp = self.session.get("https://substack.com/api/v1/user/profile/self")
        resp.raise_for_status()
        user_id = resp.json()["id"]
        print(f"[substack] user ID = {user_id}")
        return user_id

    def upload_image(self, image_path: str) -> str:
        """Upload a local image file to Substack CDN. Returns the CDN URL.

        Substack expects a form-encoded base64 data URI — NOT multipart/form-data.
        Format: data={"image": b"data:image/jpeg;base64,<encoded>"}
        """
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type or not mime_type.startswith("image/"):
            mime_type = "image/jpeg"

        with open(image_path, "rb") as f:
            raw_bytes = f.read()
        encoded = base64.b64encode(raw_bytes)
        data_uri = b"data:" + mime_type.encode() + b";base64," + encoded

        print(f"[substack] uploading image | mime={mime_type} | base64_len={len(encoded)}")
        time.sleep(1)
        resp = self.session.post(
            f"{self.base_url}/api/v1/image",
            data={"image": data_uri},
        )
        if not resp.ok:
            print(f"[substack] image upload FAILED | status={resp.status_code} | body={resp.text[:500]}")
        resp.raise_for_status()
        s3_url = resp.json()["url"]
        cdn_url = _to_cdn_url(s3_url)
        print(f"[substack] image upload OK | s3={s3_url}")
        print(f"[substack] image CDN URL   → {cdn_url}")
        return cdn_url

    def _create_draft(self, title: str, subtitle: str, body: dict, user_id: int) -> str:
        """POST a new draft. Returns the draft ID as a string."""
        node_types = [n["type"] for n in body["content"]]
        print(f"[substack] creating draft | title='{title}' | nodes={node_types}")
        time.sleep(1)
        payload = {
            "draft_title": title,
            "draft_subtitle": subtitle,
            "draft_body": json.dumps(body),
            "draft_bylines": [{"id": user_id, "is_guest": False}],
            "audience": "everyone",
            "draft_section_id": None,
            "section_chosen": False,
            "write_comment_permissions": "everyone",
            "type": "newsletter",
        }
        resp = self.session.post(
            f"{self.base_url}/api/v1/drafts",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        draft_id = str(resp.json()["id"])
        print(f"[substack] draft created | id={draft_id}")

        # Fetch the draft back to verify what Substack stored
        time.sleep(1)
        check = self.session.get(f"{self.base_url}/api/v1/drafts/{draft_id}")
        if check.ok:
            stored_body = json.loads(check.json().get("draft_body", "{}"))
            all_types = [n.get("type") for n in stored_body.get("content", [])]
            image_paras = [
                n for n in stored_body.get("content", [])
                if n.get("type") == "paragraph"
                and any(c.get("type") == "image" for c in n.get("content", []))
            ]
            print(f"[substack] draft body stored | node_types={all_types}")
            print(f"[substack] image paragraphs stored = {len(image_paras)}")
            for para in image_paras:
                src = para["content"][0]["attrs"].get("src", "")
                print(f"[substack]   stored image src={src[:80]}...")
        else:
            print(f"[substack] could not fetch draft for verification | status={check.status_code}")

        return draft_id

    def _prepublish(self, draft_id: str):
        """Run Substack's prepublish validation step."""
        print(f"[substack] prepublish check | draft_id={draft_id}")
        time.sleep(1)
        resp = self.session.get(f"{self.base_url}/api/v1/drafts/{draft_id}/prepublish")
        resp.raise_for_status()
        print(f"[substack] prepublish OK | response={resp.text[:200]}")

    def _publish_draft(self, draft_id: str) -> dict:
        """Publish the draft. Returns the published post object."""
        print(f"[substack] publishing draft | id={draft_id}")
        time.sleep(1)
        resp = self.session.post(
            f"{self.base_url}/api/v1/drafts/{draft_id}/publish",
            json={"send": False, "share_automatically": False},
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        result = resp.json()
        print(f"[substack] publish OK | slug={result.get('slug')} | canonical={result.get('canonical_url')}")
        return result

    def publish(
        self,
        title: str,
        subtitle: str,
        sections: list,
        image_urls: list = None,
    ) -> str:
        """
        Full publish pipeline:
          get_user_id → build body → create_draft → prepublish → publish
        Returns the live post URL.
        """
        print(f"\n{'='*50}")
        print(f"[substack] starting publish pipeline")
        print(f"[substack] title='{title}' | sections={len(sections)} | images={len(image_urls or [])}")

        user_id = self._get_user_id()
        body = _build_prosemirror_body(sections, image_urls or [])

        image_nodes = [
            n for n in body["content"]
            if n.get("type") == "paragraph"
            and any(c.get("type") == "image" for c in n.get("content", []))
        ]
        print(f"[substack] prosemirror body built | total_nodes={len(body['content'])} | image_nodes={len(image_nodes)}")
        for para in image_nodes:
            src = para["content"][0]["attrs"]["src"]
            print(f"[substack]   image src={src[:80]}...")

        draft_id = self._create_draft(title, subtitle, body, user_id)
        self._prepublish(draft_id)
        result = self._publish_draft(draft_id)

        canonical = result.get("canonical_url") or f"{self.base_url}/p/{result.get('slug', draft_id)}"
        print(f"[substack] pipeline complete | url={canonical}")
        print(f"{'='*50}\n")
        return canonical
