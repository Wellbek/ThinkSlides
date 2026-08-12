"""
Carry the citation trail into the rendered deck.

``web_search`` results are already tracked as cited sources and shown in the
chat, but that trail stopped there: a slide bullet saying "15% increase in
revenue" gave someone reading the .pptx later no way back to where it came
from.

This runs as a pass over the slide JSON immediately before rendering, rather
than inside the renderer. That is deliberate. ``pptx_renderer`` is faithful to
the template by cloning real layout XML and filling placeholders without
touching ``rPr``; threading citation markup through that would mean generating
runs it does not otherwise generate, on every template, for both the
template and default paths. Editing the slide JSON instead means markers and
the reference list arrive as ordinary text and an ordinary slide, and inherit
whatever the template does with them - which is the only way this looks
intentional across very different templates.

Opt-in, and a no-op when the run has no research behind it: with nothing to
cite there is nothing to mark.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Slide titles are placeholders with their own size; a reference list runs long
# and is the one thing on its slide, so it gets the body placeholder.
SOURCES_SLIDE_TITLE = "Sources"

# Above this many references the appendix stops being readable on one slide.
# The deck still cites every one of them - the overflow simply continues onto
# another slide rather than being dropped or shrunk into illegibility.
MAX_SOURCES_PER_SLIDE = 12


def _domain(url: str) -> str:
    """The bit of a URL a reader actually recognises."""
    try:
        host = urlparse(url).netloc
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def collect_sources(research: list[dict] | None) -> list[dict]:
    """
    The deck's reference list: one entry per distinct URL, in the order the
    run first saw it, which is the order the markers will number them in.
    """
    ordered: list[dict] = []
    seen: set[str] = set()
    for finding in research or []:
        if not isinstance(finding, dict):
            continue
        url = (finding.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        ordered.append({
            "url": url,
            "title": (finding.get("title") or "").strip() or _domain(url) or url,
            "domain": _domain(url),
        })
    return ordered


def _mark_runs(slide: dict, source_count: int) -> int:
    """
    Append "[n]" to every run the model tagged with a sourceRef.

    Returns how many markers were placed. A sourceRef pointing past the end of
    the reference list is dropped rather than rendered: a marker with no entry
    behind it is worse than no marker, since it implies a source that is not
    there.
    """
    marked = 0
    for item in slide.get("contents") or []:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        for run in item.get("runs") or []:
            if not isinstance(run, dict):
                continue
            ref = run.pop("sourceRef", None)
            if not isinstance(ref, int) or not (1 <= ref <= source_count):
                continue
            text = str(run.get("text") or "").rstrip()
            if not text:
                continue
            marker = f" [{ref}]"
            if text.endswith(marker.strip()):
                continue
            run["text"] = f"{text}{marker}"
            marked += 1
    return marked


def _sources_slides(sources: list[dict], start_index: int) -> list[dict]:
    """The appendix: numbered references, chunked to stay readable."""
    slides: list[dict] = []
    for offset in range(0, len(sources), MAX_SOURCES_PER_SLIDE):
        chunk = sources[offset:offset + MAX_SOURCES_PER_SLIDE]
        continued = offset > 0
        runs = []
        for position, source in enumerate(chunk, start=offset + 1):
            label = source["title"]
            suffix = f" - {source['domain']}" if source["domain"] else ""
            runs.append({
                "text": f"[{position}] {label}{suffix}",
                "bullet": False,
                "level": 0,
            })
        slides.append({
            "slideIndex": start_index + len(slides),
            "layoutName": "Title and Content",
            "contents": [
                {
                    "type": "text",
                    "placeholderIdx": 0,
                    "runs": [{
                        "text": SOURCES_SLIDE_TITLE + (" (continued)" if continued else ""),
                    }],
                },
                {"type": "text", "placeholderIdx": 1, "runs": runs},
            ],
            "speakerNotes": "Sources gathered by web research during generation.",
        })
    return slides


def apply_source_citations(
    slides: list[dict],
    research: list[dict] | None,
    *,
    enabled: bool,
) -> tuple[list[dict], list[dict]]:
    """
    Add citation markers and a reference appendix to a deck.

    Returns ``(slides, sources)``. Both are returned unchanged when the feature
    is off or the run has nothing to cite, so callers can pass through
    unconditionally.
    """
    if not enabled:
        return slides, []

    sources = collect_sources(research)
    if not sources:
        # Deep Thinking never ran, or turned nothing up. Nothing to cite, so
        # the toggle is a no-op rather than an empty "Sources" slide.
        return slides, []

    marked = 0
    for slide in slides:
        if isinstance(slide, dict):
            marked += _mark_runs(slide, len(sources))

    appendix = _sources_slides(sources, start_index=len(slides))
    logger.info(
        "source_citations_applied",
        sources=len(sources),
        markers=marked,
        appendix_slides=len(appendix),
    )
    return [*slides, *appendix], sources
