import base64
import mimetypes
from mistralai.client import Mistral
import config
from tools.image_handler import ImageHandler

VISION_MODEL = "pixtral-12b-2409"

_DESCRIBE_PROMPT = (
    "You are analyzing an image from a LinkedIn post that will be used in a Substack newsletter article. "
    "Describe this image in 2-4 sentences focusing on: "
    "what type of image it is (chart, graph, infographic, photo, screenshot, etc.), "
    "the key data, metrics, or message it conveys, "
    "and any notable numbers, trends, or insights visible. "
    "Be specific and factual. Your description will be used to write relevant article content around this image."
)


class ImageAgent:
    def __init__(self):
        self.client = Mistral(api_key=config.MISTRAL_API_KEY)
        self.handler = ImageHandler()

    def describe_images(self, image_urls: list) -> list:
        """
        Describe each image using Pixtral vision model.
        Returns list of {index, url, description} dicts.
        Falls back to a placeholder description on any failure so the pipeline never breaks.
        """
        descriptions = []
        for i, url in enumerate(image_urls):
            try:
                desc = self._describe_single(url)
                print(f"[image_agent] image {i} described | chars={len(desc)}")
            except Exception as e:
                desc = f"Image {i + 1} from the LinkedIn post."
                print(f"[image_agent] image {i} description failed: {e}")
            descriptions.append({"index": i, "url": url, "description": desc})
        return descriptions

    def _describe_single(self, url: str) -> str:
        tmp_path = self.handler.download(url)
        try:
            mime, _ = mimetypes.guess_type(tmp_path)
            if not mime:
                mime = "image/jpeg"
            with open(tmp_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode()
            data_uri = f"data:{mime};base64,{encoded}"

            response = self.client.chat.complete(
                model=VISION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _DESCRIBE_PROMPT},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ],
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        finally:
            self.handler.cleanup(tmp_path)
