from __future__ import annotations

import base64
import json
import logging
from typing import Any

from fastapi import UploadFile

from app.core.config import OPENAI_MODEL_VISION
from app.core.openai_client import get_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You extract text from a photographed document and prepare only plain document data.
Return JSON only. Do not return Markdown, XML, HTML, HWPX files, HWPX package paths,
manifest data, section0.xml, or any generated document markup.

The response must match this shape exactly:
{
  "title": "document title",
  "body": "plain document body text"
}

Rules:
- If the image contains an obvious title, use it as title.
- If there is no obvious title, create a short title from the main content.
- Put all readable document text into body as plain text.
- Preserve meaningful line breaks.
- Do not invent HWPX structure. The server will insert title/body into a valid template.
"""


class OpenAiDocumentError(RuntimeError):
    pass


async def image_to_document_json(file: UploadFile) -> dict[str, str]:
    client = get_client()
    if client is None:
        raise OpenAiDocumentError("OpenAI API key is not configured on the backend.")

    image_bytes = await file.read()
    if not image_bytes:
        raise OpenAiDocumentError("Uploaded image file is empty.")

    content_type = file.content_type or "image/png"
    data_url = f"data:{content_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"

    completion = client.chat.completions.create(
        model=OPENAI_MODEL_VISION,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Extract the photographed document as title/body JSON only.",
                    },
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                ],
            },
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    raw = completion.choices[0].message.content or "{}"
    try:
        parsed: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OpenAiDocumentError(f"OpenAI returned invalid JSON: {raw[:200]}") from exc

    title = str(parsed.get("title") or "Captured document").strip()
    body = str(parsed.get("body") or "").strip()
    if not body:
        raise OpenAiDocumentError("OpenAI response did not include body text.")

    return {"title": title, "body": body}
