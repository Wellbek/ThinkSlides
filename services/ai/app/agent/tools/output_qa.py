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


# ── Generated images that came back carrying text ──────────────────────

# A generated image whose pixels are this share ink is an image that was drawn
# with lines in it. Below this, the odd dark region is texture; above it, the
# thing has structure it was told not to have.
GENERATED_TEXT_INK_RATIO = 0.055

# How wide a run of dark pixels has to be, as a share of the image, before it
# reads as a rule rather than as part of a picture. An axis is a long straight
# line; a photograph almost never has one.
GENERATED_RULE_SHARE = 0.62
GENERATED_RULE_ROWS = 3

# Below this the image is too small to judge, and the check would be reading
# noise. Skipped rather than guessed at.
MIN_INSPECTABLE_PX = 64


def slides_with_generated_images(slides: list[dict]) -> dict[int, list[dict]]:
    """Which slides carry a generated image, and which items they are.

    Read from the provenance the builder attached, so this cannot drift from
    what ``generate_image`` actually produced - and so an image reused from the
    user's own document is never inspected, because a photograph of a real
    dashboard in a source PDF is legitimate content.
    """
    found: dict[int, list[dict]] = {}
    for position, slide in enumerate(slides or []):
        if not isinstance(slide, dict):
            continue
        index = slide.get("slideIndex")
        index = index if isinstance(index, int) else position
        generated = [
            item for item in (slide.get("contents") or [])
            if isinstance(item, dict)
            and str(item.get("provenance") or "") == "generated"
        ]
        if generated:
            found[index] = generated
    return found


def check_generated_images(slides: list[dict]) -> list[dict]:
    """Generated images that came back carrying text or chart furniture.

    The builder's bar (#177) stops a *request* for a data-bearing image. It
    cannot stop a provider from returning one anyway: a model asked for "an
    abstract representation of growth" may well answer with axes and numbers on
    it, and a fabricated chart is indistinguishable from a real one on the page.

    So this reads the bytes that actually came back. It is deliberately blunt -
    it looks for the two things a chart has and an illustration does not, a high
    ink ratio and long straight rules - because a full OCR pass would cost more
    than the failure it is preventing and would still miss a chart with no
    labels on it.

    A hit sends the slide back, the same way an overflow does.
    """
    issues: list[dict] = []
    for index, items in slides_with_generated_images(slides).items():
        for item in items:
            reason = _generated_image_defect(item)
            if reason:
                issues.append({
                    "slideIndex": index,
                    "type": "generated_image_carries_data",
                    "detail": reason,
                })
    return issues


def _generated_image_defect(item: dict) -> str:
    """Why this generated image may not ship, or "" if it is fine."""
    payload = item.get("image") if isinstance(item.get("image"), dict) else {}
    raw = str(payload.get("data") or "")
    if not raw:
        return ""

    try:
        import base64
        import io

        from PIL import Image

        image = Image.open(io.BytesIO(base64.b64decode(raw)))
        image = image.convert("L")
    except Exception:  # noqa: BLE001 - an unreadable image is the renderer's problem
        return ""

    width, height = image.size
    if width < MIN_INSPECTABLE_PX or height < MIN_INSPECTABLE_PX:
        return ""

    # Downsampled first: the check is about structure, not detail, and a
    # megapixel scan per image would put seconds on every validation pass.
    scale = 240 / max(width, height)
    if scale < 1.0:
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
        width, height = image.size

    pixels = list(image.getdata())
    ink = _ink_mask(pixels, width, height)

    ratio = sum(ink) / float(len(ink) or 1)
    rules = _long_rules(ink, width, height)

    if rules >= GENERATED_RULE_ROWS:
        return (
            f"The generated image on this slide has {rules} long straight "
            "rules across it, which is what an axis or a gridline looks like. "
            "A generated image must never carry chart furniture - regenerate "
            "it as a scene or an idea, or use build_chart if the point is a "
            "quantity."
        )
    if ratio > GENERATED_TEXT_INK_RATIO and rules:
        return (
            "The generated image on this slide reads as a diagram rather than "
            "an illustration - it has ruled lines and dense dark structure. "
            "Regenerate it without any text, axes or labels, or use "
            "build_diagram if the point is a structure."
        )
    return ""


