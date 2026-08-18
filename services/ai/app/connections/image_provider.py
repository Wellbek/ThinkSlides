"""
Text-to-image, through whichever provider the organisation configured.

Small on purpose. This module knows how to turn a prompt into PNG bytes and
nothing else - it does not decide *whether* an image should be made, what it
should be of, or what may appear on it. Those are all in
``agent/tools/generate_image.py``, because they are editorial decisions and this
is a transport.

The one thing that does live here is the **negative prompt**. Every provider is
told, on every request, that the image must carry no text, no numbers, no axes
and no chart furniture. That is belt and braces on top of the builder's refusal
(#177), and it belongs on both sides: the builder stops a request for a chart,
and this stops a provider deciding on its own that a chart would be helpful.
"""

from __future__ import annotations

import base64

import httpx

from app.connections.store import ResolvedConnection
from app.observability.logging import get_logger

logger = get_logger(__name__)

# Long enough for a diffusion model, short enough that a hung provider does not
# hold a slide. A missing illustration is a far better outcome than a stalled
# run - the same trade the MCP handshake timeout makes.
TIMEOUT_SECONDS = 60.0

SUPPORTED_RATIOS = {
    "square": "1024x1024",
    "landscape": "1536x1024",
    "portrait": "1024x1536",
}

# Sent with every request, to every provider. The builder refuses a request that
# asks for data; this refuses a provider that offers it unasked.
NEGATIVE = (
    "No text of any kind. No numbers, no percentages, no currency. No axes, no "
    "gridlines, no tick marks, no legend, no labels, no captions, no chart, no "
    "graph, no table, no infographic. No logos, no watermarks, no signatures, "
    "no user interface."
)


class ImageError(RuntimeError):
    """A provider that could not be reached, or would not draw."""


async def generate(connection: ResolvedConnection, prompt: str,
                   ratio: str = "landscape") -> bytes:
    """One image as PNG bytes, from the organisation's configured provider."""
    size = SUPPORTED_RATIOS.get(ratio, SUPPORTED_RATIOS["landscape"])
    full = f"{prompt}\n\n{NEGATIVE}"

    if connection.connection_id == "image_openai":
        return await _openai(connection, full, size)
    if connection.connection_id == "image_zhipu":
        return await _zhipu(connection, full, size)
    raise ImageError(
        f"'{connection.connection_id}' is not an image provider this service "
        "knows how to call."
    )


async def _openai(connection: ResolvedConnection, prompt: str, size: str) -> bytes:
    key = connection.secrets.get("api_key") or ""
    if not key:
        raise ImageError("No API key is configured for this image provider.")

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "gpt-image-1", "prompt": prompt, "size": size,
                      "n": 1},
            )
        except httpx.HTTPError as exc:
            raise ImageError(f"Could not reach the image provider: {exc}") from exc

    return _decode(response, "data", "b64_json")


async def _zhipu(connection: ResolvedConnection, prompt: str, size: str) -> bytes:
    key = connection.secrets.get("api_key") or ""
    if not key:
        raise ImageError("No API key is configured for this image provider.")

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(
                "https://open.bigmodel.cn/api/paas/v4/images/generations",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "cogview-3-plus", "prompt": prompt, "size": size},
            )
        except httpx.HTTPError as exc:
            raise ImageError(f"Could not reach the image provider: {exc}") from exc

        payload = _payload(response)
        entries = payload.get("data") or []
        if not entries:
            raise ImageError("The image provider returned no image.")
        url = str((entries[0] or {}).get("url") or "")
        if not url:
            raise ImageError("The image provider returned no image.")
        try:
            fetched = await client.get(url)
            fetched.raise_for_status()
        except httpx.HTTPError as exc:
            raise ImageError(f"Could not fetch the generated image: {exc}") from exc
        return fetched.content


def _payload(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        raise ImageError(_reason(response))
    try:
        parsed = response.json()
    except ValueError as exc:
        raise ImageError("The image provider's reply was not JSON.") from exc
    return parsed if isinstance(parsed, dict) else {}


def _decode(response: httpx.Response, list_key: str, field: str) -> bytes:
    entries = _payload(response).get(list_key) or []
    if not entries:
        raise ImageError("The image provider returned no image.")
    encoded = str((entries[0] or {}).get(field) or "")
    if not encoded:
        raise ImageError("The image provider returned no image data.")
    try:
        return base64.b64decode(encoded)
    except (ValueError, TypeError) as exc:
        raise ImageError("The generated image could not be decoded.") from exc


def _reason(response: httpx.Response) -> str:
    """Why a request failed, in words worth showing a user."""
    if response.status_code in (401, 403):
        return "The image provider refused the credential. Check the API key."
    if response.status_code == 429:
        return "The image provider is rate limiting this key."
    if response.status_code >= 500:
        return f"The image provider returned an error ({response.status_code})."
    try:
        detail = (response.json().get("error") or {}).get("message")
    except (ValueError, AttributeError):
        detail = None
    return detail or f"The image provider rejected the request ({response.status_code})."
