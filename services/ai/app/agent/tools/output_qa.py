"""
The checks that geometry cannot see.

``validate_rendered_output``'s geometric pass reads real LibreOffice output
and is genuinely strong at what it does - it catches text off the slide and
content drawn on top of other content. But a deck can pass it cleanly while
still being broken in ways a reader notices immediately: a bullet reading
"[insert figure]", the same point made on three slides, a chart with the
same numbers listed beside it, or a file that only opens because
python-pptx is lenient about the package it produced.

Three passes live here, all pure functions over data the validator already
has:

* :func:`check_content` - the deck's own JSON, for defects in what it says
* :func:`check_package` - the .pptx as a zip, for defects in the file
* :func:`check_density` - the rendered PDF, for slides that are mostly empty

Content and package findings are defects to fix. Density findings are
advisories: a sparse cover slide is a legitimate design, and a validator
that sends the deck back for it would burn the revision budget on taste.
"""

from __future__ import annotations
import re
import zipfile

# Text that means the model left a hole in the deck. Matched
# case-insensitively against rendered slide copy.
_PLACEHOLDER_PATTERNS = (
    (re.compile(r"lorem ipsum", re.I), "leftover Lorem ipsum filler"),
    (re.compile(r"\[\s*(insert|add|your|tbd|placeholder|xx+)[^\]]*\]", re.I),
     "an unfilled [insert ...] placeholder"),
    (re.compile(r"\bclick to add\b", re.I), "the template's own 'Click to add' prompt"),
    (re.compile(r"^\s*(tbd|tba|n/a - fill in)\s*$", re.I), "a TBD stub"),
    (re.compile(r"<\s*(insert|placeholder|your)[^>]*>", re.I),
     "an unfilled <placeholder>"),
    (re.compile(r"\bxxx+\b", re.I), "an XXX stub"),
)

# A figure: 12, 3.4, 1,200, 45%. Used to spot copy restating a visual.
_NUMBER_RE = re.compile(r"\d[\d,]*\.?\d*")

# Below this many characters a "duplicate" is more likely a shared label
# ("Revenue", "2025") than a repeated point.
MIN_DUPLICATE_LENGTH = 25
# How many of a visual's own figures must reappear in the slide's copy
# before it counts as restating rather than headlining one of them.
RESTATED_FIGURE_THRESHOLD = 2
# Below this share of the page covered by content, a slide reads as empty.
# Deliberately low: this is an advisory, and a spacious slide is a choice.
SPARSE_COVERAGE_RATIO = 0.06
# ...and only for a slide carrying this many elements. A cover, a section
# break and a closing slide are *meant* to be sparse; what this is really
# looking for is a slide with plenty of content that still covers almost
# nothing, which means it rendered wrong rather than sparse.
MIN_ELEMENTS_TO_EXPECT_FILL = 8
# The footer band, as a fraction of page height. Deck chrome is not content
# and must not count towards a slide looking full.
FOOTER_BAND_TOP = 0.88

# Parts every readable .pptx must carry.
_REQUIRED_PARTS = (
    "[Content_Types].xml",
    "ppt/presentation.xml",
)


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (text or "").lower()).strip()


def _slide_texts(slide: dict) -> list[str]:
    """Every text run on a slide, in order."""
    texts = []
    for item in slide.get("contents") or []:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        for run in item.get("runs") or []:
            if isinstance(run, dict):
                texts.append(str(run.get("text") or ""))
    return texts


def _visual_figures(slide: dict) -> list[str]:
    """The figures a slide's chart/table/metric row already shows."""
    figures: list[str] = []
    for item in slide.get("contents") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "chart":
            for series in (item.get("chart") or {}).get("series") or []:
                for value in (series or {}).get("values") or []:
                    figures.extend(_NUMBER_RE.findall(str(value)))
        elif kind == "table":
            for row in (item.get("table") or {}).get("rows") or []:
                for cell in row or []:
                    figures.extend(_NUMBER_RE.findall(str(cell)))
        elif kind == "metrics":
            for entry in (item.get("metrics") or {}).get("items") or []:
                figures.extend(_NUMBER_RE.findall(str((entry or {}).get("value", ""))))
    return figures


