"""
One run's design languages, authored and checked.

The call itself, kept apart from the tool that offers the result so the
authoring can be exercised without a pause, a preview or a chat session.

Retried once, with the reason the first set failed. A model told only "try
again" tries the same thing again; told "every language uses six columns" it
changes the measure - and the difference between those two prompts is the
difference between three arrangements of one idea and three designs.
"""

from __future__ import annotations

from typing import Optional, Sequence

from app.agent.tools._json_extract import extract_json_object
from app.design import authoring, deck_format as deck_format_module
from app.design.authored import Language, to_pack
from app.design.deck_format import DeckFormat
from app.design.schema import Canvas
from app.design.vocabulary import ensure
from app.observability.logging import get_logger

logger = get_logger(__name__)

MAX_ATTEMPTS = 2


async def author(llm, brief: str, canvas: Canvas, *, answers: str = "",
                 wanted: int = authoring.WANTED,
                 fmt: Optional[DeckFormat] = None,
                 ) -> tuple[list[Language], str]:
    """The languages this deck should be offered, and why if there are none.

    A language that will not derive into a usable pack is dropped here rather
    than offered and discovered later: the gates that reject a badly recovered
    language are the same ones that reject a badly invented one, and running
    them now is what keeps "the agent designs it" from meaning "the agent gets
    to skip the checks".

    ``fmt`` is what kind of presentation this is - a pitch, a poster, a
    dashboard. It shapes the prompt (what the deck is designing for) and the
    derivation floor (``fmt.floor_pt``), so a language rejected here is
    rejected against the same standard the deck will actually be held to.
    """
    fmt = fmt or deck_format_module.by_id(None)
    problems: list[str] = []
    best: list[Language] = []
    floor_pt = fmt.floor_pt(canvas)

    for attempt in range(MAX_ATTEMPTS):
        text = authoring.prompt(brief, answers, wanted, fmt=fmt)
        if problems:
            text += ("\n\nThe last set was rejected:\n"
                     + "\n".join(f"- {p}" for p in problems)
                     + "\nDesign a different set that does not repeat that.")
        try:
            response = await llm.chat(
                messages=[{"role": "user", "content": text}], tools=None)
        except Exception as exc:
            # A provider falling over must not fail the run. The caller falls
            # back to whatever it had before this existed.
            logger.warning("design_authoring_failed", error=str(exc))
            return best, f"the model could not be reached ({exc})"

        payload = extract_json_object(
            (response.get("message") or {}).get("content", "{}")) or {}
        languages = authoring.read(payload)
        verdicts = {language.name: _problem(language, canvas, floor_pt)
                    for language in languages}
        usable = [language for language in languages
                  if not verdicts[language.name]]
        if len(usable) > len(best):
            best = usable

        problems = [f"{name}: {why}" for name, why in verdicts.items()
                    if why and why != NO_EXHIBIT_PAGE]
        no_exhibit = [language.name for language in languages
                      if language not in usable
                      and not _carries_an_exhibit(language)]
        if no_exhibit:
            # Named specifically. "Try again" gets the same set back; "none
            # of your content pages has a figure slot" gets a page for the
            # deck's charts to go on.
            problems.append(
                f"{', '.join(no_exhibit)} declared no content page with a "
                "figure slot, so the deck's charts and tables would have "
                "nowhere to go")
        if not usable:
            problems.append("no language in that set could be used")
        elif len(usable) < wanted:
            problems.append(
                f"only {len(usable)} of {wanted} languages were usable")
        sameness = authoring.differs_enough(usable)
        if sameness:
            problems.append(sameness)
        if not problems:
            logger.info("design_languages_authored", attempt=attempt + 1,
                        names=[language.name for language in usable])
            return usable[:wanted], ""

    logger.info("design_languages_partial",
                kept=len(best), problems=problems)
    return best[:wanted], "; ".join(problems)


NO_EXHIBIT_PAGE = "declared no content page with a figure slot"


def _derives(language: Language, canvas: Canvas, floor_pt: float) -> bool:
    """Whether this language becomes a pack that would actually render."""
    return not _problem(language, canvas, floor_pt)


