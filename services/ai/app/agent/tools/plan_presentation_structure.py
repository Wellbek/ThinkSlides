"""
Tool: plan_presentation_structure
Plans the overall slide structure and outline based on analyzed content and requirements.
"""

from __future__ import annotations
from app.agent import run_context
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.run_context import require_chat_provider
from app.agent.tools._today import today_line
from app.agent.tools._json_extract import coerce_json_arg, extract_json_object
from app import budget as budget_model
from app.design import deck_format as deck_format_module
from app.measure import archetypes as archetype_model
from app.observability.logging import get_logger
import json
import re
import time

logger = get_logger(__name__)

# A figure in the analysed content is something a slide could be built around,
# which is what separates a deck that can honestly be dense from one that
# cannot. Percentages, currency and plain numbers all count; a year on its own
# does not, because every document has those.
_FIGURE_RE = re.compile(r"(?<!\d)(?!(?:19|20)\d{2}(?!\d))\d[\d,.]*\s?%?")

# How much content depth to plan for, keyed by the composer's detail_level.
_DETAIL_GUIDANCE = {
    "concise": (
        "Each content slide should have 3-4 key points maximum. Favor fewer, "
        "punchier slides over splitting a topic further than it needs."
    ),
    "detailed": (
        "Each content slide should have 4-6 key points. When a topic has more "
        "to say than comfortably fits one slide's points, add another slide "
        "rather than cramming everything in - err toward a few more slides "
        "over thin ones."
    ),
    "comprehensive": (
        "Each content slide should have 5-8 key points, using the twoColumn "
        "contentType to fit them when the layout catalog offers it. Break "
        "large topics into multiple slides freely - a comprehensive deck is "
        "expected to run longer than a concise one, and totalSlides should "
        "reflect that. Lean harder on charts and tables than at lower detail "
        "levels: where a topic has both a narrative and figures behind it, a "
        "comprehensive deck can afford a dedicated chart or table slide "
        "alongside the prose one rather than choosing between them."
    ),
}


# A dashboard answers two to four different questions about one claim. One
# question is an exhibit slide; past four, none of the exhibits is legible at
# the size a quarter-page leaves it.
MIN_EXHIBIT_QUESTIONS = 2
MAX_EXHIBIT_QUESTIONS = 4


def _format_guidance(fmt) -> str:
    """What this format expects of the plan, folded in beside the detail
    guidance and the density guidance it already reads alongside."""
    if fmt.single_page:
        # A poster is not a deck: everything the material has goes on the one
        # page a poster is, so the plan's job changes from pacing several
        # slides to sectioning one.
        return (
            f"\n\nFormat: {fmt.label}. This is a SINGLE PAGE, not a deck - "
            f"totalSlides MUST be 1. Plan its material as sections on that "
            f"one page rather than as separate slides: {fmt.vocabulary}. Use "
            "\"section\" on each slide's single entry only if you genuinely "
            "need more than one contentType block; otherwise fold every "
            "section into the one slide's keyPoints and notes."
        )
    return (
        f"\n\nFormat: {fmt.label} - {fmt.description}. Plan roughly "
        f"{fmt.pages_min}-{fmt.pages_max} slides unless the material clearly "
        f"calls for fewer or more, and favour this page vocabulary: "
        f"{fmt.vocabulary}."
    )


