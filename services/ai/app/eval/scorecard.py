"""
Score a generated deck against the reference profile, and write the result in
a form that diffs cleanly between commits.

The scorecard is the point of the harness. Every issue in the consultancy-deck
epic claims output will look more like the reference; without a number that
claim is an assertion, and without a *diffable* number a change that improves
palette conformance while wrecking density looks like an improvement.

Two design rules the rest of this module follows:

* **Every metric is a distance to the reference, normalised to 0-1.** A score
  of 1.0 means the deck matches the reference on that property. Metrics with
  no reference to compare against (a deck with no charts, say) report ``None``
  and are excluded from the total rather than scored as zero, so an absent
  feature is not confused with a badly implemented one.
* **Substance is scored separately from style and is never averaged into it.**
  Filler raises density, and density is most of the style score. Reporting one
  blended number would let a deck get worse while its score went up, which is
  precisely the failure the density-budget issue was warned about.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from app import colors
from app.measure.deck_metrics import DeckProfile
from app.measure.page_metrics import signature_distance

# Metrics grouped by what they measure. The style group answers "does this
# look like the reference"; the substance group answers "is there anything in
# it". They are reported side by side and never merged.
STYLE_METRICS = (
    "ink_coverage", "blocks_per_slide", "words_per_slide", "type_scale",
    "type_pairing", "palette", "ground_split", "chrome", "apparatus",
    "chart_style", "archetype_match",
)
SUBSTANCE_METRICS = (
    "informative_titles", "distinct_content", "lexical_variety", "sourced_figures",
)

# A rendered colour within this RGB distance of a palette entry is that entry.
PALETTE_MATCH_DISTANCE = 40.0
# Type sizes within this many points of a rung on the reference ramp conform.
TYPE_SIZE_TOLERANCE_PT = 1.5


@dataclass
class MetricScore:
    name: str
    score: Optional[float]
    generated: Any = None
    reference: Any = None
    note: str = ""


@dataclass
class Scorecard:
    """One run's scores, ready to be written next to the last one."""
    deck: str
    reference: str
    style: dict[str, MetricScore] = field(default_factory=dict)
    substance: dict[str, MetricScore] = field(default_factory=dict)

    @property
    def style_score(self) -> Optional[float]:
        return _mean_score(self.style)

    @property
    def substance_score(self) -> Optional[float]:
        return _mean_score(self.substance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "deck": self.deck,
            "reference": self.reference,
            "styleScore": _round(self.style_score),
            "substanceScore": _round(self.substance_score),
            "style": {k: asdict(v) for k, v in self.style.items()},
            "substance": {k: asdict(v) for k, v in self.substance.items()},
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Style fidelity: {self.deck}",
            "",
            f"Reference: `{self.reference}`",
            "",
            f"- **Style** {_format(self.style_score)}",
            f"- **Substance** {_format(self.substance_score)}",
            "",
            "| metric | score | generated | reference |",
            "| --- | --- | --- | --- |",
        ]
        for group in (self.style, self.substance):
            for metric in group.values():
                lines.append(
                    f"| {metric.name} | {_format(metric.score)} "
                    f"| {_short(metric.generated)} | {_short(metric.reference)} |"
                )
        return "\n".join(lines) + "\n"