def _problem(language: Language, canvas: Canvas, floor_pt: float) -> str:
    """Why this language cannot be offered, or "" when it can.

    The gate is the derivation and a compile of every page it will be asked
    to set, run here, where a failure costs a retry, rather than at render,
    where it cost the deck (#306, #351). Each answer names what to change,
    because it is read back to the model: "try again" gets the same set back.
    """
    try:
        pack = ensure(to_pack(language, canvas, floor_pt=floor_pt))
    except Exception as exc:
        logger.info("authored_language_underivable",
                    name=language.name, error=str(exc))
        return f"could not be derived into pages ({exc})"
    if not pack.archetypes or not pack.type_roles:
        return "derived into no pages"
    if not _carries_an_exhibit(language):
        # A deck argues from charts and tables, and they go in figure slots.
        # A language with none composed its cover and its prose pages and
        # then had nowhere to put the graph, so the run failed at the third
        # page with "no archetype can carry a page of 103 characters" - which
        # names the paragraph count and not the actual problem. Caught here,
        # the model is told what is missing and writes a page for it.
        logger.info("authored_language_has_no_exhibit_page",
                    name=language.name)
        return NO_EXHIBIT_PAGE
    problem = compile_problem(pack)
    if problem:
        logger.info("authored_language_does_not_compile",
                    name=language.name, problem=problem)
    return problem


# What a language is compiled against before it is offered: every role a deck
# has, a page of prose, and the two exhibits a deck most often argues from, at
# the size they usually come. Five rows of four short columns is the median
# comparison table in the benchmark runs; smaller than that proves nothing.
PROBE_TABLE = [["Option", "Cost", "Risk", "Best for"],
               *[[f"Option {n}", "Moderate spare capacity",
                  "Partial exposure on rollback", "Routine releases"]
                 for n in range(1, 6)]]
PROBE_CHART = {"type": "bar", "categories": ["2023", "2024", "2025", "2026"],
               "series": [{"name": "Share", "values": [12, 18, 25, 31]}]}


def _probe_slides() -> list[dict]:
    def text(line: str) -> dict:
        return {"type": "text", "runs": [{"text": line}]}

    prose = ("The platform team ships most changes as routine releases, and "
             "a smaller number carry enough risk to need staged exposure.")
    return [
        {"slideIndex": 0, "role": "cover", "contents": [
            text("A deck title of ordinary length"), text(prose)]},
        {"slideIndex": 1, "role": "section", "contents": [
            text("A section divider")]},
        {"slideIndex": 2, "role": "content", "contents": [
            text("A headline that states the page's finding"), text(prose),
            text(prose)]},
        {"slideIndex": 3, "role": "content", "contents": [
            text("A comparison set as a table"), text(prose),
            {"type": "table", "table": {"headerRow": True, "rows": PROBE_TABLE}}]},
        {"slideIndex": 4, "role": "content", "contents": [
            text("A trend set as a chart"), text(prose),
            {"type": "chart", "chart": PROBE_CHART}]},
        {"slideIndex": 5, "role": "closing", "contents": [
            text("What to do next"), text(prose)]},
    ]