def demote_unearned_dashboards(slides: list) -> list[str]:
    """
    Turn a dashboard with no real exhibit plan back into a chart slide.

    The exhibit plan is what makes the archetype safe to have. A dashboard is
    a promise that this page answers several *different* questions about one
    claim, and the plan is where that promise is checked - at the point the
    page is specified, rather than at render time when the only remaining
    option is to redesign it.

    Three ways the promise fails, all of them producing the same bad page:

    * no plan at all - the slide asked for four exhibits and said nothing
      about what they are for;
    * one question - that is an exhibit slide that used the wrong word;
    * the same question twice - four exhibits saying one thing, which reads
      worse than one exhibit saying it once.

    Demoted rather than rejected, and in place: the slide is still a good
    chart slide, and failing the whole plan over it would throw away the
    fifteen slides around it that were fine. The reason is returned so the
    plan can carry it back to the model.
    """
    problems: list[str] = []
    for slide in slides:
        if not isinstance(slide, dict):
            continue
        if str(slide.get("contentType") or "").strip().lower() != "dashboard":
            continue

        questions = [
            str(q).strip() for q in (slide.get("exhibitPlan") or [])
            if str(q or "").strip()
        ]
        index = slide.get("slideIndex")
        reason = ""
        if len(questions) < MIN_EXHIBIT_QUESTIONS:
            reason = (
                f"it lists {len(questions)} exhibit question(s); a dashboard "
                f"needs at least {MIN_EXHIBIT_QUESTIONS} genuinely different ones"
            )
        elif len(questions) > MAX_EXHIBIT_QUESTIONS:
            reason = (
                f"it lists {len(questions)} exhibit questions; past "
                f"{MAX_EXHIBIT_QUESTIONS} no exhibit is legible at the size a "
                "quarter-page leaves it"
            )
        elif len({q.lower() for q in questions}) != len(questions):
            reason = (
                "two of its exhibit questions are the same, so two of its "
                "exhibits would say one thing"
            )

        if reason:
            slide["contentType"] = "chart"
            slide.pop("exhibitPlan", None)
            problems.append(
                f"Slide {index}: planned as a dashboard, but {reason}. It is "
                "planned as a chart slide instead. Do not re-plan it as a "
                "dashboard by inventing more questions."
            )
        else:
            slide["exhibitPlan"] = questions
    return problems


def _density_guidance(design_system: dict, material: float = 1.0) -> str:
    """
    The template's own density, stated to the planner as a per-kind target.

    Without this the plan is written against a fixed "4-6 key points",
    whatever the template does - so a user uploading a dense analyst deck got
    its colours and its fonts and none of its substance. The figures are
    measured off the template's rendered pages, so they are not a preference
    to argue with.

    Per kind of page rather than one number, because holding a cover and a
    matrix to the same figure produces a deck of uniformly cluttered pages,
    which is a different failure from the one this fixes. And scaled by how
    much material the run actually has: a plan told to fill fifteen cells from
    three paragraphs will invent twelve.
    """
    from app.measure.density import density_brief

    brief = density_brief(design_system)
    if not brief:
        return ""

    content = budget_model.budget_for(design_system, budget_model.CONTENT, material)
    exhibit = budget_model.budget_for(design_system, budget_model.EXHIBIT, material)
    thin = material < 0.5
    lines = [
        f"\n\nDensity target: {brief}.",
        "A block is one countable thing on the page - a bullet, a card, a row "
        "of a table, a labelled statistic, a chart.",
        f"So a content slide should come to roughly {content.blocks_target:.0f} "
        f"blocks and {content.words_target:.0f} words, and a chart or table "
        f"slide to roughly {exhibit.blocks_target:.0f} blocks and "
        f"{exhibit.words_target:.0f} words including the exhibit. A cover or a "
        "section divider carries its headline and nothing else.",
    ]
    if thin:
        lines.append(
            "The material supplied for this deck is thin, and those targets "
            "are already scaled down for it. Plan fewer slides rather than "
            "padding them: a short deck that says something is the right "
            "outcome here, and inventing content to reach a density is not."
        )
    else:
        lines.append(
            "If the target is well above the key-point count below, that is "
            "the target speaking: give those slides more points, or richer "
            "ones, rather than adding more slides. Only where the analysed "
            "content actually supports it - a slide of invented detail is "
            "worse than a sparse one."
        )
    return " ".join(lines)


def _chapter_guidance(design_system: dict) -> str:
    """
    The series this template repeats, offered to the planner to instantiate.

    A template like the reference is not a set of individually designed pages;
    it is one chapter run once per subject, with the same questions asked in
    the same order every time. That repetition is most of what makes such a
    deck read as one document. The planner had no way to say so, so a
    fourteen-subject deck came out as fourteen unrelated runs of invention.
    """
    chapter = archetype_model.chapter_of(design_system)
    if chapter is None or chapter.length < 2:
        return ""
    return (
        f"\n\n{chapter.describe()}\n"
        "If this deck covers several comparable subjects - products, markets, "
        "trends, business units - plan it the same way: repeat that series "
        "once per subject, reusing each position's question verbatim as the "
        "slide title, and set \"chapterOf\" on every slide to the subject it "
        "belongs to. A reader who can tell where they are without reading is "
        "what the pattern buys.\n"
        "Deviate where the material demands it, and say so by leaving the "
        "position out - a subject with no figures cannot fill a chart slide, "
        "and substituting a slide that says something is right where "
        "inventing data to fill the shape is not. Deviation should be a "
        "decision, not the default.\n"
        "If the content is one subject rather than several, ignore the "
        "pattern and plan the deck on its own terms."
    )