def score(generated: DeckProfile, reference: DeckProfile) -> Scorecard:
    """Compare a generated deck's profile to the reference's."""
    card = Scorecard(deck=generated.name, reference=reference.name)

    card.style["ink_coverage"] = _ratio_metric(
        "ink_coverage", generated.ink_mean, reference.ink_mean
    )
    card.style["blocks_per_slide"] = _ratio_metric(
        "blocks_per_slide", generated.blocks_median, reference.blocks_median
    )
    card.style["words_per_slide"] = _ratio_metric(
        "words_per_slide", generated.words_median, reference.words_median
    )
    card.style["type_scale"] = _type_scale_metric(generated, reference)
    card.style["type_pairing"] = _type_pairing_metric(generated, reference)
    card.style["palette"] = _palette_metric(generated, reference)
    card.style["ground_split"] = _fraction_metric(
        "ground_split", generated.ground_split_fraction, reference.ground_split_fraction
    )
    card.style["chrome"] = _group_metric(
        "chrome",
        [
            (generated.eyebrow_fraction, reference.eyebrow_fraction),
            (generated.page_number_fraction, reference.page_number_fraction),
            (generated.brand_mark_fraction, reference.brand_mark_fraction),
        ],
    )
    card.style["apparatus"] = _group_metric(
        "apparatus",
        [
            (generated.footnote_fraction, reference.footnote_fraction),
            (generated.source_line_fraction, reference.source_line_fraction),
        ],
    )
    card.style["chart_style"] = _chart_style_metric(generated, reference)
    card.style["archetype_match"] = _archetype_metric(generated, reference)

    card.substance["informative_titles"] = _fraction_metric(
        "informative_titles", generated.informative_title_fraction,
        reference.informative_title_fraction,
    )
    card.substance["distinct_content"] = _fraction_metric(
        "distinct_content", generated.distinct_content, reference.distinct_content
    )
    card.substance["lexical_variety"] = _fraction_metric(
        "lexical_variety", generated.lexical_variety, reference.lexical_variety
    )
    card.substance["sourced_figures"] = _fraction_metric(
        "sourced_figures", generated.sourced_figure_fraction,
        reference.sourced_figure_fraction,
    )
    return card