def compile_problem(pack) -> str:
    """What stops this pack setting a deck, in words a designer can act on.

    Three checks, cheapest first:
    - every arrangement's own frames are sound geometry, with and without
      anything in them - a cover whose frames overlapped at equal depth was
      refused at render and shipped nothing (#306);
    - some content arrangement shows a table and a chart legibly - a language
      whose largest figure slot was an inch and a half tall set every table
      as a sliver (#351);
    - a deck of every role composes and passes the same geometry check the
      renderer runs.
    """
    from app.design.geometry import check_page
    from app.design.material import MaterialItem
    from app.design.run import Unplaceable, compose
    from app.design.schema import DesignDoc, Page, PackRef
    from app.design.select import (
        _accepts, _slot_rect, exhibit_floor_in,
        readable_somewhere,
    )
    from app.design.vocabulary import PLAIN_ID

    shell = DesignDoc(pack=PackRef(id=pack.id, version=pack.version),
                      canvas=pack.canvas, grid=pack.grid, pages=())
    for archetype in pack.archetypes:
        bare = Page(id="probe", archetype=archetype.id, role=archetype.role,
                    frames=archetype.frames)
        broken = check_page(shell, bare)
        if broken:
            return (f"its {archetype.label or archetype.id} page is unsound "
                    f"geometry: {broken[0]}")

    pages = [a for a in pack.archetypes if a.role in ("content", "exhibit")]
    for kind, payload in (("table", {"table": {"headerRow": True,
                                               "rows": PROBE_TABLE}}),
                          ("chart", {"chart": PROBE_CHART})):
        item = MaterialItem(id=f"probe-{kind}", kind=kind, payload=payload)
        if not readable_somewhere(item, pack, pages):
            sizes = [
                _slot_rect(a, slot, pack, a.grid or pack.grid)
                for a in pages for slot in a.slots if _accepts(slot, item)
            ]
            sizes = [s for s in sizes if s]
            tallest = max(sizes, key=lambda s: s[1]) if sizes else (0.0, 0.0)
            need = exhibit_floor_in(item, pack, tallest[0] or None)
            return (f"no content page gives a {kind} room to be read: its "
                    f"tallest figure slot is {tallest[1]:.1f}in by "
                    f"{tallest[0]:.1f}in, and a five-row {kind} needs about "
                    f"{need:.1f}in of height at that width. Give at least one "
                    f"content page a figure slot that tall - fewer fixed "
                    f"slots above and below it, or a figure beside the prose "
                    f"rather than under it")

    try:
        doc = compose(_probe_slides(), pack, title="probe")
    except Unplaceable as exc:
        return f"a deck of every page kind could not be composed ({exc})"
    from app.design.geometry import check_doc

    broken = check_doc(doc)
    if broken:
        return f"a composed page is unsound geometry: {broken[0]}"
    plain = sorted({page.provenance.source_slide + 1 for page in doc.pages
                    if page.archetype == PLAIN_ID
                    and page.provenance.source_slide is not None})
    if plain:
        # Placed, but only on the fallback page: the language itself has no
        # page for that material, which is what this gate exists to find.
        return (f"none of its own pages could set probe page(s) "
                f"{', '.join(map(str, plain))} of a deck of every page kind")
    return ""


def _carries_an_exhibit(language: Language) -> bool:
    """Whether any content page of this language can hold a chart."""
    return any(
        slot.kind == "figure"
        for page in language.pages
        if page.role in ("content", "exhibit")
        for slot in page.slots
    )


def packs_for(languages: Sequence[Language], canvas: Canvas,
             fmt: Optional[DeckFormat] = None) -> list:
    """Each language as the pack a deck would actually be composed against."""
    floor_pt = (fmt or deck_format_module.by_id(None)).floor_pt(canvas)
    out = []
    for language in languages:
        try:
            out.append(ensure(to_pack(language, canvas, floor_pt=floor_pt)))
        except Exception as exc:   # already filtered, but never fail a run
            logger.warning("authored_pack_failed", name=language.name,
                           error=str(exc))
    return out


def summary(language: Language) -> str:
    """One line describing a language, for a user choosing between them."""
    ramp = language.ratio("headline")
    loudness = "loud" if ramp >= 2.6 else "quiet" if ramp <= 1.8 else "measured"
    return (f"{language.columns} columns, {loudness} headline, "
            f"{language.density} pages, {language.display_face} over "
            f"{language.body_face}")


def describe(languages: Sequence[Language]) -> list[dict]:
    """The languages as the shape an interface offers a choice between."""
    return [
        {
            "id": _slug(language.name),
            "name": language.name,
            "rationale": language.rationale,
            "summary": summary(language),
            "accent": language.accent,
            "ground": language.ground,
            "pages": [page.name for page in language.pages],
        }
        for language in languages
    ]


def _slug(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in name)
    return "-".join(part for part in cleaned.split("-") if part) or "language"


def find(languages: Sequence[Language], chosen: str) -> Optional[Language]:
    """The language a user picked, by id or by name."""
    wanted = (chosen or "").strip().lower()
    for language in languages:
        if wanted in (_slug(language.name), language.name.lower()):
            return language
    return None
