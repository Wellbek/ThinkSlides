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
        usable = [language for language in languages
                  if _derives(language, canvas, floor_pt)]
        if len(usable) > len(best):
            best = usable

        problems = []
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


def _derives(language: Language, canvas: Canvas, floor_pt: float) -> bool:
    """Whether this language becomes a pack that would actually render.

    The check is the derivation itself: a language whose ramp or measure
    cannot produce placeable frames fails here, where it costs a retry, rather
    than at render, where it costs the deck.
    """
    try:
        pack = ensure(to_pack(language, canvas, floor_pt=floor_pt))
    except Exception as exc:
        logger.info("authored_language_underivable",
                    name=language.name, error=str(exc))
        return False
    if not pack.archetypes or not pack.type_roles:
        return False
    if not _carries_an_exhibit(language):
        # A deck argues from charts and tables, and they go in figure slots.
        # A language with none composed its cover and its prose pages and
        # then had nowhere to put the graph, so the run failed at the third
        # page with "no archetype can carry a page of 103 characters" - which
        # names the paragraph count and not the actual problem. Caught here,
        # the model is told what is missing and writes a page for it.
        logger.info("authored_language_has_no_exhibit_page",
                    name=language.name)
        return False
    return True


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
