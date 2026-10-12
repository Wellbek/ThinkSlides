"""
Build a Design Language Pack for each exemplar, from its own pages.

    PYTHONPATH=. .venv/bin/python tools/build_packs.py

The PDFs are deliberately not committed (see app/eval/corpus.py); the packs
they produce are, because a pack is a reviewable artefact and the whole point
is that a designer can read what was extracted and correct it.
"""
from __future__ import annotations

import json
import os
import sys

from app.design.pack_build import from_pdf
from app.eval import corpus

OUT = os.path.join("app", "design", "packs")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    written = 0
    for entry in corpus.EXEMPLARS:
        path = corpus.find_exemplar_pdf(entry.id)
        if not path:
            print(f"skip {entry.id}: pdf not on this machine", file=sys.stderr)
            continue
        # No page list: the publication's own pages are clustered and each
        # group becomes an archetype. The held-out validation set is the one
        # thing that is never read - a pack fitted to the pages it will later
        # be scored against measures nothing.
        pack = from_pdf(path, pack_id=entry.id,
                        exclude_pages=entry.active_validation_pages(),
                        label=entry.title, note=entry.note)
        target = os.path.join(OUT, f"{entry.id}.json")
        with open(target, "w") as handle:
            json.dump(pack.model_dump(mode="json"), handle, indent=2)
        grid = pack.grid_confidence
        print(f"{entry.id}: {pack.grid.columns} cols "
              f"(snap {grid.value:.2f}), rhythm {pack.rhythm.unit_pt:.1f}pt "
              f"(conf {pack.rhythm.confidence.value:.2f}), "
              f"spacing {pack.spacing.base_pt:.1f}pt "
              f"(explains {pack.spacing.confidence.value:.0%}), "
              f"{len(pack.type_roles)} type roles, "
              f"{len(pack.archetypes)} archetypes -> {target}")
        written += 1
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