def _ink_mask(pixels: list[int], width: int, height: int) -> list[bool]:
    """Which pixels are dark relative to the image's own median.

    Relative rather than absolute: an illustration on a dark ground is all
    "dark" by an absolute threshold, and would fail every time.
    """
    ordered = sorted(pixels)
    median = ordered[len(ordered) // 2] if ordered else 128
    threshold = median - 55 if median > 110 else median + 55
    darker = median > 110
    return [
        (value < threshold) if darker else (value > threshold)
        for value in pixels
    ]


def _long_rules(ink: list[bool], width: int, height: int) -> int:
    """How many rows or columns are a long unbroken run of ink.

    An axis, a gridline and a table border are all this; a face, a landscape
    and an abstract shape are not. Counting rows and columns separately catches
    both orientations without any notion of what a chart is.
    """
    rules = 0
    for y in range(height):
        row = ink[y * width:(y + 1) * width]
        if _longest_run(row) >= width * GENERATED_RULE_SHARE:
            rules += 1
    for x in range(width):
        column = [ink[y * width + x] for y in range(height)]
        if _longest_run(column) >= height * GENERATED_RULE_SHARE:
            rules += 1
    return rules


def _longest_run(values: list[bool]) -> int:
    longest = current = 0
    for value in values:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


# ── Exhibits too small to read ─────────────────────────────────────────

# A chart's box, in square inches, below which no amount of thinning makes it
# legible on a projected slide. A deck-width exhibit is around 40 square inches
# and a quarter of a composed page is 6, so this is the floor under the quarter -
# a page divided further than four ways is not a dashboard, it is a mosaic.
MIN_EXHIBIT_AREA_IN2 = 3.2

# And a shape below which it is not a chart whatever its area. A band two inches
# by a third of an inch has no room for a value axis, and a column that narrow
# cannot carry a category label.
MIN_EXHIBIT_SIDE_IN = 1.1

# The canvas a slide is measured against when nothing says otherwise. 16:9 at
# the size this renderer uses.
DEFAULT_CANVAS_IN = (13.333, 7.5)

EXHIBIT_TYPES = frozenset({"chart", "table", "diagram"})


def check_exhibit_legibility(
    slides: list[dict],
    canvas_in: tuple[float, float] = DEFAULT_CANVAS_IN,
) -> list[dict]:
    """Exhibits bound to a region too small to read them in.

    The renderer thins a small chart's furniture - fewer labels, no legend,
    coarser ticks - which is what makes a four-exhibit page work. Past a point
    thinning runs out: there is no version of a chart that is legible in a band
    an inch tall, and a page divided that far has stopped being a dashboard.

    Checked against the region tree rather than the rendered PDF, because a
    chart's type is rasterised into its PNG and the converted page has no text
    spans to measure inside it. The geometry is the honest thing to check: it is
    what decided the size in the first place.
    """
    from app.renderer.regions import RegionTree

    issues: list[dict] = []
    for position, slide in enumerate(slides or []):
        if not isinstance(slide, dict):
            continue
        index = slide.get("slideIndex")
        index = index if isinstance(index, int) else position

        tree = RegionTree.from_slide(slide, canvas_in[0], canvas_in[1])
        if tree is None:
            # No region tree means every exhibit has the slide's body to itself,
            # which is never too small.
            continue

        for item in slide.get("contents") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "") not in EXHIBIT_TYPES:
                continue
            region_id = str(item.get("regionId") or "").strip()
            if not region_id:
                continue
            resolved = tree.resolved.get(region_id)
            if resolved is None:
                continue
            rect = resolved.rect
            reason = _too_small_to_read(rect.width, rect.height)
            if reason:
                issues.append({
                    "slideIndex": index,
                    "type": "exhibit_too_small_to_read",
                    "detail": (
                        f"The {item['type']} bound to region '{region_id}' is "
                        f"{rect.width:.1f}in by {rect.height:.1f}in. {reason} "
                        "Give this page fewer exhibits, or move this one to a "
                        "slide of its own."
                    ),
                })
    return issues


def _too_small_to_read(width_in: float, height_in: float) -> str:
    """Why an exhibit this size cannot be read, or "" if it can."""
    if min(width_in, height_in) < MIN_EXHIBIT_SIDE_IN:
        return (
            f"Neither side may be under {MIN_EXHIBIT_SIDE_IN}in - there is no "
            "room for an axis, and a column that narrow cannot carry a category "
            "label."
        )
    if width_in * height_in < MIN_EXHIBIT_AREA_IN2:
        return (
            f"That is under {MIN_EXHIBIT_AREA_IN2}in2, which is smaller than a "
            "quarter of a composed page - past the point where dropping labels "
            "and legends makes it legible."
        )
    return ""


# ── The deck, as a whole ───────────────────────────────────────────────
#
# Every other check in this module looks at one slide. None of them can see the
# smell this one exists for: a deck where every visual page carries exactly one
# bar chart. Each slide is individually fine, and the deck is a template.
#
# **It flags sameness, not sparseness.** That distinction is the whole design.
# A deck with three text slides, five one-chart slides and one dashboard passes.
# A deck of nine identical bar charts does not. A thin deck with few exhibits
# passes, because the material did not go further - and pushing it to go further
# would produce fabricated exhibits, which is exactly the trap budget.py was
# written to prevent. Get this backwards and the check becomes a filler quota.

# Below this many exhibits there is not enough of a deck to have a pattern. Two
# bar charts is not a habit.
MIN_EXHIBITS_TO_JUDGE = 5

