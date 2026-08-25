"""
The gate on the metrics themselves.

Before a composition metric is allowed to steer anything, it has to prove it
can tell a designed page from a scrambled one. The test is deliberately
brutal: take a real reference page, move its elements around *without
changing any of them*, and require the metric to drop. Same text, same
images, same areas, same ink, same palette, same type - only the composition
differs.

Every metric in the old scorecard fails this by construction, which is the
whole finding. Ink coverage, words per page, the type ramp and the palette
are identical before and after a scramble, so a scrambled reference page
scores exactly as well as the real one. A metric that reports no difference
between those two cannot report a difference between our output and either.

A metric that fails here is deleted rather than tuned until it passes.
Tuning a metric against the test that is supposed to validate it is how you
end up with a number that agrees with you and nothing else.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from app.eval.composition import (
    METRIC_NAMES,
    Baseline,
    Element,
    PageObservation,
    measure_page,
)

# How much a metric must separate a real page from a scrambled one before it
# is believed. Below this it is reporting noise with a confident face.
REQUIRED_MARGIN = 0.10
# Scrambles are stochastic, so the reading is a median over several.
DEFAULT_SEEDS = (11, 23, 37)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _regroup(columns: Sequence[Sequence[Baseline]],
             flat: Sequence[Baseline]) -> tuple[tuple[Baseline, ...], ...]:
    """Put perturbed baselines back into the columns they came from."""
    out, index = [], 0
    for column in columns:
        out.append(tuple(flat[index:index + len(column)]))
        index += len(column)
    return tuple(out)


def jitter(obs: PageObservation, seed: int, sigma: float = 0.02) -> PageObservation:
    """Nudge every element off its alignment, keeping its size.

    This is the purest test: nothing is added, removed or resized, and the
    only property destroyed is that edges used to line up.
    """
    rng = random.Random(seed)
    moved = []
    for element in obs.elements:
        dx = rng.gauss(0.0, sigma)
        dy = rng.gauss(0.0, sigma)
        x0 = _clamp(element.x0 + dx, -0.05, 1.0)
        y0 = _clamp(element.y0 + dy, -0.05, 1.0)
        moved.append(Element(x0, y0, x0 + element.width, y0 + element.height,
                             element.kind))
    baselines = tuple(
        Baseline(b.position_pt + rng.gauss(0.0, sigma * obs.height_pt), b.size_pt)
        for b in obs.baselines
    )
    return PageObservation(obs.width_pt, obs.height_pt, tuple(moved), baselines,
                           _regroup(obs.baseline_columns, baselines))


def shuffle_positions(obs: PageObservation, seed: int) -> PageObservation:
    """Swap where things sit, keeping what they are and how big they are."""
    rng = random.Random(seed)
    elements = list(obs.elements)
    origins = [(e.x0, e.y0) for e in elements]
    rng.shuffle(origins)
    moved = [
        Element(x0, y0, x0 + e.width, y0 + e.height, e.kind)
        for e, (x0, y0) in zip(elements, origins)
    ]
    return PageObservation(obs.width_pt, obs.height_pt, tuple(moved),
                           obs.baselines, obs.baseline_columns)


def break_rhythm(obs: PageObservation, seed: int) -> PageObservation:
    """Take the text off its baseline grid and leave everything else alone."""
    rng = random.Random(seed)
    baselines = tuple(
        Baseline(b.position_pt + rng.uniform(-0.5, 0.5) * b.size_pt * 1.3,
                 b.size_pt)
        for b in obs.baselines
    )
    return PageObservation(obs.width_pt, obs.height_pt, obs.elements, baselines,
                           _regroup(obs.baseline_columns, baselines))


SCRAMBLES: dict[str, Callable[[PageObservation, int], PageObservation]] = {
    "jitter": jitter,
    "shuffle": shuffle_positions,
    "rhythm": break_rhythm,
}


@dataclass(frozen=True)
class MetricVerdict:
    """One metric's readings, and whether it earned its place.

    Scored against the scramble that hurts it most, not against the median
    of all of them. Breaking the baseline rhythm moves no element, so for
    every spatial metric it returns the unscrambled reading - averaging that
    in drags a good metric back to its own reference value and fails it for
    a property it was never being asked about.
    """

    name: str
    reference: float
    scrambled: float
    ours: Optional[float] = None
    worst_scramble: str = ""

    @property
    def margin(self) -> float:
        return self.reference - self.scrambled

    @property
    def discriminates(self) -> bool:
        return self.margin >= REQUIRED_MARGIN

    def line(self) -> str:
        ours = "-" if self.ours is None else f"{self.ours:.3f}"
        verdict = "keeps" if self.discriminates else "DELETE"
        return (f"| {self.name} | {self.reference:.3f} | {self.scrambled:.3f} "
                f"| {ours} | {self.margin:+.3f} | {self.worst_scramble} "
                f"| {verdict} |")


@dataclass(frozen=True)
class DiscriminationResult:
    verdicts: tuple[MetricVerdict, ...]

    @property
    def passing(self) -> tuple[str, ...]:
        return tuple(v.name for v in self.verdicts if v.discriminates)

    @property
    def failing(self) -> tuple[str, ...]:
        return tuple(v.name for v in self.verdicts if not v.discriminates)

    def to_markdown(self) -> str:
        lines = [
            "| metric | reference | scrambled | ours | margin | scramble | verdict |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        lines += [v.line() for v in self.verdicts]
        return "\n".join(lines)


def _median(values: Sequence[float]) -> float:
    usable = [v for v in values if v == v]  # drops NaN
    return statistics.median(usable) if usable else float("nan")


def discriminate(reference: PageObservation, *,
                 ours: Optional[PageObservation] = None,
                 seeds: Sequence[int] = DEFAULT_SEEDS,
                 scrambles: Sequence[str] = ("jitter", "shuffle", "rhythm"),
                 **measure_kwargs) -> DiscriminationResult:
    """Score a real page, scrambled copies of it, and optionally our output.

    The scrambled readings are a median across seeds and scramble kinds, so
    one unlucky permutation that happens to land tidily cannot pass a metric
    that does not deserve it.
    """
    real = measure_page(reference, **measure_kwargs)
    mine = measure_page(ours, **measure_kwargs) if ours is not None else None

    per_scramble: dict[str, dict[str, list[float]]] = {
        kind: {name: [] for name in METRIC_NAMES} for kind in scrambles
    }
    for kind in scrambles:
        make = SCRAMBLES[kind]
        for seed in seeds:
            score = measure_page(make(reference, seed), **measure_kwargs)
            for name in METRIC_NAMES:
                value = score.values.get(name, float("nan"))
                if value == value:
                    per_scramble[kind][name].append(value)

    verdicts = []
    for name in METRIC_NAMES:
        real_value = real.values.get(name, float("nan"))
        if real_value != real_value:
            continue  # the metric did not apply to this page at all
        worst_kind, worst_value = "", real_value
        for kind in scrambles:
            median = _median(per_scramble[kind][name])
            if median != median:
                continue
            if median <= worst_value:
                worst_kind, worst_value = kind, median
        verdicts.append(
            MetricVerdict(
                name=name,
                reference=real_value,
                scrambled=worst_value,
                ours=(mine.values.get(name) if mine else None),
                worst_scramble=worst_kind or "none",
            )
        )
    return DiscriminationResult(verdicts=tuple(verdicts))
