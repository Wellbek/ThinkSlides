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
the reference list arrive as ordinary content and inherit whatever the
template does with them - which is the only way this looks intentional across
very different templates.

Two things the renderer does have to know about, because they cannot be
expressed as text: ``link`` on a paragraph (the reference list, where the line
*is* the source) and ``citation`` on a paragraph (the "[3]" marker, appended as
its own run so the marker alone is clickable and not the whole sentence).

Opt-in, and a no-op when the run has no research behind it: with nothing to
cite there is nothing to mark.
"""

from __future__ import annotations

import math
from urllib.parse import urlparse

from app.observability.logging import get_logger

logger = get_logger(__name__)

SOURCES_SLIDE_TITLE = "Sources"

# Above this many references the appendix stops being readable on one slide.
# The deck still cites every one of them - the overflow simply continues onto
# another slide rather than being dropped or shrunk into illegibility.
#
# Deliberately conservative. A reference line is longer than a bullet and wraps
# to two lines on a template with generous body type, and the template-clone
# path cannot currently shrink text whose size it inherits from the master
# (issue #108), so the slide has to hold the lines at full size.
MAX_SOURCES_PER_SLIDE = 8

# A reference has to be recognisable, not complete: the link carries the rest.
# Some pages have titles that run to a full sentence, and one of those wraps a
# reference across three lines on its own.
MAX_REFERENCE_TITLE_CHARS = 70

# Used only when the deck carries no layout of its own to borrow, which in
# practice means a caller that assembled slide JSON by hand: every slide
# design_slide_content produces has a layoutId. "content" is the default
# catalog's ordinary body layout.
FALLBACK_LAYOUT_ID = "content"


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
            # Empty for most results - only some backends date a page. A
            # reference without one simply does not claim a date.
            "published": (finding.get("published") or "").strip(),
        })
    return ordered


def _mark_runs(slide: dict, sources: list[dict]) -> int:
    """
    Turn every run the model tagged with a sourceRef into a cited one.

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
            if not isinstance(ref, int) or not (1 <= ref <= len(sources)):
                continue
            if run.get("citation") or not str(run.get("text") or "").strip():
                continue
            run["citation"] = {"text": f" [{ref}]", "link": sources[ref - 1]["url"]}
            marked += 1
    return marked


def _shorten(title: str) -> str:
    """A title trimmed to the last whole word that fits."""
    if len(title) <= MAX_REFERENCE_TITLE_CHARS:
        return title
    clipped = title[:MAX_REFERENCE_TITLE_CHARS].rsplit(" ", 1)[0].rstrip(" ,.-:;")
    return f"{clipped or title[:MAX_REFERENCE_TITLE_CHARS].rstrip()}..."


def _reference_line(position: int, source: dict) -> str:
    """One numbered reference, as much of it as there is to say."""
    parts = [_shorten(source["title"])]
    if source["domain"] and source["domain"] not in source["title"]:
        parts.append(source["domain"])
    if source["published"]:
        parts.append(source["published"])
    return f"[{position}] " + " - ".join(parts)


def _borrowed_layout(slides: list[dict]) -> dict:
    """
    The layout the appendix should wear, taken from the deck's own body slides.

    Inventing one does not work. The template renderer resolves a slide's
    ``layoutId`` against the template's real layouts and falls back to layout
    "1" - the title slide - when a slide does not name one, which is how the
    reference slides came out wearing the cover design. The default renderer
    has its own version of the same trap: with no layoutId it infers the layout
    from the content, and a page holding a single reference looks exactly like
    a title slide, so a 13th source landed alone on a second page and rendered
    as a full-bleed brand-coloured cover.

    Borrowing sidesteps both. Whatever the body slides of this deck use exists
    in this template, has the placeholders being filled, and is by definition
    consistent with the rest of the deck.
    """
    candidates: list[dict] = []
    for slide in slides:
        if not isinstance(slide, dict):
            continue
        layout_id = str(slide.get("layoutId") or "").strip()
        if not layout_id:
            continue
        texts = [
            item for item in (slide.get("contents") or [])
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        if len(texts) < 2:
            continue
        texts.sort(key=lambda item: int(item.get("placeholderIdx", 0) or 0))
        title, body = texts[0], texts[1]
        # A real body, not a one-line standfirst on a cover.
        if len(body.get("runs") or []) < 2:
            continue
        candidates.append({
            "layoutId": layout_id,
            "titleIdx": int(title.get("placeholderIdx", 0) or 0),
            "bodyIdx": int(body.get("placeholderIdx", 1) or 1),
        })

    if not candidates:
        return {"layoutId": FALLBACK_LAYOUT_ID, "titleIdx": 0, "bodyIdx": 1}

    # The most-used body layout, and the last slide that used it - so the
    # placeholder indices come from a slide that really is laid out that way.
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate["layoutId"]] = counts.get(candidate["layoutId"], 0) + 1
    winner = max(counts, key=lambda layout_id: counts[layout_id])
    return next(c for c in reversed(candidates) if c["layoutId"] == winner)


def _chunk(sources: list[dict]) -> list[list[dict]]:
    """
    Split the references across as few slides as fit, evenly.

    Evenly matters: filling each page to the cap left the remainder alone on
    the last one, which is both an odd-looking page and the shape the layout
    inference used to mistake for a title slide.
    """
    pages = max(1, math.ceil(len(sources) / MAX_SOURCES_PER_SLIDE))
    per_page = math.ceil(len(sources) / pages)
    return [
        sources[offset:offset + per_page]
        for offset in range(0, len(sources), per_page)
    ]


def _sources_slides(sources: list[dict], start_index: int, layout: dict) -> list[dict]:
    """The appendix: numbered references, every line a link to its source."""
    slides: list[dict] = []
    position = 1
    for chunk in _chunk(sources):
        runs = [
            {
                "text": _reference_line(position + offset, source),
                "bullet": False,
                "level": 0,
                # The whole line is the source, so the whole line is the link.
                "link": source["url"],
            }
            for offset, source in enumerate(chunk)
        ]
        continued = position > 1
        slides.append({
            "slideIndex": start_index + len(slides),
            "layoutId": layout["layoutId"],
            "contents": [
                {
                    "type": "text",
                    "placeholderIdx": layout["titleIdx"],
                    "runs": [{
                        "text": SOURCES_SLIDE_TITLE + (" (continued)" if continued else ""),
                    }],
                },
                {"type": "text", "placeholderIdx": layout["bodyIdx"], "runs": runs},
            ],
            "speakerNotes": "Sources gathered by web research during generation.",
        })
        position += len(chunk)
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
            marked += _mark_runs(slide, sources)

    layout = _borrowed_layout(slides)
    appendix = _sources_slides(sources, len(slides), layout)
    logger.info(
        "source_citations_applied",
        sources=len(sources),
        markers=marked,
        appendix_slides=len(appendix),
        layout_id=layout["layoutId"],
    )
    return [*slides, *appendix], sources