# What share of a deck's exhibits may be the same kind before it reads as a
# template rather than as a set of decisions. Generous on purpose: a financial
# review legitimately leans on bars, and a deck that is 70% one type is only
# suspicious once there is a lot of it.
MAX_ONE_TYPE_SHARE = 0.8

# How many distinct exhibit kinds a deck with plenty of exhibits should manage.
# Not a quota on count - a quota on variety, which is a different thing.
MIN_DISTINCT_TYPES = 2

# Above this many exhibits, one type carrying all of them is a stronger signal
# than the share alone: nine of one thing is a habit whatever the percentage.
MANY_EXHIBITS = 8


def _exhibit_kinds(slide: dict) -> list[str]:
    """What kinds of exhibit one slide carries.

    A chart's type counts as its kind, not "chart": nine bar charts and a deck
    with a bridge, a mix, a ranked list and a trend are the two cases this whole
    check is trying to tell apart, and at the level of "chart" they are
    identical.
    """
    kinds: list[str] = []
    for item in (slide or {}).get("contents") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        if kind == "chart":
            chart = item.get("chart") if isinstance(item.get("chart"), dict) else {}
            kinds.append("chart:" + str(chart.get("chartType") or "bar").lower())
        elif kind == "diagram":
            payload = item.get("diagram") if isinstance(item.get("diagram"), dict) else {}
            kinds.append("diagram:" + str(payload.get("kind") or "flow"))
        elif kind in ("table", "metrics", "image"):
            kinds.append(kind)
    return kinds


def check_deck(slides: list[dict]) -> list[dict]:
    """Advisories about the deck as a whole. Sameness, never sparseness.

    Returned as advisories rather than as issues: a uniform deck is a weaker
    deck, not a broken one, and sending it back would spend the revision budget
    on taste. What it buys is that the uniformity is *said* - by the time a
    reviewer notices nine identical bar charts, the deck has already been sent.
    """
    advisories: list[dict] = []
    per_slide = [
        _exhibit_kinds(slide) for slide in (slides or [])
        if isinstance(slide, dict)
    ]
    all_kinds = [kind for kinds in per_slide for kind in kinds]
    total = len(all_kinds)

    if total < MIN_EXHIBITS_TO_JUDGE:
        # Not enough of a deck to have a pattern, and a thin deck passing is the
        # point rather than an omission.
        return advisories

    counts: dict[str, int] = {}
    for kind in all_kinds:
        counts[kind] = counts.get(kind, 0) + 1
    dominant, dominant_count = max(counts.items(), key=lambda pair: pair[1])
    share = dominant_count / total

    if len(counts) < MIN_DISTINCT_TYPES or share >= MAX_ONE_TYPE_SHARE:
        advisories.append({
            "type": "deck_exhibits_are_uniform",
            "detail": (
                f"{dominant_count} of this deck's {total} exhibits are the same "
                f"kind ({dominant}). A deck of one exhibit type reads as a "
                "template rather than as a set of decisions - a bridge, a mix, a "
                "ranked comparison and a trend answer different questions, and "
                "the chart vocabulary carries all of them. This is only worth "
                "changing where a different exhibit would answer the slide's "
                "question better; do not add exhibits the material does not "
                "support."
            ),
        })
    elif dominant_count >= MANY_EXHIBITS and len(counts) <= 2:
        advisories.append({
            "type": "deck_exhibits_are_uniform",
            "detail": (
                f"{dominant_count} exhibits of this deck are {dominant}, across "
                f"only {len(counts)} kinds in total. Worth checking whether some "
                "of those slides are asking a different question than the one a "
                f"{dominant} answers."
            ),
        })

    spread = _exhibit_spread(per_slide)
    if spread:
        advisories.append(spread)
    return advisories


def _exhibit_spread(per_slide: list[list[str]]) -> dict | None:
    """Whether every visual page carries exactly the same number of exhibits.

    Uniform at one is the failure this workstream exists to prevent. Uniform at
    four is the opposite failure and just as suspicious: a page's exhibit count
    follows its material, and material does not come in equal portions.

    A deck with few visual pages is not judged - three pages carrying one
    exhibit each is a small deck, not a pattern.
    """
    counts = [len(kinds) for kinds in per_slide if kinds]
    if len(counts) < 4:
        return None
    if len(set(counts)) > 1:
        return None

    only = counts[0]
    if only == 1:
        return {
            "type": "deck_exhibit_counts_are_uniform",
            "detail": (
                f"Every one of this deck's {len(counts)} visual slides carries "
                "exactly one exhibit. Some pages carry a finding that needs two "
                "or three exhibits side by side, and this deck has none. Where "
                "the material genuinely supports one, "
                "one is right - this is worth a look, not an automatic change."
            ),
        }
    return {
        "type": "deck_exhibit_counts_are_uniform",
        "detail": (
            f"Every one of this deck's {len(counts)} visual slides carries "
            f"exactly {only} exhibits. An exhibit count follows the material, "
            "and material does not come in equal portions - a page whose "
            f"finding is one number does not need {only} charts."
        ),
    }
