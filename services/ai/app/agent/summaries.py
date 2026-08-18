"""
Plain-language summaries of what each tool call actually did.

The activity feed used to answer "what is it doing?" with the tool's raw JSON
result - a design system bundle or a whole slide object, pretty-printed. That
is the right thing to have available and the wrong thing to lead with: the
interesting sentence ("planned 8 slides", "found 3 layout problems") was buried
in a hundred lines of braces.

So the server writes the sentence. It is authored here rather than asked of the
model for the same reason the validation narration is: it is derived from the
payload itself, so it is always present, always consistent, and cannot
describe something other than what happened. The raw JSON stays on the event
for anyone who expands it.

Every summarizer is defensive about shape - a tool that returned something
unexpected should degrade to a shorter sentence, never raise into the loop.
"""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlparse

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Titles/queries are echoed into the feed, so they are clipped rather than
# allowed to push the summary onto three lines.
_MAX_TITLE_CHARS = 60
_MAX_LISTED_TITLES = 5


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _clip(text: str, limit: int = _MAX_TITLE_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _join(parts: list[str]) -> str:
    """"a, b and c" - the way a person would say a list."""
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _slide_label(index: Any) -> str:
    return f"Slide {index + 1}" if isinstance(index, int) else "The slide"


def _analyze_document(args: dict, data: dict) -> str:
    from os.path import basename

    name = basename(str(args.get("document_path") or data.get("document_path") or "")) or "the document"
    bits = []
    length = data.get("content_length")
    if isinstance(length, int) and length:
        bits.append(f"{length:,} characters of text")
    images = data.get("images") or []
    if images:
        bits.append(_plural(len(images), "embedded image"))
    read = f"Read {name}"
    if bits:
        read += " - " + _join(bits)
    if data.get("truncated"):
        read += " (truncated to fit)"
    return read + "."


def _extract_key_data(args: dict, data: dict) -> str:
    counted = [
        (len(data.get(key) or []), label)
        for key, label in (
            ("metrics", "metric"),
            ("key_points", "key point"),
            ("figures", "figure"),
            ("dates", "date"),
            ("names", "name"),
            ("topics", "topic"),
        )
        if isinstance(data.get(key), list) and data.get(key)
    ]
    if not counted:
        return "Found no structured data points in the material."
    return "Pulled out " + _join([_plural(n, label) for n, label in counted]) + "."


def _design_system(args: dict, data: dict) -> str:
    layouts = data.get("layouts") or []
    palette = data.get("palette") or {}
    typography = data.get("typography") or {}
    bits = [_plural(len(layouts), "layout")] if layouts else []
    primary = palette.get("primary") or palette.get("accent1")
    if primary:
        bits.append(f"primary colour {primary}")
    heading = (typography.get("headingFont") or {})
    heading_name = heading.get("name") if isinstance(heading, dict) else heading
    if heading_name:
        bits.append(f"{heading_name} for headings")
    if not bits:
        return "Loaded the design system."
    return "Design system ready: " + _join(bits) + "."


def _web_search(args: dict, data: dict) -> str:
    results = data.get("results") or []
    query = _clip(data.get("query") or args.get("query") or "")
    if not results:
        note = data.get("note")
        return f'Searched for "{query}" - no results.' if not note else _clip(note, 160)
    domains = []
    for item in results:
        host = urlparse(str(item.get("url") or "")).netloc.removeprefix("www.")
        if host and host not in domains:
            domains.append(host)
    summary = f'Searched for "{query}" - {_plural(len(results), "result")}'
    if domains:
        summary += f" from {_join(domains[:3])}"
        if len(domains) > 3:
            summary += f" and {len(domains) - 3} more"
    return summary + "."


def _plan_structure(args: dict, data: dict) -> str:
    slides = data.get("slides") or []
    total = data.get("totalSlides") or len(slides)
    titles = [_clip(s.get("title") or "", 40) for s in slides if isinstance(s, dict)]
    titles = [t for t in titles if t]
    summary = f'Planned {_plural(int(total or 0), "slide")}'
    if titles:
        shown = titles[:_MAX_LISTED_TITLES]
        summary += ": " + ", ".join(shown)
        if len(titles) > len(shown):
            summary += f", +{len(titles) - len(shown)} more"
    return summary + "."


def _select_layout(args: dict, data: dict) -> str:
    name = data.get("layoutName") or "a layout"
    placeholders = data.get("placeholders") or []
    where = _slide_label(args.get("slide_index"))
    summary = f'{where} will use the "{name}" layout'
    if placeholders:
        summary += f" ({_plural(len(placeholders), 'placeholder')})"
    return summary + "."


def _run_text(item: dict) -> str:
    """The copy in one text content item, as a single string."""
    runs = item.get("runs")
    if isinstance(runs, list):
        return " ".join(str(r.get("text", "")) for r in runs if isinstance(r, dict)).strip()
    text = item.get("text")
    if isinstance(text, list):
        return " ".join(str(t) for t in text).strip()
    return str(text or "").strip()


def _design_slide(args: dict, data: dict) -> str:
    """What ended up on the slide.

    The designed slide is the renderer's own shape - text carried as `runs`
    inside placeholders, with the title just another text item - so the title
    is taken from the outline the tool was given and its own text item is not
    counted again as body copy.
    """
    import json as _json

    title = str(data.get("title") or "")
    if not title:
        try:
            outline = _json.loads(args.get("slide_outline") or "{}")
            title = str(outline.get("title") or "")
        except (TypeError, ValueError):
            title = ""

    contents = data.get("contents") or []
    pieces: dict[str, int] = {}
    bullets = 0
    lines = 0
    for item in contents:
        if not isinstance(item, dict):
            continue
        kind = item.get("type") or "text"
        if kind != "text":
            pieces[kind] = pieces.get(kind, 0) + 1
            continue
        text = _run_text(item)
        if title and text == title:
            continue  # the title placeholder, already named
        runs = item.get("runs")
        if isinstance(runs, list) and runs:
            for run in runs:
                if isinstance(run, dict) and run.get("bullet"):
                    bullets += 1
                else:
                    lines += 1
        elif text:
            lines += 1

    described = []
    if bullets:
        described.append(_plural(bullets, "bullet"))
    if lines:
        described.append(_plural(lines, "line", "lines") + " of copy")
    for kind, count in pieces.items():
        described.append(_plural(count, kind))

    where = _slide_label(data.get("slideIndex", args.get("slide_index")))
    summary = f'{where}, "{_clip(title)}"' if title else where
    return summary + (f": {_join(described)}." if described else " written.")


def _build_visual(args: dict, data: dict) -> str:
    summary = data.get("summary")
    if summary:
        text = str(summary)
        return text[:1].upper() + text[1:] + "."
    return f"{_slide_label(data.get('slideIndex'))} got its visual."


def _validate_slide(args: dict, data: dict) -> str:
    where = _slide_label(data.get("slideIndex"))
    errors = data.get("errors") or []
    warnings = data.get("warnings") or []
    if errors:
        return f"{where}: rejected - " + _join([_plural(len(errors), "error")] + (
            [_plural(len(warnings), "warning")] if warnings else []
        )) + f" - {_clip(str(errors[0]), 90)}"
    if warnings:
        return f"{where}: valid, with {_plural(len(warnings), 'warning')}: {_clip(str(warnings[0]), 90)}"
    return f"{where} matches the schema."


def _select_image(args: dict, data: dict) -> str:
    where = _slide_label(data.get("slideIndex", args.get("slide_index")))
    if data.get("selected"):
        return f"{where} gets an image from the source material."
    return f"{where} gets no image - {_clip(data.get('reason') or 'nothing matched well enough', 90)}"


def _finalize(args: dict, data: dict) -> str:
    if data.get("filePath"):
        name = data.get("fileName") or "the deck"
        bits = [_plural(int(data.get("slideCount") or 0), "slide")]
        size = data.get("fileSizeBytes")
        if isinstance(size, int) and size > 0:
            bits.append(f"{size / 1_000_000:.1f} MB" if size >= 1_000_000 else f"{size // 1000} KB")
        return f"Rendered {name} - {', '.join(bits)}."
    return "Could not render the .pptx; wrote a markdown fallback instead."


def _validate_rendered(args: dict, data: dict) -> str:
    issues = data.get("issues") or []
    advisories = data.get("advisories") or []
    if not issues:
        summary = "Checked the rendered file - nothing overflows, overlaps or reads as filler."
        if advisories:
            summary += f" {_plural(len(advisories), 'note')} on polish."
        return summary
    first = issues[0] if isinstance(issues[0], dict) else {}
    where = _slide_label(first.get("slideIndex"))
    detail = _clip(first.get("detail") or first.get("type") or "a layout problem", 90)
    summary = f"Found {_plural(len(issues), 'problem')} in the rendered file"
    return f"{summary} - {where}: {detail}" + ("." if len(issues) == 1 else ", and others.")


def _ask_user(args: dict, data: dict) -> str:
    questions = data.get("questions") or []
    if not questions:
        return "Asked the user a question."
    if len(questions) == 1:
        return f'Asked: "{_clip(questions[0].get("question"), 90)}"'
    return (
        f"Asked the user {len(questions)} questions: "
        + _join([_clip(q.get("question"), 45) for q in questions])
    )


_SUMMARIZERS: dict[str, Callable[[dict, dict], str]] = {
    "analyze_document": _analyze_document,
    "extract_key_data": _extract_key_data,
    "extract_template_design_system": _design_system,
    "web_search": _web_search,
    "plan_presentation_structure": _plan_structure,
    "select_layout": _select_layout,
    "design_slide_content": _design_slide,
    "build_chart": _build_visual,
    "build_table": _build_visual,
    "build_key_metrics": _build_visual,
    "validate_slide_json": _validate_slide,
    "select_source_image": _select_image,
    "finalize_and_render_pptx": _finalize,
    "validate_rendered_output": _validate_rendered,
    "ask_user_question": _ask_user,
}


def summarize_tool_result(
    tool_name: str, args: dict | None, data: Any, success: bool
) -> str:
    """One sentence describing what this tool call did, for the activity feed.

    Returns "" when there is nothing useful to say - a failed call, whose error
    the feed shows in full anyway, or a tool with no summarizer yet.
    """
    if not success or not isinstance(data, dict):
        return ""
    summarizer = _SUMMARIZERS.get(tool_name)
    if summarizer is None:
        return ""
    try:
        return summarizer(args or {}, data)
    except Exception as e:  # noqa: BLE001 - narration must not break a run
        logger.warning("tool_summary_failed", tool_name=tool_name, error=str(e))
        return ""
