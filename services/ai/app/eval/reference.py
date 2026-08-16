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
from app.eval.deck_metrics import DeckProfile, profile_pdf

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


def reference_metadata() -> dict:
    with open(REFERENCE_PROFILE_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    return {k: v for k, v in data.items() if k != "profiles"}


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
    payload = {
        "source": os.path.basename(path),
        "pageCount": corpus.REFERENCE_PAGE_COUNT,
        "pageSizePt": list(corpus.REFERENCE_PAGE_SIZE_PT),
        "designPages": list(corpus.DESIGN_PAGES),
        "validationPages": list(corpus.active_validation_pages()),
        "profiles": {name: profile.to_dict() for name, profile in profiles.items()},
    }
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(REFERENCE_PROFILE_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return REFERENCE_PROFILE_PATH