# A heading is written by the planner, before anyone knows what the data will
# turn out to be. A deep run asked for "revenue trend over the last five years",
# the search surfaced three, and the slide shipped "Revenue Trend Over Five
# Years" above a three-point line. The claim in the heading is checkable
# against the visual underneath it, so it gets checked.
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

# Units that make a number a count of things on the chart rather than a date,
# a currency amount or a percentage. "2025 Revenue" must not read as a span.
_SPAN_UNITS = (
    "years?|quarters?|months?|weeks?|decades?|"
    "markets?|regions?|countries?|products?|segments?|categories|category|"
    "competitors?|channels?|brands?|teams?|sites?|steps?|phases?|pillars?"
)
_SPAN_CLAIM_RE = re.compile(
    r"\b(?P<n>\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")[-\s]+(?P<unit>" + _SPAN_UNITS + r")\b",
    re.IGNORECASE,
)


def _span_claim(text: str) -> tuple[int, str] | None:
    """The count a heading claims, e.g. "Five Years" -> (5, "years")."""
    match = _SPAN_CLAIM_RE.search(text or "")
    if not match:
        return None
    raw = match.group("n").lower()
    count = _NUMBER_WORDS.get(raw)
    if count is None:
        try:
            count = int(raw)
        except ValueError:
            return None
    return count, match.group("unit").lower()


def _slide_title(slide: dict) -> str:
    """The slide's heading: the first text block, as the renderer treats it."""
    for item in slide.get("contents") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            return " ".join(
                str(run.get("text") or "")
                for run in item.get("runs") or []
                if isinstance(run, dict)
            ).strip()
    return ""


def _visual_extent(slide: dict) -> tuple[int, str] | None:
    """How many data points the slide's first chart or table actually shows."""
    for item in slide.get("contents") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "chart":
            categories = (item.get("chart") or {}).get("categories") or []
            if categories:
                return len(categories), "chart"
        if item.get("type") == "table":
            rows = (item.get("table") or {}).get("rows") or []
            if rows:
                body = rows[1:] if (item.get("table") or {}).get("headerRow") else rows
                if body:
                    return len(body), "table"
    return None


def check_content(slides: list[dict]) -> list[dict]:
    """Defects in what the deck says, checked against the slide JSON."""
    issues: list[dict] = []
    seen_points: dict[str, int] = {}

    for index, slide in enumerate(slides or []):
        if not isinstance(slide, dict):
            continue
        slide_index = slide.get("slideIndex")
        slide_index = slide_index if isinstance(slide_index, int) else index
        texts = _slide_texts(slide)

        for text in texts:
            for pattern, description in _PLACEHOLDER_PATTERNS:
                if pattern.search(text):
                    issues.append({
                        "slideIndex": slide_index,
                        "type": "content",
                        "detail": (
                            f"The copy still contains {description}: {text[:60]!r}. "
                            "Replace it with the real content."
                        ),
                    })
                    break

        # A heading that claims more (or fewer) data points than the visual
        # under it actually has. The title is written before the data is
        # known, so it has to be reconciled against what was built.
        claim = _span_claim(_slide_title(slide))
        extent = _visual_extent(slide)
        if claim and extent and claim[0] != extent[0]:
            claimed, unit = claim
            actual, kind = extent
            issues.append({
                "slideIndex": slide_index,
                "type": "content",
                "detail": (
                    f"The heading claims {claimed} {unit} but the {kind} on this "
                    f"slide shows {actual}. Either cover {claimed} {unit} in the "
                    f"data, or retitle the slide to the {actual} it actually has - "
                    "a heading the visual does not support reads as missing data."
                ),
            })

        # The same point twice - within this slide, or already made earlier.
        local: set[str] = set()
        for text in texts:
            key = _normalise(text)
            if len(key) < MIN_DUPLICATE_LENGTH:
                continue
            if key in local:
                issues.append({
                    "slideIndex": slide_index,
                    "type": "content",
                    "detail": f"The same point appears twice on this slide: {text[:60]!r}.",
                })
            local.add(key)
            first_seen = seen_points.get(key)
            if first_seen is not None and first_seen != slide_index:
                issues.append({
                    "slideIndex": slide_index,
                    "type": "content",
                    "detail": (
                        f"This point already appears on slide {first_seen + 1}: "
                        f"{text[:60]!r}. Cut it or say something new here."
                    ),
                })
            else:
                seen_points.setdefault(key, slide_index)

        # Copy restating the visual next to it.
        figures = _visual_figures(slide)
        if figures:
            body = " ".join(texts[1:])
            body_numbers = set(_NUMBER_RE.findall(body))
            restated = {f for f in figures if f in body_numbers and len(f) > 1}
            if len(restated) >= RESTATED_FIGURE_THRESHOLD:
                issues.append({
                    "slideIndex": slide_index,
                    "type": "content",
                    "detail": (
                        "The bullets restate figures the chart or table on this slide "
                        f"already shows ({', '.join(sorted(restated)[:4])}). Replace them "
                        "with what the numbers mean, not the numbers themselves."
                    ),
                })

    return issues