def _deck_material(content_summary: str, key_data: dict) -> float:
    """
    How much this run has to say, before any slide is planned.

    The same reading the content designer makes per slide, made once over
    everything the run gathered, so the plan's density and each slide's
    density are scaled by the same judgement rather than two different ones.
    """
    figures = len(_FIGURE_RE.findall(json.dumps(key_data or {})))
    return budget_model.material_score(
        [key for key in (key_data or {}) if key],
        has_exhibit=figures >= 3,
        figures=figures,
        research_findings=len(run_context.get_research_findings() or []),
        source_words=len(str(content_summary or "").split()),
    )


class PlanPresentationStructureTool:
    """Plan the slide structure and outline for a presentation."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="plan_presentation_structure",
            description=(
                "Plan the overall structure and outline of a presentation. "
                "Given content analysis results and requirements, determines the number of slides, "
                "their titles, key points, and suggested layout types. "
                "Returns a structured outline with slide metadata."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="content_summary",
                    type="string",
                    description="Summary of the content to be presented",
                ),
                ToolParameter(
                    name="key_data",
                    type="string",
                    description="JSON string of extracted key data points",
                    required=False,
                ),
                ToolParameter(
                    name="target_slide_count",
                    type="integer",
                    description="Desired number of slides (0 for auto-determine)",
                    required=False,
                ),
                ToolParameter(
                    name="audience",
                    type="string",
                    description="Target audience description",
                    required=False,
                ),
                ToolParameter(
                    name="design_system_summary",
                    type="string",
                    description=(
                        "JSON summary of the design system layout catalog. Optional - "
                        "the system supplies the current design system automatically, "
                        "so you do not need to pass this."
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="detail_level",
                    type="string",
                    description="Content depth: concise, detailed, or comprehensive",
                    required=False,
                ),
                ToolParameter(
                    name="deck_format",
                    type="string",
                    required=False,
                    description=(
                        "What kind of presentation this is (boardroom, pitch, "
                        "academic, dashboard, poster). Supplied automatically; "
                        "you do not need to pass this."
                    ),
                ),
            ],
        )

    @classmethod
    async def run(
        cls,
        content_summary: str,
        key_data: str = "{}",
        target_slide_count: int = 0,
        audience: str = "general",
        design_system_summary: str = "{}",
        detail_level: str = "detailed",
        deck_format: str = "",
    ) -> ToolResult:
        start = time.time()
        try:
            fmt = deck_format_module.by_id(deck_format)
            # The workspace's configured model. There is no environment
            # fallback: a run reaches a tool only after the loop resolved
            # Settings into a provider, and its absence is an error, not a
            # cue to quietly use somebody else's key.
            llm = require_chat_provider()

            # coerce_json_arg, not a bare json.loads: a tool-calling model may
            # send a native JSON object for these despite their declared
            # "string" type, which raises TypeError rather than the
            # JSONDecodeError this used to guard against. Both are
            # supplementary context (not required for the plan to succeed),
            # so a soft {} fallback on genuine failure is fine here.
            parsed_data = coerce_json_arg(key_data, {})
            parsed_ds = coerce_json_arg(design_system_summary, {})

            available_layouts = parsed_ds.get("layouts", [])
            layout_info = ""
            if available_layouts:
                layout_names = [lt.get("layoutName", f"Layout {lt.get('layoutId', '?')}") for lt in available_layouts]
                layout_info = f"\nAvailable layout names in template: {', '.join(layout_names)}"

            detail_guidance = _DETAIL_GUIDANCE.get(detail_level, _DETAIL_GUIDANCE["detailed"])
            density_info = _density_guidance(
                parsed_ds, _deck_material(content_summary, parsed_data)
            )
            chapter_info = _chapter_guidance(parsed_ds)
            format_info = _format_guidance(fmt)

            prompt = f"""{today_line()}

