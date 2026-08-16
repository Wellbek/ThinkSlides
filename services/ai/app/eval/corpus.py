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