def check_package(pptx_path: str) -> list[dict]:
    """Defects in the file itself, rather than in what it says.

    python-pptx opens a malformed package leniently, so corruption used to
    surface only indirectly - as a LibreOffice conversion that produced no
    preview at all. A duplicate ``ppt/slides/slide1.xml`` entry (which an
    earlier default-template generator really did produce) is exactly the
    kind of thing that reads as "the download is broken" to a user.
    """
    issues: list[dict] = []
    try:
        with zipfile.ZipFile(pptx_path) as archive:
            names = archive.namelist()
            bad = archive.testzip()
            if bad is not None:
                issues.append({
                    "type": "file",
                    "detail": f"The rendered .pptx contains a corrupt entry: {bad}.",
                })

            duplicates = sorted({n for n in names if names.count(n) > 1})
            if duplicates:
                issues.append({
                    "type": "file",
                    "detail": (
                        "The rendered .pptx contains duplicate package entries "
                        f"({', '.join(duplicates[:3])}), which some readers reject."
                    ),
                })

            for part in _REQUIRED_PARTS:
                if part not in names:
                    issues.append({
                        "type": "file",
                        "detail": f"The rendered .pptx is missing the required part {part}.",
                    })
    except (zipfile.BadZipFile, OSError) as e:
        issues.append({
            "type": "file",
            "detail": f"The rendered .pptx could not be read as a valid package: {e}",
        })
    return issues


def check_density(pdf_path: str) -> list[dict]:
    """Slides that render nearly empty. Advisory, never a gate."""
    import fitz

    advisories: list[dict] = []
    doc = fitz.open(pdf_path)
    try:
        for page_index in range(len(doc)):
            page = doc[page_index]
            page_area = page.rect.width * page.rect.height
            if page_area <= 0:
                continue

            footer_top = page.rect.height * FOOTER_BAND_TOP
            covered = 0.0
            blocks = 0
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if not (span.get("text") or "").strip():
                            continue
                        x0, y0, x1, y1 = span["bbox"]
                        if y0 >= footer_top:
                            continue  # deck chrome, not content
                        covered += max(0.0, x1 - x0) * max(0.0, y1 - y0)
                        blocks += 1
            for info in page.get_image_info():
                bbox = info.get("bbox")
                if not bbox:
                    continue
                x0, y0, x1, y1 = (float(v) for v in bbox)
                covered += max(0.0, x1 - x0) * max(0.0, y1 - y0)
                blocks += 1

            if (blocks >= MIN_ELEMENTS_TO_EXPECT_FILL
                    and covered / page_area < SPARSE_COVERAGE_RATIO):
                advisories.append({
                    "slideIndex": page_index,
                    "type": "density",
                    "detail": (
                        "This slide carries several elements but renders almost "
                        "blank - its content is probably not landing where it "
                        "should. Check the layout it was assigned."
                    ),
                })
    finally:
        doc.close()
    return advisories
