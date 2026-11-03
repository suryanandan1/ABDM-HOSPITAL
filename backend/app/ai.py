"""Shared calls to Mistral AI: structured output (AI_MODEL) and OCR (AI_OCR_MODEL).
AI only suggests or summarises; it never decides."""

import base64
from contextlib import contextmanager

import httpx
from mistralai.client import Mistral, errors
from pydantic import BaseModel

from app.config import settings


class AIError(Exception):
    """AI call failed; message is safe to show to staff."""


def client() -> Mistral:
    if not settings.mistral_api_key:
        raise AIError("AI is not configured: set MISTRAL_API_KEY in backend/.env")
    return Mistral(api_key=settings.mistral_api_key, timeout_ms=180_000)


@contextmanager
def provider_errors():
    try:
        yield
    except errors.MistralError as exc:
        if exc.status_code == 401:
            raise AIError("AI provider credentials are invalid (check MISTRAL_API_KEY)") from exc
        if exc.status_code == 429:
            raise AIError("AI provider rate limit reached; try again shortly") from exc
        raise AIError(f"AI provider error {exc.status_code}") from exc  # body may echo request content; not shown
    except (httpx.RequestError, errors.NoResponseError) as exc:
        raise AIError("Could not reach the AI provider") from exc


def structured(system: str, content: list[dict], output_format: type[BaseModel], max_tokens: int = 16000) -> BaseModel:
    text = "\n\n".join(block["text"] for block in content)
    with provider_errors():
        response = client().chat.parse(
            response_format=output_format,
            model=settings.ai_model,
            max_tokens=max_tokens,
            temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
        )
    choice = response.choices[0] if response.choices else None
    if choice is None or choice.finish_reason == "length" or choice.message.parsed is None:
        raise AIError("The AI response was incomplete")
    return choice.message.parsed


def ocr(data: bytes, content_type: str) -> str:
    """Text of a PDF or image (page by page, as markdown) read by Mistral OCR."""
    uri = f"data:{content_type};base64,{base64.standard_b64encode(data).decode()}"
    document = ({"type": "document_url", "document_url": uri} if content_type == "application/pdf"
                else {"type": "image_url", "image_url": uri})
    with provider_errors():
        response = client().ocr.process(model=settings.ai_ocr_model, document=document)
    return "\n\n".join(page.markdown for page in response.pages if page.markdown).strip()