Plan a presentation structure based on the following:

Content Summary:
{content_summary[:6000]}

Key Data: {json.dumps(parsed_data, indent=2)[:2000]}

Target Audience: {audience}
Target Slide Count: {target_slide_count if target_slide_count > 0 else 'auto-determine'}
{layout_info}{density_info}{chapter_info}{format_info}

Return a JSON object with this structure:
{{
  "title": "Presentation Title",
  "totalSlides": <number>,
  "slides": [
    {{
      "slideIndex": 0,
      "title": "Slide Title",
      "section": "Which part of the deck this slide belongs to",
      "titleRegister": "claim|question|label",
      "keyPoints": ["point1", "point2"],
      "suggestedLayout": "Title Slide",
      "contentType": "title|content|twoColumn|image|table|chart|dashboard|conclusion",
      "exhibitPlan": ["Only on a dashboard slide: one QUESTION per intended exhibit, 2-4 of them"],
      "chapterOf": "The subject this slide belongs to, when the deck repeats a chapter per subject - otherwise omit",
      "notes": "Any additional notes for content generation"
    }}
  ],
  "deviations": ["Only when this plan does not do exactly what was asked - see the rule below. Omit or leave empty otherwise."]
}}

Rules:
- Before anything else, check this plan against every explicit, checkable instruction above (a stated count, "one per X", a specific list of items to cover, an explicit exclusion) - not the general topic, the concrete asks. If the plan cannot satisfy one (the content summary does not actually contain enough distinct items for the count asked), or you are deliberately adding slides or scope nobody asked for, name it plainly in "deviations": what was asked, what you planned instead, and why. An empty list is a claim that the plan matches what was asked, not a default - leave it empty only when that is actually true. This is shown to the user before the deck is built, so a real mismatch named here is a redirect; the same mismatch unsaid is the user finding out only once it is rendered.
- First slide should be a title slide (contentType "title") - it carries only a title and an optional subtitle, no body bullets, so give it 0-1 key points.
- Last slide should be a conclusion/summary slide. If it recaps points (3-5 key points), give it contentType "content" so the points render in a body placeholder - only use contentType "conclusion" (title-only) for a slide with no body content.
- Every slide that has key points MUST use a contentType whose layout has a body placeholder: "content" (one body), "twoColumn" (two bodies), "chart", "table", or "image". Never assign 3-5 key points to a "title" or "conclusion" slide - those layouts have no body placeholder and the points would be dropped.
- Give every slide a "titleRegister", which says what its headline is FOR:
  * "claim" - the slide carries a finding and its headline states it: "Applied AI recorded the highest innovation score of all 14 trends". A reader who reads only the claim headlines of a deck has read its argument. Use this for any slide backed by a chart, a table, figures or a comparison.
  * "question" - the headline asks something the slide answers, repeated in the same form across comparable slides. Use this when the deck has a repeating structure ("Why should leaders pay attention?" asked of every subject).
  * "label" - the headline names the topic. Correct for a cover, a section break, a divider, and for any slide that genuinely has no finding to state.
  Do NOT mark a slide "claim" unless it has something to state a finding FROM. A confident headline over a slide with no figures is the deck lying about its own evidence, and it is rejected at validation.
