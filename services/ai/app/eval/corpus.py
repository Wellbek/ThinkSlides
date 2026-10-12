"""
The reference corpus and its split, encoded rather than left to convention.

The reference deck for the consultancy-grade epic is McKinsey's *Technology
Trends Outlook 2022*, 184 pages at 960 x 540 pt - exactly the 13.333 x 7.5in
canvas ``design_system.get_default_design_system`` already declares, so
geometry read off it transfers to our renderer 1:1.

The split is the whole point of the harness. Design-set pages were read while
the epic was written and may be inspected freely during implementation.
Validation-set pages are reserved for scoring and must never be looked at -
otherwise the harness measures memorisation rather than generalisation.

``assert_design_page`` is the guard: any code path that renders a reference
page for a human to look at goes through it, so peeking at a validation page
raises instead of quietly succeeding. Retiring a validation page (because it
had to be opened) is a deliberate edit to ``RETIRED_VALIDATION_PAGES`` with a
replacement, not a silent reuse.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# 1-based page numbers, as printed on the pages themselves.
DESIGN_PAGES: tuple[int, ...] = (1, 3, 4, 7, 12, 22, 30, 40, 66, 95, 110, 130, 168)
VALIDATION_PAGES: tuple[int, ...] = (18, 35, 52, 74, 89, 103, 118, 137, 152, 175)

# Validation pages that had to be opened and are therefore no longer held out.
# Add a replacement to VALIDATION_PAGES in the same commit that retires one.
RETIRED_VALIDATION_PAGES: tuple[int, ...] = ()

REFERENCE_PAGE_COUNT = 184
REFERENCE_PAGE_SIZE_PT = (960.0, 540.0)

# The reference PDF is a third-party publication and is deliberately not
# committed. The harness runs off the measured profile in ``fixtures/``; the
# PDF is only needed to regenerate that profile.
REFERENCE_PDF_ENV = "THINKSLIDES_REFERENCE_PDF"
_DEFAULT_REFERENCE_PDF_NAMES = (
    "mckinsey-tech-trends-outlook-2022-full-report.pdf",
)


class ValidationPeek(AssertionError):
    """Raised when something tries to display a held-out validation page."""


def active_validation_pages() -> tuple[int, ...]:
    """Validation pages still held out, i.e. minus anything retired."""
    retired = set(RETIRED_VALIDATION_PAGES)
    return tuple(p for p in VALIDATION_PAGES if p not in retired)


def assert_split_is_disjoint() -> None:
    """The two sets must not overlap, or the split means nothing."""
    overlap = set(DESIGN_PAGES) & set(VALIDATION_PAGES)
    if overlap:
        raise AssertionError(f"pages are in both halves of the split: {sorted(overlap)}")
    out_of_range = [
        p for p in (*DESIGN_PAGES, *VALIDATION_PAGES)
        if not 1 <= p <= REFERENCE_PAGE_COUNT
    ]
    if out_of_range:
        raise AssertionError(f"pages outside the reference deck: {sorted(out_of_range)}")


def assert_design_page(page_number: int) -> None:
    """Guard a path that would show a reference page to a human."""
    if page_number in active_validation_pages():
        raise ValidationPeek(
            f"page {page_number} is in the held-out validation set. Score against it, "
            f"do not look at it. If it genuinely has to be opened, retire it in "
            f"corpus.RETIRED_VALIDATION_PAGES and add a replacement."
        )


def find_reference_pdf() -> str | None:
    """Locate the reference PDF, or None when it is not on this machine."""
    from_env = os.environ.get(REFERENCE_PDF_ENV)
    if from_env and os.path.exists(from_env):
        return from_env

    search_dirs = [
        os.getcwd(),
        os.path.expanduser("~/Downloads"),
        os.path.expanduser("~"),
    ]
    for directory in search_dirs:
        for name in _DEFAULT_REFERENCE_PDF_NAMES:
            candidate = os.path.join(directory, name)
            if os.path.exists(candidate):
                return candidate
    return None


# --------------------------------------------------------------------------
# The exemplar library
# --------------------------------------------------------------------------
#
# One reference cannot represent "publication grade". Measured side by side,
# three decks that are all unarguably excellent disagree on nearly every
# property the harness scores:
#
#     words / page     EY 305   WTI 632   BXSL 407   McKinsey 204
#     charts / page    EY 0.14  WTI 0.93  BXSL 1.91  McKinsey 0.60
#
# So a single profile does not describe the target - it describes one house.
# Scoring every generated deck against it steered every deck toward that
# house, which is the opposite of being able to produce a wide range.
#
# These are exemplars, not buckets. Nothing sorts a deck into one by its
# subject: an investor deck rendered in an editorial register is a perfectly
# good investor deck. What the library gives the scorecard is a *spread* of
# what good looks like, so "how close is this to the nearest thing we consider
# excellent" becomes answerable, and a deck is allowed to resemble none of
# them and still be right.


@dataclass(frozen=True)
class Exemplar:
    """One reference publication, and which of its pages may be looked at."""

    id: str
    title: str
    filenames: tuple[str, ...]
    page_count: int
    page_size_pt: tuple[float, float]
    design_pages: tuple[int, ...]
    # Empty when the whole deck has been read and nothing can honestly be
    # held out - see WTI below. An exemplar with no validation set still
    # scores; it just cannot claim to be measuring generalisation.
    validation_pages: tuple[int, ...] = ()
    note: str = ""
    retired_validation_pages: tuple[int, ...] = field(default=())

    def active_validation_pages(self) -> tuple[int, ...]:
        retired = set(self.retired_validation_pages)
        return tuple(p for p in self.validation_pages if p not in retired)


EXEMPLARS: tuple[Exemplar, ...] = (
    Exemplar(
        id="ey-iif-risk",
        title="EY/IIF Global Insurance Risk Management Survey (March 2023)",
        filenames=("ey-inaugural-ey-iif-global-insurance-risk-management-survey-march23.pdf",),
        page_count=42,
        page_size_pt=(1920.0, 1080.0),
        design_pages=(4, 8, 12, 16, 20),
        validation_pages=(24, 28, 32, 36, 40),
        note="Editorial consulting report. Section-labelled openers with the "
             "assertion in a pull-quote beside them, so its informative-title "
             "share is genuinely low - that is the house, not a bad deck.",
    ),
    Exemplar(
        id="bxsl-investor",
        title="Blackstone Secured Lending Fund 1Q26 Investor Presentation",
        filenames=("BXSL-1Q26-Investor-Presentation.pdf",),
        page_count=42,
        page_size_pt=(720.0, 540.0),
        design_pages=(4, 8, 12, 16, 20),
        validation_pages=(24, 28, 32, 36, 40),
        note="Dense 4:3 investor deck: nearly two charts a page, a full "
             "footnote apparatus, and an attribution line on most pages.",
    ),
    Exemplar(
        id="wti-2025",
        title="Microsoft Work Trend Index 2025: The Year the Frontier Firm Is Born",
        filenames=("WTI-2025-04-The-Year-the-Frontier-v13_68535917c7c2a.pdf",),
        page_count=15,
        page_size_pt=(1224.0, 792.0),
        design_pages=(4, 8, 12),
        validation_pages=(),
        note="Magazine register, and design-set only on purpose: the whole "
             "deck was read while the headline and attribution detectors were "
             "being fixed, so no page of it can honestly be held out.",
    ),
)

EXEMPLARS_BY_ID = {exemplar.id: exemplar for exemplar in EXEMPLARS}


def exemplar(exemplar_id: str) -> Exemplar:
    try:
        return EXEMPLARS_BY_ID[exemplar_id]
    except KeyError:
        known = ", ".join(sorted(EXEMPLARS_BY_ID))
        raise KeyError(f"unknown exemplar {exemplar_id!r} - known: {known}") from None


def assert_exemplar_splits_are_disjoint() -> None:
    """The same guarantee ``assert_split_is_disjoint`` gives the primary
    reference, for every exemplar in the library."""
    for entry in EXEMPLARS:
        overlap = set(entry.design_pages) & set(entry.validation_pages)
        if overlap:
            raise AssertionError(
                f"{entry.id}: pages are in both halves of the split: {sorted(overlap)}"
            )
        out_of_range = [
            p for p in (*entry.design_pages, *entry.validation_pages)
            if not 1 <= p <= entry.page_count
        ]
        if out_of_range:
            raise AssertionError(
                f"{entry.id}: pages outside the deck: {sorted(out_of_range)}"
            )


def assert_exemplar_design_page(exemplar_id: str, page_number: int) -> None:
    """Guard a path that would show one of an exemplar's pages to a human."""
    entry = exemplar(exemplar_id)
    if page_number in entry.active_validation_pages():
        raise ValidationPeek(
            f"{exemplar_id} page {page_number} is in the held-out validation set. "
            f"Score against it, do not look at it."
        )


def find_exemplar_pdf(exemplar_id: str) -> str | None:
    """Locate an exemplar's PDF, or None when it is not on this machine.

    Same rule as the primary reference: these are third-party publications and
    are deliberately not committed. The harness runs off the measured profiles
    in ``fixtures/``; the PDFs are only needed to regenerate them.
    """
    entry = exemplar(exemplar_id)
    from_env = os.environ.get(f"THINKSLIDES_EXEMPLAR_{entry.id.upper().replace('-', '_')}")
    if from_env and os.path.exists(from_env):
        return from_env

    search_dirs = [
        os.environ.get("THINKSLIDES_EXEMPLAR_DIR", ""),
        os.getcwd(),
        os.path.expanduser("~/Downloads"),
        os.path.expanduser("~"),
    ]
    for directory in search_dirs:
        if not directory:
            continue
        for name in entry.filenames:
            candidate = os.path.join(directory, name)
            if os.path.exists(candidate):
                return candidate
    return None
