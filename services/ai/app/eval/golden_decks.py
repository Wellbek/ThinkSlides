"""
Four golden decks, and the exhibit profile each one has to hold.

A capability nothing asserts is a capability that regresses. These four assert
that the exhibit work in this epic is actually used - and, in one case, that it
is **not** used when it should not be.

======================== ==================================================
financial review         Figure-dense. A composed dashboard page, and at
                         least four distinct chart kinds across the deck.
market scan              Mixed. A spread of exhibit counts, not a uniform one.
technical architecture   Structural. Diagrams rather than charts.
deliberately thin        Little material behind it. No dashboard, and slides
                         with no exhibit where the material carries none.
======================== ==================================================

**The fourth one is the important one.** The other three assert that the new
capability is used; the thin brief asserts that it is not used when it should not
be, which is the failure mode every other issue in this stack has to guard
against individually. Having it here means the governing constraint - exhibit
count follows the material, never a quota - is enforced once, mechanically,
rather than re-argued in each review.

These are captured slide JSON, not LLM output. What they protect is the
machinery: that a composed page still renders, that the diagram kinds still
compile, that the deck-level check still passes a thin deck. The prompts they
stand in for are in ``BRIEFS`` for the end-to-end runs that do use a provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BRIEFS = {
    "financial_review": (
        "Build a financial review of last year: the revenue bridge, the mix "
        "shift under a flat total, margin against plan, and how it holds by "
        "region. Figure-dense - the audience is a board."
    ),
    "market_scan": (
        "Scan the market for us: how big it is, who the players are, where it "
        "is growing, and what would have to be true for us to enter."
    ),
    "technical_architecture": (
        "Explain our platform architecture to a non-technical exec team: the "
        "request path, how the services relate, and the rollout sequence."
    ),
    "thin_brief": (
        "Make me a short deck about why our team should adopt a weekly "
        "retrospective. I do not have any data on it."
    ),
}


def _title(text: str) -> dict:
    return {"type": "text", "runs": [{"text": text, "bold": True, "bullet": False}]}


def _text(*lines: str, bullet: bool = True) -> dict:
    return {"type": "text",
            "runs": [{"text": line, "bullet": bullet, "level": 0} for line in lines]}


def _chart(kind: str, categories: list[str], series: list[dict], **extra) -> dict:
    chart = {"chartType": kind, "categories": categories, "series": series}
    chart.update(extra)
    return {"type": "chart", "chart": chart}


def _diagram(kind: str, items: list[dict], **extra) -> dict:
    payload = {"kind": kind, "items": items}
    payload.update(extra)
    return {"type": "diagram", "diagram": payload}


def _bound(block: dict, region_id: str, slot: str = "") -> dict:
    return {**block, "regionId": region_id, "slot": slot or region_id}


# ── 1. Financial review: figure-dense, with one composed dashboard ─────

FINANCIAL_REVIEW = [
    {"slideIndex": 0, "layoutId": "title", "contents": [
        _title("FY2025 financial review"),
        _text("Revenue held; the mix moved underneath it", bullet=False)]},

    # The dashboard page: four exhibits answering four different questions,
    # each bound to its own region of a composed page.
    {"slideIndex": 1, "layoutId": "content",
     "archetype": "dashboard", "contentType": "dashboard",
     "exhibitPlan": [
         "What are the headline figures?",
         "Where did the movement come from?",
         "What changed underneath the flat total?",
         "Does it hold across regions?",
     ],
     "regions": [
         {"id": "strip", "kind": "band", "x": 0.0, "y": 0.0, "w": 1.0, "h": 0.22},
         {"id": "bridge", "kind": "panel", "x": 0.0, "y": 0.24, "w": 0.49, "h": 0.38},
         {"id": "mix", "kind": "panel", "x": 0.51, "y": 0.24, "w": 0.49, "h": 0.38},
         {"id": "regions", "kind": "panel", "x": 0.0, "y": 0.64, "w": 1.0, "h": 0.36},
     ],
     "contents": [
        _title("Revenue held flat while the mix moved"),
        _bound({"type": "metrics", "metrics": {"items": [
            {"value": "$4.15B", "label": "Revenue"},
            {"value": "+1.2%", "label": "Year over year"},
            {"value": "31.4%", "label": "Gross margin"},
            {"value": "$0.9B", "label": "Free cash flow"}]}}, "strip"),
        _bound(_chart("waterfall", ["FY24", "Subscriptions", "Services", "Churn", "FY25"],
                      [{"name": "US$m", "values": [4104.0, 72.0, 44.0, -68.0, 4152.0]}],
                      totalIndices=[0, 4], unit="US$m"), "bridge"),
        _bound(_chart("stacked100", ["FY23", "FY24", "FY25"],
                      [{"name": "Subscription", "values": [52.0, 58.0, 66.0]},
                       {"name": "Services", "values": [33.0, 29.0, 24.0]},
                       {"name": "Licence", "values": [15.0, 13.0, 10.0]}]), "mix"),
        _bound(_chart("slope", ["FY24", "FY25"],
                      [{"name": "Americas", "values": [1980.0, 2090.0]},
                       {"name": "EMEA", "values": [1310.0, 1288.0]},
                       {"name": "APAC", "values": [814.0, 774.0]}]), "regions"),
     ]},

    {"slideIndex": 2, "layoutId": "chart", "contents": [
        _title("Margin recovered in the second half"),
        _text("Cost actions landed in Q3, not Q2 as planned"),
        _chart("combo", ["Q1", "Q2", "Q3", "Q4"],
               [{"name": "Revenue (US$m)", "values": [1010.0, 1024.0, 1049.0, 1069.0]},
                {"name": "Gross margin (%)", "values": [29.8, 30.1, 32.0, 33.4]}],
               secondaryAxisSeries=["Gross margin (%)"],
               secondaryAxisTitle="Gross margin (%)")]},

    {"slideIndex": 3, "layoutId": "chart", "contents": [
        _title("Where we sit against plan"),
        _chart("bullet", ["Revenue", "Gross margin", "Free cash flow"],
               [{"name": "Actual", "values": [4152.0, 31.4, 902.0]}],
               targets=[4200.0, 31.0, 850.0])]},

    {"slideIndex": 4, "layoutId": "table", "contents": [
        _title("Segment detail"),
        {"type": "table", "table": {"headerRow": True, "rows": [
            ["Segment", "FY25 (US$m)", "Growth", "Margin"],
            ["Subscription", "2,740", "+14.8%", "38.2%"],
            ["Services", "996", "-15.9%", "18.1%"],
            ["Licence", "416", "-22.4%", "27.6%"],
            ["Total", "4,152", "+1.2%", "31.4%"]]}}]},

    {"slideIndex": 5, "layoutId": "conclusion", "contents": [
        _title("What follows"),
        _text("Fund subscription capacity ahead of the FY26 renewal cycle",
              "Stop new services hiring until utilisation recovers")]},
]

# ── 2. Market scan: mixed, with a deliberate spread of exhibit counts ──

MARKET_SCAN = [
    {"slideIndex": 0, "layoutId": "title", "contents": [
        _title("Adjacent market scan"),
        _text("Size, players, growth and what entry would require",
              bullet=False)]},

    {"slideIndex": 1, "layoutId": "content", "contents": [
        _title("The market is smaller than it is described as"),
        _text("Vendor figures count adjacent spend that is not addressable",
              "The serviceable segment is roughly a third of the headline")]},

    {"slideIndex": 2, "layoutId": "chart", "contents": [
        _title("Addressable spend, three definitions"),
        _chart("barh", ["Total market as reported", "Addressable today",
                        "Serviceable with current product"],
               [{"name": "US$bn", "values": [18.4, 7.1, 2.3]}])]},

    # Two exhibits, not four: the spread is the point.
    {"slideIndex": 3, "layoutId": "content",
     "regions": [
         {"id": "left", "kind": "panel", "x": 0.0, "y": 0.0, "w": 0.48, "h": 1.0},
         {"id": "right", "kind": "panel", "x": 0.52, "y": 0.0, "w": 0.48, "h": 1.0},
     ],
     "exhibitPlan": ["Who holds share today?",
                     "Where is the growth going?"],
     "contents": [
        _title("Share is concentrated; growth is not"),
        _bound(_chart("donut", ["Incumbent A", "Incumbent B", "Long tail"],
                      [{"name": "Share", "values": [41.0, 27.0, 32.0]}]), "left"),
        _bound(_chart("line", ["2021", "2022", "2023", "2024", "2025"],
                      [{"name": "Incumbents", "values": [8.1, 8.4, 8.6, 8.7, 8.8]},
                       {"name": "Long tail", "values": [2.0, 2.6, 3.6, 4.9, 6.4]}]),
               "right"),
     ]},

    {"slideIndex": 4, "layoutId": "content", "contents": [
        _title("What entry would require"),
        _diagram("matrix2x2", [
            {"label": "Partner", "detail": "Fast, low control"},
            {"label": "Build", "detail": "Slow, full control"},
            {"label": "Resell", "detail": "Immediate, thin margin"},
            {"label": "Acquire", "detail": "Fast, expensive"},
        ], xAxis="Time to market", yAxis="Control")]},

    {"slideIndex": 5, "layoutId": "conclusion", "contents": [
        _title("Recommendation"),
        _text("Partner into the serviceable segment; revisit build in FY27")]},
]

# ── 3. Technical architecture: structural, so diagrams not charts ──────

TECHNICAL_ARCHITECTURE = [
    {"slideIndex": 0, "layoutId": "title", "contents": [
        _title("Platform architecture"),
        _text("How a request becomes a rendered document", bullet=False)]},

    {"slideIndex": 1, "layoutId": "content", "contents": [
        _title("The request path"),
        _diagram("flow", [
            {"label": "Browser", "detail": "Composes the request"},
            {"label": "Orchestrator", "detail": "Auth and queueing"},
            {"label": "Agent", "detail": "Plans and drafts"},
            {"label": "Renderer", "detail": "Writes the document"},
            {"label": "Storage", "detail": "Serves the file"},
        ])]},

    {"slideIndex": 2, "layoutId": "content", "contents": [
        _title("What sits on what"),
        _diagram("pyramid", [
            {"label": "Product surface", "detail": "What a user touches"},
            {"label": "Agent and tools", "detail": "Where the decisions are"},
            {"label": "Renderer and schema", "detail": "What guarantees the output"},
        ])]},

    {"slideIndex": 3, "layoutId": "content", "contents": [
        _title("Service ownership"),
        _diagram("orgChart", [
            {"label": "Platform"},
            {"label": "Orchestrator", "parent": "Platform"},
            {"label": "Agent service", "parent": "Platform"},
            {"label": "Renderer", "parent": "Platform"},
        ])]},

    {"slideIndex": 4, "layoutId": "content", "contents": [
        _title("Rollout"),
        _diagram("timeline", [
            {"date": "Q1 2026", "label": "Pilot with two teams"},
            {"date": "Q2 2026", "label": "Regional rollout"},
            {"date": "Q3 2026", "label": "General availability"},
        ])]},

    {"slideIndex": 5, "layoutId": "conclusion", "contents": [
        _title("What we are asking for"),
        _text("Sign-off on the Q2 rollout window")]},
]

# ── 4. Deliberately thin: no material, so no exhibits ──────────────────

THIN_BRIEF = [
    {"slideIndex": 0, "layoutId": "title", "contents": [
        _title("Weekly retrospectives"),
        _text("A proposal", bullet=False)]},

    {"slideIndex": 1, "layoutId": "content", "contents": [
        _title("We fix the same things twice"),
        _text("The same integration break has recurred three releases running",
              "Nobody owns the follow-up because nobody writes it down")]},

    {"slideIndex": 2, "layoutId": "content", "contents": [
        _title("What a retrospective would change"),
        _text("One hour a week, with a written owner for each action",
              "Actions carried into the next week's agenda rather than lost")]},

    {"slideIndex": 3, "layoutId": "conclusion", "contents": [
        _title("What we are asking"),
        _text("Try it for six weeks and decide on the evidence")]},
]


@dataclass(frozen=True)
class ExhibitProfile:
    """What a golden deck's exhibits have to look like.

    Stated as ranges and floors rather than exact counts, because a fixture is a
    stand-in for a class of deck and an exact assertion would break on any
    reasonable change to the fixture itself. What must not drift is the
    *character*: figure-dense stays figure-dense, thin stays thin.
    """

    name: str
    brief_key: str
    slides: list = field(default_factory=list)
    min_exhibits: int = 0
    max_exhibits: int | None = None
    min_distinct_chart_kinds: int = 0
    min_distinct_diagram_kinds: int = 0
    # A page carrying two or more exhibits bound to regions.
    requires_composed_page: bool = False
    forbids_composed_page: bool = False
    # How many slides may carry no exhibit at all. A floor, not a ceiling:
    # a text slide is a legitimate slide.
    min_slides_without_exhibit: int = 0
    # Whether every visual slide is allowed to carry the same number of
    # exhibits. False means the deck has to show a spread.
    allows_uniform_counts: bool = True


GOLDEN = (
    ExhibitProfile(
        name="financial_review",
        brief_key="financial_review",
        slides=FINANCIAL_REVIEW,
        min_exhibits=7,
        min_distinct_chart_kinds=4,
        requires_composed_page=True,
        allows_uniform_counts=False,
    ),
    ExhibitProfile(
        name="market_scan",
        brief_key="market_scan",
        slides=MARKET_SCAN,
        min_exhibits=4,
        min_distinct_chart_kinds=3,
        min_distinct_diagram_kinds=1,
        requires_composed_page=True,
        allows_uniform_counts=False,
        min_slides_without_exhibit=2,
    ),
    ExhibitProfile(
        name="technical_architecture",
        brief_key="technical_architecture",
        slides=TECHNICAL_ARCHITECTURE,
        min_exhibits=4,
        min_distinct_chart_kinds=0,
        min_distinct_diagram_kinds=4,
        min_slides_without_exhibit=2,
    ),
    # The important one. Everything here is a ceiling or a floor pointing the
    # other way: this deck must NOT have grown exhibits.
    ExhibitProfile(
        name="thin_brief",
        brief_key="thin_brief",
        slides=THIN_BRIEF,
        min_exhibits=0,
        max_exhibits=0,
        forbids_composed_page=True,
        min_slides_without_exhibit=4,
    ),
)

GOLDEN_BY_NAME = {profile.name: profile for profile in GOLDEN}


def measure(slides: list[dict]) -> dict:
    """The exhibit profile of a deck, as the numbers the assertions read.

    Kept here rather than in the test so the harness and the CI check measure
    the same thing - two readings of "how many exhibits does this deck have"
    would eventually disagree.
    """
    chart_kinds: set[str] = set()
    diagram_kinds: set[str] = set()
    per_slide: list[int] = []
    composed_pages = 0
    exhibits = 0

    for slide in slides or []:
        if not isinstance(slide, dict):
            continue
        on_this_slide = 0
        bound = 0
        for item in slide.get("contents") or []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "")
            if kind == "chart":
                chart = item.get("chart") or {}
                chart_kinds.add(str(chart.get("chartType") or "bar").lower())
            elif kind == "diagram":
                payload = item.get("diagram") or {}
                diagram_kinds.add(str(payload.get("kind") or ""))
            elif kind not in ("table", "metrics", "image"):
                continue
            on_this_slide += 1
            if str(item.get("regionId") or "").strip():
                bound += 1
        exhibits += on_this_slide
        per_slide.append(on_this_slide)
        if bound >= 2:
            composed_pages += 1

    visual = [count for count in per_slide if count]
    return {
        "exhibits": exhibits,
        "chartKinds": sorted(chart_kinds),
        "diagramKinds": sorted(diagram_kinds),
        "perSlide": per_slide,
        "composedPages": composed_pages,
        "slidesWithoutExhibit": sum(1 for count in per_slide if count == 0),
        "uniformCounts": len(set(visual)) <= 1,
    }
