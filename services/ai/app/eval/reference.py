"""
The reference deck's measured profile, as a committed fixture.

The reference PDF is a third-party publication and is not in the repository,
so the harness cannot depend on it being present. What is committed is the
*measurement* - the design-set and validation-set profiles produced by
``page_metrics`` and ``deck_metrics`` - which is what scoring actually needs
and which keeps the suite offline and deterministic.

Regenerate with::

    python -m app.eval build-reference

with the PDF either at ``$THINKSLIDES_REFERENCE_PDF`` or in ~/Downloads. The
build measures the validation pages without ever rendering them for a human
to look at, which is how the split survives contact with the tooling.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from app.eval import corpus
from app.measure import archetypes as archetype_model
from app.measure import clusters, grammar
from app.measure.deck_metrics import DeckProfile, profile_pdf
from app.measure.density import DensityProfile, TypeProfile
from app.measure.density import profile_pdf as profile_density
from app.measure.structure import count_blocks_in_pdf

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
REFERENCE_PROFILE_PATH = os.path.join(FIXTURE_DIR, "reference_profile.json")

DESIGN_SPLIT = "design"
VALIDATION_SPLIT = "validation"


def load_reference_profile(split: str = VALIDATION_SPLIT) -> DeckProfile:
    """The committed profile for one half of the corpus split."""
    with open(REFERENCE_PROFILE_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    if split not in data.get("profiles", {}):
        raise KeyError(f"no {split!r} profile in {REFERENCE_PROFILE_PATH}")
    return DeckProfile.from_dict(data["profiles"][split])


def load_reference_density(
    split: str = DESIGN_SPLIT,
) -> tuple[DensityProfile, TypeProfile]:
    """
    The reference's density budget and type ramp, as committed.

    Separate from ``DeckProfile`` because it is measured differently: blocks
    here are counted by ``measure.structure``, which reads the page's grid,
    rather than by the line clusterer ``page_metrics`` uses for the broad
    shape of a page. The budget a planner is held to has to be the stricter
    of the two, or the deck is written to one number and scored on another.
    """
    with open(REFERENCE_PROFILE_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    section = (data.get("density") or {}).get(split)
    if section is None:
        raise KeyError(f"no {split!r} density in {REFERENCE_PROFILE_PATH}")
    return (DensityProfile.from_dict(section.get("density")),
            TypeProfile.from_dict(section.get("type")))


def reference_block_counts() -> dict[int, int]:
    """The grid-aware block count for each design page, as committed."""
    with open(REFERENCE_PROFILE_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    return {int(page): int(count)
            for page, count in (data.get("blocksByDesignPage") or {}).items()}


def load_reference_survey() -> clusters.TemplateSurvey:
    """The reference deck's layout survey, as committed."""
    with open(REFERENCE_PROFILE_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    survey = data.get("pageSurvey") or {}
    return clusters.TemplateSurvey(
        page_count=int(survey.get("pageCount") or 0),
        considered=tuple(survey.get("pagesConsidered") or ()),
        sample=tuple(survey.get("sampledPages") or ()),
        clusters=tuple(
            clusters.PageCluster(
                id=int(entry.get("id", index)),
                label=str(entry.get("label") or "unknown"),
                pages=tuple(entry.get("pages") or ()),
                representative=int(entry.get("representative") or 0),
            )
            for index, entry in enumerate(survey.get("clusters") or [])
        ),
    )


def load_archetypes() -> archetype_model.TemplateArchetypes:
    """The reference's named archetypes and its chapter, as committed."""
    with open(REFERENCE_PROFILE_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    return archetype_model.TemplateArchetypes.from_dict(data.get("archetypes"))


def load_design_grammar() -> grammar.TemplateGrammar:
    """The region grammar read from the reference's design pages, as committed."""
    with open(REFERENCE_PROFILE_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    return grammar.TemplateGrammar.from_dict(data.get("designGrammar"))


def reference_metadata() -> dict:
    with open(REFERENCE_PROFILE_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    return {k: v for k, v in data.items()
            if k not in ("profiles", "density", "blocksByDesignPage",
                         "pageSurvey", "designGrammar",
                         "archetypes")}


def build_reference_profile(pdf_path: Optional[str] = None) -> str:
    """Measure the reference deck and write the committed fixture."""
    corpus.assert_split_is_disjoint()
    path = pdf_path or corpus.find_reference_pdf()
    if not path:
        raise FileNotFoundError(
            "reference PDF not found - set "
            f"{corpus.REFERENCE_PDF_ENV} or place it in ~/Downloads"
        )

    profiles = {
        DESIGN_SPLIT: profile_pdf(path, "reference-design", corpus.DESIGN_PAGES),
        VALIDATION_SPLIT: profile_pdf(
            path, "reference-validation", corpus.active_validation_pages()
        ),
    }
    density = {
        DESIGN_SPLIT: profile_density(path, corpus.DESIGN_PAGES),
        VALIDATION_SPLIT: profile_density(path, corpus.active_validation_pages()),
    }
    # Per-page, for the design set only. The block counter is the piece of
    # measurement most easily fooled, and a per-page figure is what lets the
    # suite check it against counts made by eye without the PDF present.
    structures = count_blocks_in_pdf(path, corpus.DESIGN_PAGES)
    blocks_by_page = {
        str(page): structure.block_count
        for page, structure in zip(corpus.DESIGN_PAGES, structures)
    }
    # The layout survey of the whole deck. Every page is read, which is the
    # point - the survey exists to find archetypes that even sampling misses,
    # and holding out pages from it would defeat that. Nothing here reveals a
    # validation page's *content*: what is committed is which pages group
    # together and what kind of page each group is.
    survey = clusters.survey_pdf(path)
    # The region grammar of the design pages. Design pages only: this is the
    # half of the corpus the extractor is *tuned* against, and committing the
    # grammar of a held-out page would be reading it.
    design_grammar = grammar.read_pdf(path, corpus.DESIGN_PAGES)
    # The archetypes and the chapter, read over the whole deck - the
    # pattern only exists across all of it. What is committed is the
    # *shape* of the chapter, not the pages it was read from.
    survey_grammar = grammar.read_pdf(
        path, [c.representative for c in survey.clusters]
    )
    named = archetype_model.read_pdf(path, survey, survey_grammar)
    payload = {
        "source": os.path.basename(path),
        "pageCount": corpus.REFERENCE_PAGE_COUNT,
        "pageSizePt": list(corpus.REFERENCE_PAGE_SIZE_PT),
        "designPages": list(corpus.DESIGN_PAGES),
        "validationPages": list(corpus.active_validation_pages()),
        "profiles": {name: profile.to_dict() for name, profile in profiles.items()},
        "density": {
            name: {"density": measured.to_dict(), "type": typed.to_dict()}
            for name, (measured, typed) in density.items()
        },
        "blocksByDesignPage": blocks_by_page,
        "pageSurvey": survey.to_dict(),
        "designGrammar": design_grammar.to_dict(),
        "archetypes": named.to_dict(),
    }
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(REFERENCE_PROFILE_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return REFERENCE_PROFILE_PATH
