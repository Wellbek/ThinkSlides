"""
Assigning material to slots, optimally rather than greedily.

Binding the biggest item to the biggest slot and working down is what a
greedy pass does, and it produces a page where the last two items are in
each other's frames: the chart that needed the wide slot took the tall one
because it was considered first. The assignment has to be solved as a whole.

This is the rectangular assignment problem. ``scipy.optimize`` has it, and
scipy is a thirty-megabyte dependency for one function in a container that
already carries LibreOffice; the algorithm is a hundred lines and has not
changed since 1957. It is implemented here as the Jonker-Volgenant shortest
augmenting path variant, which is O(n^3) and exact.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

INFEASIBLE = math.inf


def solve(cost: Sequence[Sequence[float]]) -> list[Optional[int]]:
    """Assign each row to at most one column, minimising total cost.

    Returns one entry per row: the column it was assigned, or None when the
    row could not be placed anywhere at finite cost. Rectangular input is
    fine in either direction - more material than slots, or more slots than
    material - and unassignable rows come back as None rather than being
    forced into a slot that cannot hold them.
    """
    rows = len(cost)
    if rows == 0:
        return []
    columns = len(cost[0])
    if columns == 0:
        return [None] * rows

    # Pad to a square matrix with a dummy column that costs nothing, so a row
    # with nowhere to go is left unassigned rather than making the problem
    # infeasible. Dummy assignments are unwound at the end.
    size = max(rows, columns)
    big = _finite_ceiling(cost) * (size + 1) + 1.0
    matrix = [
        [
            (cost[r][c] if r < rows and c < columns and
             cost[r][c] < INFEASIBLE else big)
            for c in range(size)
        ]
        for r in range(size)
    ]

    # u, v are the dual potentials; way[] records the alternating path.
    potential_row = [0.0] * (size + 1)
    potential_col = [0.0] * (size + 1)
    match_col = [size] * (size + 1)   # column -> row, size means unmatched
    way = [0] * (size + 1)

    for row in range(size):
        match_col[size] = row
        column = size
        minimum = [INFEASIBLE] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[column] = True
            current_row = match_col[column]
            delta = INFEASIBLE
            next_column = 0
            for candidate in range(size):
                if used[candidate]:
                    continue
                reduced = (matrix[current_row][candidate]
                           - potential_row[current_row]
                           - potential_col[candidate])
                if reduced < minimum[candidate]:
                    minimum[candidate] = reduced
                    way[candidate] = column
                if minimum[candidate] < delta:
                    delta = minimum[candidate]
                    next_column = candidate
            for candidate in range(size + 1):
                if used[candidate]:
                    potential_row[match_col[candidate]] += delta
                    potential_col[candidate] -= delta
                else:
                    minimum[candidate] -= delta
            column = next_column
            if match_col[column] == size:
                break
        while column != size:
            previous = way[column]
            match_col[column] = match_col[previous]
            column = previous

    assignment: list[Optional[int]] = [None] * rows
    for column in range(size):
        row = match_col[column]
        if row < rows and column < columns and cost[row][column] < INFEASIBLE:
            assignment[row] = column
    return assignment


def _finite_ceiling(cost: Sequence[Sequence[float]]) -> float:
    finite = [
        value for row in cost for value in row
        if value < INFEASIBLE and not math.isnan(value)
    ]
    return max(finite) if finite else 1.0


def total_cost(cost: Sequence[Sequence[float]],
               assignment: Sequence[Optional[int]]) -> float:
    """What an assignment actually costs, unassigned rows excluded."""
    return sum(
        cost[row][column]
        for row, column in enumerate(assignment)
        if column is not None
    )
