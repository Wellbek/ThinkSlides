"""
Publication-grade figures, where an organisation has configured a provider.

**The data path does not change - only the rasteriser.** The same validated
arguments go to either backend: series lengths already checked against
categories, plain numbers, units already moved into the series name. That is the
whole reason this is safe. The provider receives numbers this system has vetted,
not a prompt describing numbers, so there is nothing here for a model to invent.
It is the same class of call as matplotlib, made over HTTP.

That distinction is what separates this from ``generate_image``, which is
hedged about with a required grounding argument and an absolute bar on carrying
figures. A figure provider is *handed* the figures. It is not being asked to
imagine them.

Everything stays on matplotlib by default. A provider that fails falls back to
matplotlib and says so, because a missing exhibit is a worse outcome than a less
beautiful one - the same trade every degradation in this codebase makes.
"""

from __future__ import annotations

import base64

import httpx

from app.connections.store import ResolvedConnection
from app.observability.logging import get_logger

logger = get_logger(__name__)

# Renderer preferences a chart may state. "matplotlib" is not merely the
# default, it is the fallback for every other value.
RENDERERS = ("matplotlib", "figure")

DEFAULT_BASE_URL = "https://api.figurelabs.com/v1"

# Shorter than an image generation: a figure provider is drawing from data it
# was handed, not diffusing, and a chart is on the critical path of a render.
TIMEOUT_SECONDS = 25.0


class FigureError(RuntimeError):
    """A provider that could not draw this figure."""


def resolve_renderer(chart_data: dict) -> str:
    """Which rasteriser this chart asked for, defaulting to matplotlib."""
    requested = str((chart_data or {}).get("renderer") or "").strip().lower()
    return requested if requested in RENDERERS else "matplotlib"


async def render(
    connection: ResolvedConnection,
    chart_data: dict,
    design_system: dict | None,
    width_in: float,
    height_in: float,
    ground: str | None = None,
) -> bytes:
    """One chart as PNG bytes, drawn by the configured figure provider.

    Sent as the same typed structure ``build_chart`` validated - categories,
    series, axis titles - plus the deck's own style tokens, so the figure comes
    back inside the deck's palette rather than in the provider's house style.
    """
    key = connection.secrets.get("api_key") or ""
    if not key:
        raise FigureError("No API key is configured for the figure provider.")
    base = str(connection.config.get("base_url") or "").strip() or DEFAULT_BASE_URL

    payload = {
        "chartType": chart_data.get("chartType"),
        "categories": chart_data.get("categories") or [],
        "series": chart_data.get("series") or [],
        "title": chart_data.get("title") or "",
        "xAxisTitle": chart_data.get("xAxisTitle") or "",
        "yAxisTitle": chart_data.get("yAxisTitle") or "",
        "unit": chart_data.get("unit") or "",
        "widthIn": round(float(width_in), 3),
        "heightIn": round(float(height_in), 3),
        "style": _style_tokens(design_system, ground),
        "transparent": True,
    }

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(
                f"{base.rstrip('/')}/figures",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise FigureError(f"Could not reach the figure provider: {exc}") from exc

    if response.status_code >= 400:
        raise FigureError(_reason(response))

    content_type = response.headers.get("content-type", "")
    if content_type.startswith("image/"):
        return response.content
    try:
        encoded = str((response.json() or {}).get("png") or "")
    except ValueError as exc:
        raise FigureError("The figure provider's reply was not an image.") from exc
    if not encoded:
        raise FigureError("The figure provider returned no image.")
    try:
        return base64.b64decode(encoded)
    except (ValueError, TypeError) as exc:
        raise FigureError("The returned figure could not be decoded.") from exc


def _style_tokens(design_system: dict | None, ground: str | None) -> dict:
    """The deck's chart style, in the shape a provider is sent.

    Read from the same ``dataViz`` block matplotlib is styled from, so a deck
    rendered through either backend is the same deck. A provider that ignores
    these produces a figure that does not belong to the page, which is why they
    are sent rather than left to the provider's defaults.
    """
    data_viz = ((design_system or {}).get("dataViz") or {})
    palette = ((design_system or {}).get("palette") or {}).get("colors") or []
    typography = ((design_system or {}).get("typography") or {})
    return {
        "seriesColors": data_viz.get("chartSeriesColors") or [
            entry.get("value") for entry in palette
            if isinstance(entry, dict) and entry.get("value")
        ][:6],
        "axisColor": data_viz.get("chartAxisColor"),
        "gridColor": data_viz.get("chartGridColor"),
        "labelMode": data_viz.get("chartLabelMode"),
        "fontFamily": typography.get("bodyFont") or typography.get("headingFont"),
        "ground": ground,
    }


def _reason(response: httpx.Response) -> str:
    if response.status_code in (401, 403):
        return "The figure provider refused the credential."
    if response.status_code == 429:
        return "The figure provider is rate limiting this key."
    if response.status_code >= 500:
        return f"The figure provider returned an error ({response.status_code})."
    try:
        detail = (response.json().get("error") or {}).get("message")
    except (ValueError, AttributeError):
        detail = None
    return detail or f"The figure provider rejected the chart ({response.status_code})."
