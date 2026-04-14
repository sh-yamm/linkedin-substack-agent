import json
from mistralai.client import Mistral
import config
from prompts.system_prompts import SYSTEM_PROMPT
from prompts.tone_modifiers import TONE_MODIFIERS


def _normalise_sections(sections: list) -> list:
    """
    Ensure every item in sections is a properly structured dict.
    Mistral occasionally returns plain strings instead of dicts — wrap them.
    """
    normalised = []
    for item in sections:
        if isinstance(item, dict):
            # Ensure required keys exist
            item.setdefault("type", "paragraph")
            item.setdefault("content", "")
            normalised.append(item)
        elif isinstance(item, str) and item.strip():
            normalised.append({"type": "paragraph", "content": item.strip()})
        # skip empty/null items
    return normalised


class ContentAgent:
    def __init__(self):
        self.client = Mistral(api_key=config.MISTRAL_API_KEY)
        self.model = config.MISTRAL_MODEL

    def rewrite(self, linkedin_text: str, tone: str, edit_instructions: str = "") -> dict:
        """
        Rewrite a LinkedIn post into a structured Substack article.

        Returns a dict:
          {
            "title": str,
            "subtitle": str,
            "sections": [{"type": "paragraph"|"heading", "level": int, "content": str}, ...]
          }
        """
        tone_guidance = TONE_MODIFIERS.get(tone, TONE_MODIFIERS["Professional"])

        user_content = f"""Transform this LinkedIn post into a Substack article.

LinkedIn post:
---
{linkedin_text}
---

Tone: {tone}
Tone guidance: {tone_guidance}
"""
        if edit_instructions.strip():
            user_content += f"\nAdditional instructions from the author: {edit_instructions.strip()}\n"

        print(f"[agent] sending to Mistral | model={self.model} | tone={tone} | "
              f"input_chars={len(linkedin_text)} | instructions={'yes' if edit_instructions.strip() else 'none'}")

        response = self.client.chat.complete(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )

        raw = response.choices[0].message.content
        print(f"[agent] Mistral responded | finish_reason={response.choices[0].finish_reason} | "
              f"output_chars={len(raw)}")

        result = json.loads(raw)

        if "title" not in result or "sections" not in result:
            raise ValueError(f"Mistral returned unexpected structure: {raw[:300]}")

        # Ensure subtitle exists
        if "subtitle" not in result:
            result["subtitle"] = ""

        # Normalise sections — Mistral occasionally returns strings instead of dicts
        result["sections"] = _normalise_sections(result["sections"])

        print(f"[agent] parsed OK | title='{result['title']}' | "
              f"sections={len(result['sections'])} | "
              f"types={[s['type'] for s in result['sections']]}")

        return result
