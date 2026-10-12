"""
Whether a script asserts a figure with nothing behind it.

Shared by both of this service's code-writing tools - `edit_deck_with_code`
(the finished-deck case) and `compute_visual_with_code` (the draft-time
case) - because the risk is identical either way and stated once here rather
than maintained in two places that would drift.

`generate_image.py:23-32` calls an invented figure presented as measured the
single worst thing this system can emit, and a vision model grading a
rendered page cannot tell a real number from a plausible one - so the check
is mechanical, at the boundary, before anything runs: a script that draws a
figure declares where its numbers came from, or it does not run. This is the
same rule `#267` already applies to every other exhibit - an unsupported one
is a reported gap rather than a silent claim - extended to the one builder
whose numbers do not pass through a typed argument a caller could ground
independently.

Deliberately not a judgement about whether the declaration is *true*.
Nothing here can know that; only that the model was made to say where the
number is from, which is what makes an invented one a lie on the record
rather than a lie nobody could have caught.
"""

from __future__ import annotations

import ast
from typing import Optional

# What a number leaves as marks, not the word "chart": a script that writes
# text frames full of percentages is asserting figures as much as one that
# calls add_chart.
FIGURE_MARKERS = (
    "add_chart", "chart_data", "CategoryChartData", "XyChartData",
    "add_table", "plot(", "bar(", "barh(", "pie(", "scatter(", "hist(",
)

# The same marks, read off the script's syntax rather than its text. A name
# from the deck's chart and table API is a figure wherever it appears; a
# plotting verb is one only as a method (`ax.bar`) or imported from a
# plotting library, because a script's own helper called `bar` - a progress
# bar, probe-progress-bar (#376) - asserts nothing.
_FIGURE_NAMES = frozenset({
    "add_chart", "chart_data", "CategoryChartData", "XyChartData", "add_table"})
_PLOT_VERBS = frozenset({"plot", "bar", "barh", "pie", "scatter", "hist"})


def _draws_a_figure(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Not ours to parse; the gate refuses it anyway. Err towards asking.
        return any(marker in source for marker in FIGURE_MARKERS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _FIGURE_NAMES:
            return True
        if isinstance(node, ast.Attribute) and (
                node.attr in _FIGURE_NAMES or node.attr in _PLOT_VERBS):
            return True
        if isinstance(node, ast.ImportFrom) and any(
                alias.name in _PLOT_VERBS | _FIGURE_NAMES for alias in node.names):
            return True
    return False


_UNGROUNDED = ("none", "n/a", "-", "na")


def problem(source: str, inputs: str) -> Optional[str]:
    """Why this script's figures have nothing behind them, or None if they do."""
    declared = str(inputs or "").strip()
    if declared and declared.lower() not in _UNGROUNDED:
        return None
    if not _draws_a_figure(source):
        return None
    return (
        "This script draws a figure and `inputs` does not say where its "
        "numbers came from. A chart drawn by code tells the reader the "
        "numbers were measured, not typed, and nothing downstream can tell "
        "a real figure from a plausible one. Name the document, the search "
        "result or the part of the conversation each number came from - or, "
        "if you do not have them, do not draw the figure."
    )