- Give every slide a "section": the part of the deck it belongs to, named in two or three words and REPEATED verbatim across every slide in that part. Sections are what let a reader tell where they are in the deck, and they are printed as a small eyebrow on each slide - so "Market context" on four consecutive slides is right, and four differently-worded variations of it is not. A deck short enough to have no parts can use one section for all of them.
- Distribute content evenly across slides
- Judge every slide against the data behind it, not just its word count. If a slide's key points are carried by numbers - a trend over time, a comparison across categories, a breakdown of a whole - give it contentType "chart". If they are carried by structured rows and columns, give it contentType "table". A deck that states figures only as prose bullets when a chart would show them is a worse deck at every level of detail.
- KPIs, metrics, financial results, growth rates, targets vs actuals and year-on-year figures are chart material by default. If the user asked for KPIs or metrics, or the key data is a set of numbers, at least one slide MUST have contentType "chart" - returning a deck of prose bullets for a numbers request is a failure, not a stylistic choice. A "chart" slide still keeps its keyPoints: they become the short interpretation alongside the visual.
- Do not manufacture visuals. Only propose "chart" or "table" where the analyzed content genuinely supplies the underlying numbers or rows; a topic with no real data should stay prose.
- A "dashboard" slide carries SEVERAL exhibits on one page - a figure strip, a chart, a second chart of a different kind, a small table. Use it only where one slide has to answer two to four genuinely DIFFERENT questions about the same claim, and the material answers all of them. Give it an "exhibitPlan": one question per intended exhibit, in the order they should be read. For example, for "Revenue held flat but the mix moved": ["What are the headline figures?", "Where did the movement come from?", "What changed underneath the flat total?", "Does it hold across regions?"] - a bridge, a mix chart and a set of regional trends each answer a different one.
- Most slides are NOT dashboards. A dashboard is the exception a page earns, and the test is the exhibit plan: if two of its questions would be answered by the same exhibit, or if any question has no figures behind it, the slide is an ordinary "chart" or "content" slide. Four exhibits saying the same thing read worse than one saying it once, and an exhibit built from figures the material does not have is the worst outcome available - a chart makes a number look sourced.
- {detail_guidance}
- Choose suggestedLayout names from the available layouts if a template is provided"""

            response = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
            )

            result_text = response.get("message", {}).get("content", "{}")
            structure = extract_json_object(result_text)
            if structure is None or not structure.get("slides"):
                # Fail loudly rather than silently proceeding with a
                # {"raw_plan": ...} object that has no totalSlides/slides -
                # every downstream step depends on this structure existing.
                return ToolResult(
                    success=False,
                    error=(
                        "Could not parse the model's response as a presentation "
                        f"structure (response started with: {result_text[:200]!r}). "
                        "Retry and respond with ONLY the JSON object - no prose "
                        "before or after it - including a non-empty 'slides' array."
                    ),
                    duration_ms=int((time.time() - start) * 1000),
                )

            duration_ms = int((time.time() - start) * 1000)
            if fmt.single_page and int(structure.get("totalSlides") or 0) != 1:
                # A single-page format that came back as a multi-slide plan
                # is the model treating it as a deck anyway. Named rather
                # than silently corrected: merging the extra slides' material
                # down to one page is exactly the work issue #285 tracks -
                # doing it here, invisibly, would hide a gap that is not
                # actually closed yet.
                structure.setdefault("deviations", []).append(
                    f"{fmt.label} is a single page, but the plan carries "
                    f"{structure.get('totalSlides')} slides."
                )
                logger.warning("single_page_format_multi_slide_plan",
                               format_id=fmt.id,
                               total_slides=structure.get("totalSlides"))
            demotions = demote_unearned_dashboards(structure.get("slides") or [])
            if demotions:
                structure["dashboardWarnings"] = demotions
                logger.info("dashboards_demoted", problems=demotions[:3])
            # A pattern instantiated inconsistently has the cost of a pattern
            # and none of the benefit, and it is the kind of thing nobody
            # notices until slide forty. The plan carries the finding back to
            # the model rather than being rejected over it: a deviation can be
            # the right call, and only the material knows.
            inconsistencies = archetype_model.check_consistency(
                structure.get("slides") or []
            )
            if inconsistencies:
                structure["chapterWarnings"] = inconsistencies
                logger.info("chapter_pattern_inconsistent",
                            problems=inconsistencies[:3])
            logger.info(
                "presentation_structure_planned",
                slide_count=structure.get("totalSlides", 0),
                chapters=len({
                    str(s.get("chapterOf") or "") for s in structure.get("slides") or []
                    if isinstance(s, dict) and s.get("chapterOf")
                }),
                duration_ms=duration_ms,
            )

            return ToolResult(
                success=True,
                data=structure,
                duration_ms=duration_ms,
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e), duration_ms=int((time.time() - start) * 1000))