"""
A slide heading must not claim more than the visual under it shows.

The planner writes the title before anyone knows what the data will turn out
to be. A deep-mode run asked for "revenue trend over the last five years", the
search surfaced three, and the slide shipped:

    Revenue Trend Over Five Years
    [a three-point line]

The heading is a checkable claim about the chart beneath it, so it gets
checked - the same class of defect as #60 (copy naming the wrong subject), one
level up.
"""

import pytest

from app.agent.tools.output_qa import _span_claim, check_content


def _slide(title: str, *, categories=None, rows=None, header_row=True) -> dict:
    contents: list[dict] = [{"type": "text", "runs": [{"text": title}]}]
    if categories is not None:
        contents.append({
            "type": "chart",
            "chart": {
                "chartType": "line",
                "categories": list(categories),
                "series": [{"name": "Revenue", "values": [1] * len(categories)}],
            },
        })
    if rows is not None:
        contents.append({
            "type": "table",
            "table": {"headerRow": header_row, "rows": rows},
        })
    return {"slideIndex": 0, "contents": contents}


def _title_issues(slide: dict) -> list[dict]:
    return [i for i in check_content([slide]) if "heading claims" in i["detail"]]


class TestSpanClaim:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Revenue Trend Over Five Years", (5, "years")),
            ("5-Year Revenue Trend", (5, "year")),
            ("Top 10 Markets", (10, "markets")),
            ("Growth Across Three Quarters", (3, "quarters")),
            ("Our Four Pillars", (4, "pillars")),
        ],
    )
    def test_a_countable_claim_is_read(self, text, expected):
        assert _span_claim(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "2025 Revenue Breakdown",   # a year, not a count
            "Revenue Growth",           # no number at all
            "$40.0B in Revenue",        # a currency amount
            "Up 15% Year on Year",      # a percentage
            "Q3 Performance",           # a period label, not a span
        ],
    )
    def test_a_number_that_is_not_a_count_is_ignored(self, text):
        """False positives here would nag on perfectly good headings."""
        assert _span_claim(text) is None


class TestHeadingAgainstChart:
    def test_the_reported_case_is_flagged(self):
        issues = _title_issues(
            _slide("Revenue Trend Over Five Years", categories=["2023", "2024", "2025"])
        )
        assert len(issues) == 1
        detail = issues[0]["detail"]
        assert "claims 5 years" in detail
        assert "shows 3" in detail

    def test_a_heading_that_matches_passes(self):
        assert _title_issues(
            _slide("Revenue Trend Over Three Years", categories=["2023", "2024", "2025"])
        ) == []

    def test_a_heading_undercounting_is_also_flagged(self):
        """Wrong in the other direction is still wrong."""
        issues = _title_issues(
            _slide("Top 3 Markets", categories=["A", "B", "C", "D", "E"])
        )
        assert len(issues) == 1
        assert "shows 5" in issues[0]["detail"]

    def test_a_year_in_the_title_is_not_a_span(self):
        assert _title_issues(
            _slide("2025 Revenue Breakdown", categories=["A", "B", "C", "D"])
        ) == []

    def test_a_heading_with_no_visual_is_left_alone(self):
        assert _title_issues(_slide("Revenue Trend Over Five Years")) == []

    def test_a_visual_with_no_claim_is_left_alone(self):
        assert _title_issues(_slide("Revenue Trend", categories=["2023", "2024"])) == []


class TestHeadingAgainstTable:
    def test_the_header_row_is_not_counted_as_data(self):
        slide = _slide(
            "Top 3 Markets",
            rows=[["Market", "Revenue"], ["US", "1"], ["UK", "2"], ["DE", "3"]],
        )
        assert _title_issues(slide) == []

    def test_a_mismatched_table_is_flagged(self):
        slide = _slide(
            "Top 5 Markets",
            rows=[["Market", "Revenue"], ["US", "1"], ["UK", "2"]],
        )
        issues = _title_issues(slide)
        assert len(issues) == 1
        assert "shows 2" in issues[0]["detail"]

    def test_a_headerless_table_counts_every_row(self):
        slide = _slide(
            "Two Segments",
            rows=[["US", "1"], ["UK", "2"]],
            header_row=False,
        )
        assert _title_issues(slide) == []


def test_the_issue_names_the_slide_it_is_about():
    issues = _title_issues(
        _slide("Revenue Trend Over Five Years", categories=["2023", "2024", "2025"])
    )
    assert issues[0]["slideIndex"] == 0
    assert issues[0]["type"] == "content"
