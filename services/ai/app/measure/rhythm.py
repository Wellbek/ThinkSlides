"""
Recovering the baseline rhythm a publication sets its text on.

Same shape of problem as the column grid, and the same trap: this is period
estimation, it always returns an answer, and a confident unit recovered from
a page that has no rhythm is worse than admitting there is none.

Two things make it harder than it looks.

**Columns must be measured separately.** Pooling every baseline on the page
and sorting them interleaves the columns, which manufactures gaps that
belong to no column - a two-column page set perfectly on 21pt produces
spurious 7pt and 13pt gaps the moment you sort its baselines together. The
rhythm is a property of a column of text, so it is measured per block and
pooled afterwards.

**The phase estimator has to be robust.** A circular mean is a mean, and it
is pulled off by the paragraph spacing and the odd rounded line. Grid
searching the phase to minimise the *median* residual is indifferent to a
minority of lines that sit anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

MIN_UNIT_PT = 7.0
MAX_UNIT_PT = 40.0
UNIT_STEP_PT = 0.05
PHASE_STEPS = 48
# A residual of a quarter unit is what uniformly random baselines produce, so
# that is where a reading stops meaning anything.
RANDOM_RESIDUAL = 0.25
MIN_CONFIDENCE = 0.5
MIN_LINES = 6


@dataclass(frozen=True)
class RecoveredRhythm:
    """A baseline unit, its phase, and how much of the text keeps to it."""

    unit_pt: float
    phase_pt: float
    residual: float
    confidence: float
    lines: int

    @property
    def trustworthy(self) -> bool:
        return self.confidence >= MIN_CONFIDENCE


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0.0


def _residual_at(baselines: Sequence[float], unit: float, phase: float) -> float:
    """Median distance to the lattice, in units."""
    out = []
    for baseline in baselines:
        offset = (baseline - phase) / unit
        out.append(abs(offset - round(offset)))
    return _median(out)


def _best_phase(baselines: Sequence[float], unit: float) -> tuple[float, float]:
    """Phase minimising the median residual, by search rather than by mean.

    A circular mean is pulled off by paragraph spacing and by the occasional
    rounded line; a median is not.
    """
    best_phase, best_residual = 0.0, float("inf")
    for step in range(PHASE_STEPS):
        phase = unit * step / PHASE_STEPS
        residual = _residual_at(baselines, unit, phase)
        if residual < best_residual:
            best_phase, best_residual = phase, residual
    return best_phase, best_residual


def recover_rhythm(columns: Sequence[Sequence[float]]) -> Optional[RecoveredRhythm]:
    """The baseline unit these columns of text share, or None if they share none.

    ``columns`` is one sequence of baseline positions per column or text
    block. Measuring per column and pooling the residuals is the whole point:
    a page's columns each keep the rhythm on their own, and interleaving them
    destroys the signal before it can be read.
    """
    usable = [list(c) for c in columns if len(c) >= 2]
    total_lines = sum(len(c) for c in usable)
    if total_lines < MIN_LINES:
        return None

    best = None
    unit = MIN_UNIT_PT
    while unit <= MAX_UNIT_PT:
        residuals = []
        weights = []
        for column in usable:
            _, residual = _best_phase(column, unit)
            residuals.append(residual)
            weights.append(len(column))
        pooled = sum(r * w for r, w in zip(residuals, weights)) / sum(weights)
        # A half-size unit fits every lattice point of the real one and adds
        # a point between each, so it always scores at least as well. Charge
        # for fineness the way the column grid charges for columns.
        score = pooled + 0.02 * (MIN_UNIT_PT / unit)
        if best is None or score < best[0]:
            best = (score, unit, pooled)
        unit = round(unit + UNIT_STEP_PT, 4)

    assert best is not None
    _, unit, pooled = best
    flat = [b for column in usable for b in column]
    phase, _ = _best_phase(flat, unit)
    confidence = max(0.0, min(1.0, 1.0 - pooled / RANDOM_RESIDUAL))
    rhythm = RecoveredRhythm(unit_pt=unit, phase_pt=phase, residual=pooled,
                             confidence=confidence, lines=total_lines)
    return rhythm if rhythm.trustworthy else None


def columns_from_page(page, size_pt: Optional[float] = None,
                      tolerance: float = 0.6) -> list[list[float]]:
    """Baselines of a page's body text, one list per text block.

    Restricted to the dominant type size unless told otherwise: a rhythm is
    kept by body copy, while a headline and a folio sit where they look
    right.
    """
    blocks: list[list[tuple[float, float]]] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        lines: list[tuple[float, float]] = []
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            lines.append((
                max(s["bbox"][3] for s in spans),
                round(max(s["size"] for s in spans), 1),
            ))
        if lines:
            blocks.append(lines)

    if size_pt is None:
        counts: dict[float, int] = {}
        for block in blocks:
            for _, size in block:
                counts[size] = counts.get(size, 0) + 1
        if not counts:
            return []
        size_pt = max(counts, key=lambda s: counts[s])

    out = []
    for block in blocks:
        kept = [pos for pos, size in block if abs(size - size_pt) <= tolerance]
        if len(kept) >= 2:
            out.append(kept)
    return out
