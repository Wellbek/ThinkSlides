"""
Recovering a grid, and - the part that actually matters - refusing to.

Period estimation always returns an answer. Handed a page laid out by hand
it will produce a confident pitch that explains nothing, and every stage
downstream will then measure conformance to that fiction and report the
convergence as progress. So the tests that carry the most weight here are
the refusals: noise must produce no grid, and a page's own scatter must not
be dressed up as a 24-column system.
"""
import random

import pytest

from app.measure.grid import (
    MIN_EDGE_SNAP,
    Edge,
    edges_from_boxes,
    recover_grid,
)


def _grid_edges(columns: int, margin: float, gutter: float,
                spans: list[tuple[int, int]], weight: float = 1.0) -> list[Edge]:
    """Edges of elements placed on a known grid, for recovering back."""
    usable = 1.0 - 2 * margin
    column = (usable - gutter * (columns - 1)) / columns
    pitch = column + gutter
    edges: list[Edge] = []
    for col, span in spans:
        left = margin + col * pitch
        right = left + span * column + (span - 1) * gutter
        edges.append(Edge(position=left, weight=weight, side="left"))
        edges.append(Edge(position=right, weight=weight, side="right"))
    return edges


class TestAKnownGridComesBack:
    def test_a_four_column_grid_is_recovered(self):
        edges = _grid_edges(
            columns=4, margin=0.05, gutter=0.025,
            spans=[(0, 1), (1, 1), (2, 1), (3, 1), (0, 2), (2, 2)],
        )
        grid = recover_grid(edges)
        assert grid is not None
        assert grid.columns == 4
        assert grid.margin_left == pytest.approx(0.05, abs=0.01)
        assert grid.gutter == pytest.approx(0.025, abs=0.015)

    def test_a_twelve_column_grid_is_recovered(self):
        """The spans have to be ones only a 12-column lattice explains.

        Elements that all start on a multiple of three and span three are
        equally well described by a four-column grid, and four is the honest
        reading of that evidence - the finer grid is unfalsifiable from it.
        Irregular starts are what make twelve the only fit.
        """
        edges = _grid_edges(
            columns=12, margin=0.04, gutter=0.01,
            spans=[(0, 5), (5, 7), (2, 3), (7, 2), (1, 4), (9, 3), (0, 12),
                   (4, 4), (10, 2), (3, 6)],
        )
        grid = recover_grid(edges)
        assert grid is not None
        assert grid.columns == 12
        assert grid.edge_snap >= MIN_EDGE_SNAP

    def test_a_finer_grid_used_only_in_thirds_reads_as_the_coarser_one(self):
        """And that reading is correct, not a limitation worth apologising for.

        Nothing in the evidence distinguishes the two, so claiming twelve
        would be asserting something the page does not show.
        """
        edges = _grid_edges(
            columns=12, margin=0.04, gutter=0.01,
            spans=[(0, 3), (3, 3), (6, 3), (9, 3), (0, 6), (6, 6)],
        )
        grid = recover_grid(edges)
        assert grid is not None
        assert grid.columns == 4


class TestItRefusesToInventOne:
    def test_random_edges_produce_no_grid(self):
        rng = random.Random(20260822)
        edges = []
        for _ in range(40):
            left = rng.uniform(0.05, 0.7)
            edges.append(Edge(position=left, weight=1.0, side="left"))
            edges.append(Edge(position=left + rng.uniform(0.08, 0.25),
                              weight=1.0, side="right"))
        assert recover_grid(edges) is None

    def test_too_few_edges_produce_no_grid(self):
        assert recover_grid([Edge(position=0.1), Edge(position=0.5)]) is None

    def test_a_grid_is_never_reported_below_the_snap_threshold(self):
        """Whatever comes back has earned it: the threshold is not advisory."""
        rng = random.Random(7)
        edges = [
            Edge(position=rng.uniform(0.0, 1.0), weight=1.0,
                 side="left" if i % 2 else "right")
            for i in range(60)
        ]
        grid = recover_grid(edges)
        assert grid is None or grid.edge_snap >= MIN_EDGE_SNAP


class TestItPrefersTheGridADesignerWouldUse:
    def test_a_coarse_grid_beats_the_fine_one_that_contains_it(self):
        """Every lattice finer than the real one also explains the real one.

        Without charging for columns, the answer is always the finest pitch
        searched - a track almost everywhere, explaining everything and
        meaning nothing.
        """
        edges = _grid_edges(columns=4, margin=0.05, gutter=0.025,
                            spans=[(0, 1), (1, 1), (2, 1), (3, 1)])
        grid = recover_grid(edges)
        assert grid is not None
        assert grid.columns <= 6

    def test_a_few_off_grid_elements_do_not_move_the_lattice(self):
        """A publication hangs a folio in the margin and indents a rail.

        A least-squares fit lets those few drag the whole grid off the many
        elements that are on it; counting hits does not.
        """
        edges = _grid_edges(columns=4, margin=0.05, gutter=0.025,
                            spans=[(0, 1), (1, 1), (2, 1), (3, 1), (1, 2)],
                            weight=4.0)
        edges += [
            Edge(position=0.017, weight=0.2, side="left"),
            Edge(position=0.031, weight=0.2, side="right"),
            Edge(position=0.913, weight=0.2, side="left"),
        ]
        grid = recover_grid(edges)
        assert grid is not None
        assert grid.columns == 4
        assert grid.margin_left == pytest.approx(0.05, abs=0.012)


class TestEdgeWeighting:
    def test_edges_are_weighted_by_element_area(self):
        """A body column and a page number are one edge each, and only one
        of them is evidence about the grid."""
        edges = edges_from_boxes(
            [(100.0, 100.0, 500.0, 400.0), (10.0, 10.0, 30.0, 20.0)],
            page_width=1000.0,
        )
        big = next(e for e in edges if e.position == pytest.approx(0.1))
        small = next(e for e in edges if e.position == pytest.approx(0.01))
        assert big.weight > small.weight * 100
