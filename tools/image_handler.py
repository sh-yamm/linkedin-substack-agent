import os
import tempfile
import requests


class ImageHandler:
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        # LinkedIn CDN checks Referer — without it the request is treated as
        # a hotlink and may return HTML or a redirect instead of the image.
        "Referer": "https://www.linkedin.com/",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    EXT_MAP = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
        "image/avif": "avif",
    }

    def download(self, url: str) -> str:
        """Download an image from a URL to a temp file. Returns the temp file path."""
        print(f"[image] downloading → {url[:80]}...")
        response = requests.get(url, headers=self.HEADERS, timeout=15)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        size_kb = len(response.content) / 1024

        # Validate we actually got an image, not an HTML error/redirect page
        if not content_type.startswith("image/"):
            print(f"[image] download failed — got '{content_type}', not an image")
            raise ValueError(
                f"URL did not return an image (got '{content_type}'). "
                f"LinkedIn CDN may require authentication for this URL."
            )

        ext = self.EXT_MAP.get(content_type, "jpg")
        tmp = tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False)
        tmp.write(response.content)
        tmp.close()
        print(f"[image] downloaded OK | type={content_type} | size={size_kb:.1f}KB | path={tmp.name}")
        return tmp.name

    def cleanup(self, path: str):
        """Delete a temp file silently."""
        try:
            os.unlink(path)
            print(f"[image] temp file deleted → {path}")
        except Exception:
            pass