def write(card: Scorecard, directory: str) -> tuple[str, str]:
    """Write the scorecard as JSON and as Markdown; return both paths."""
    os.makedirs(directory, exist_ok=True)
    stem = _slug(card.deck)
    json_path = os.path.join(directory, f"{stem}.json")
    md_path = os.path.join(directory, f"{stem}.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(card.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(card.to_markdown())
    return json_path, md_path


# --------------------------------------------------------------------------
# Individual metrics
# --------------------------------------------------------------------------

def _ratio_metric(name: str, generated: float, reference: float) -> MetricScore:
    """
    Score a quantity against a target as the ratio of the smaller to the
    larger, so overshooting the reference costs the same as undershooting it.

    Overshoot has to cost something: a deck that crams twice the reference's
    words onto a page has not become twice as good at looking like it.
    """
    if not reference:
        return MetricScore(name, None, generated, reference, "no reference value")
    if generated <= 0:
        return MetricScore(name, 0.0, generated, reference)
    ratio = min(generated, reference) / max(generated, reference)
    return MetricScore(name, round(ratio, 4), round(generated, 4), round(reference, 4))


def _fraction_metric(name: str, generated: float, reference: float) -> MetricScore:
    """
    Score a 0-1 rate against the reference's, with no penalty for exceeding it.

    Unlike a quantity, more is not a mismatch here: a deck that cites every
    figure has not drifted from a reference that cites four fifths of them.
    """
    if reference <= 0:
        return MetricScore(name, None, generated, reference, "reference does not use this")
    return MetricScore(
        name, round(min(1.0, generated / reference), 4),
        round(generated, 4), round(reference, 4),
    )


def _group_metric(name: str, pairs: list[tuple[float, float]]) -> MetricScore:
    scored = [_fraction_metric(name, g, r) for g, r in pairs]
    usable = [m.score for m in scored if m.score is not None]
    if not usable:
        return MetricScore(name, None, note="reference does not use this")
    return MetricScore(
        name, round(sum(usable) / len(usable), 4),
        [round(g, 3) for g, _ in pairs], [round(r, 3) for _, r in pairs],
    )


def _type_scale_metric(generated: DeckProfile, reference: DeckProfile) -> MetricScore:
    """The share of rendered type that sits on a rung of the reference ramp."""
    reference_sizes = [float(s) for s in reference.type_ramp]
    if not reference_sizes or not generated.type_ramp:
        return MetricScore("type_scale", None, note="no ramp to compare")
    total = sum(generated.type_ramp.values()) or 1
    conforming = sum(
        count for size, count in generated.type_ramp.items()
        if any(abs(float(size) - rung) <= TYPE_SIZE_TOLERANCE_PT for rung in reference_sizes)
    )
    return MetricScore(
        "type_scale", round(conforming / total, 4),
        sorted(generated.type_ramp), sorted(reference.type_ramp),
    )


def _type_pairing_metric(generated: DeckProfile, reference: DeckProfile) -> MetricScore:
    """
    Whether the deck's display/body relationship matches the reference's -
    both the size step and whether the display face is a serif.
    """
    if not reference.display_body_ratio or not generated.display_body_ratio:
        return MetricScore("type_pairing", None, note="no pairing to compare")
    step = min(generated.display_body_ratio, reference.display_body_ratio) / max(
        generated.display_body_ratio, reference.display_body_ratio
    )
    serif = 1.0 - abs(
        generated.display_is_serif_fraction - reference.display_is_serif_fraction
    )
    return MetricScore(
        "type_pairing", round((step + serif) / 2, 4),
        {"ratio": generated.display_body_ratio,
         "serif": generated.display_is_serif_fraction},
        {"ratio": reference.display_body_ratio,
         "serif": reference.display_is_serif_fraction},
    )


def _palette_metric(generated: DeckProfile, reference: DeckProfile) -> MetricScore:
    """
    The share of rendered ink and ground drawn from colours the reference
    also uses.

    This is deliberately a conformance check and not an identity check: a deck
    built on a brand palette is not supposed to come out McKinsey navy. It
    scores whether the deck's colours form a small, coherent set the way the
    reference's do, by measuring against the reference's own set - so it is
    only meaningful when the reference is the template the deck was built
    from, which is how the harness uses it.
    """
    reference_colors = list(reference.text_colors) + list(reference.grounds)
    if not reference_colors:
        return MetricScore("palette", None, note="no reference palette")

    total = sum(generated.text_colors.values()) or 1
    conforming = sum(
        count for color, count in generated.text_colors.items()
        if _nearest_distance(color, reference_colors) <= PALETTE_MATCH_DISTANCE
    )
    ground_weight = sum(generated.grounds.values()) or 1.0
    ground_conforming = sum(
        weight for color, weight in generated.grounds.items()
        if _nearest_distance(color, reference_colors) <= PALETTE_MATCH_DISTANCE
    )
    combined = (conforming / total + ground_conforming / ground_weight) / 2
    return MetricScore(
        "palette", round(combined, 4),
        list(generated.text_colors)[:4] + list(generated.grounds)[:2],
        reference_colors[:6],
    )


def _chart_style_metric(generated: DeckProfile, reference: DeckProfile) -> MetricScore:
    if not generated.chart_count or not reference.chart_count:
        return MetricScore("chart_style", None, note="no charts on one side")
    gridline = 1.0 - abs(
        generated.chart_gridline_fraction - reference.chart_gridline_fraction
    )
    axis = 1.0 - abs(
        generated.chart_vertical_axis_fraction - reference.chart_vertical_axis_fraction
    )
    return MetricScore(
        "chart_style", round((gridline + axis) / 2, 4),
        {"gridlines": generated.chart_gridline_fraction,
         "verticalAxis": generated.chart_vertical_axis_fraction},
        {"gridlines": reference.chart_gridline_fraction,
         "verticalAxis": reference.chart_vertical_axis_fraction},
    )


def _archetype_metric(generated: DeckProfile, reference: DeckProfile) -> MetricScore:
    """
    How close each generated page sits to its nearest reference page in
    signature space - "does a generated slide cluster with a reference one".
    """
    if not generated.signatures or not reference.signatures:
        return MetricScore("archetype_match", None, note="no signatures")
    distances = [
        min(signature_distance(generated_sig, reference_sig)
            for reference_sig in reference.signatures)
        for generated_sig in generated.signatures
    ]
    mean_distance = sum(distances) / len(distances)
    return MetricScore(
        "archetype_match", round(max(0.0, 1.0 - mean_distance), 4),
        round(mean_distance, 4), 0.0,
    )


# --------------------------------------------------------------------------

def _nearest_distance(color: str, candidates: list[str]) -> float:
    rgb = colors.parse_hex(color)
    if rgb is None:
        return 999.0
    best = 999.0
    for candidate in candidates:
        other = colors.parse_hex(candidate)
        if other is None:
            continue
        distance = sum((a - b) ** 2 for a, b in zip(rgb, other)) ** 0.5
        best = min(best, distance)
    return best


def _mean_score(group: dict[str, MetricScore]) -> Optional[float]:
    usable = [m.score for m in group.values() if m.score is not None]
    return round(sum(usable) / len(usable), 4) if usable else None


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 4)


def _format(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _short(value: Any) -> str:
    text = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower()).strip("-")
