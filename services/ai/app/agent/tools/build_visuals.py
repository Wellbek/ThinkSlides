"""
Tools: build_chart, build_table, build_key_metrics

Typed, parameterised constructors for the visual elements of a slide.

Charts and tables used to reach the deck only as free-form JSON inside
``design_slide_content``'s single LLM response, described to the model in
prose in the middle of a long prompt. Everything that could go wrong did:
series with fewer values than categories, units baked into the numbers
("36.2B"), ragged rows, a chart restating the bullets sitting next to it -
and none of it was detectable until the renderer silently dropped the item
or drew something wrong.

Here the model states the data through a function signature instead. Each
tool validates what it was given, normalises it, and returns a ready
content block that the loop attaches to the slide itself. The figures are
checked once, at the point they are stated, and never round-trip through a
second model call - which is also what stops a chart and its own bullets
disagreeing.

Failures come back as a tool error naming the specific problem, because
that is a message the model can act on.

Each of these also says *where* it goes and *which one it is* - ``region_id``
and ``slot``, shared with the block builders through ``_binding``. Without
them a chart could only ever land in a placeholder, so a page could never put
a bridge beside a mix chart however much the material wanted it.
"""

from __future__ import annotations
import time

from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.tools._binding import bind, region_param, slot_param
from app.agent.tools._json_extract import coerce_json_arg
from app.observability.logging import get_logger

logger = get_logger(__name__)

CHART_TYPES = ["bar", "barh", "line", "pie", "scatter", "area"]

# Past roughly this, a category label cannot be printed under a vertical bar
# without being rotated or truncated, and both read badly on a slide. The
# figure is the label length the axis can carry at slide size, divided among
# the categories - so six long names trip it and twelve short ones do not.
HORIZONTAL_LABEL_BUDGET = 90
LABEL_MODES = ["auto", "none", "all", "terminal", "end"]
VALUE_FORMATS = ["number", "percent", "currency"]

MAX_CATEGORIES = 30
MAX_SERIES = 6
MAX_TABLE_COLUMNS = 8
MAX_TABLE_ROWS = 15
MAX_METRICS = 4

# Cells standing in for "no value". A table made mostly of these is a
# research gap presented as a finding.
_EMPTY_CELLS = frozenset({"", "-", "–", "—", "n/a", "na", "tbd", "?"})
MAX_EMPTY_CELL_RATIO = 0.34


def _as_number(value) -> float | None:
    """A plain number, or None when the value carries formatting."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_list(value) -> list:
    """Accept a real list or a JSON string of one - models send both."""
    parsed = coerce_json_arg(value, value)
    if isinstance(parsed, list):
        return parsed
    return []


def _orient(chart_type: str, categories: list[str]) -> tuple[str, str]:
    """
    Turn a bar chart on its side when its labels need the room.

    "Enterprise software and services" under a vertical bar is either rotated
    forty-five degrees or truncated, and both read badly on a slide. Which of
    the two happens is a matplotlib detail; that the chart is worse either way
    is not.

    Decided here rather than asked of the model, for the same reason the rest
    of this module validates rather than trusts: label geometry is arithmetic,
    and a tool that already normalises its input should not make the caller
    reason about pixel widths. A caller that explicitly asked for barh, or for
    anything that is not a bar, is left alone.
    """
    if chart_type != "bar" or not categories:
        return chart_type, ""

    total = sum(len(str(c)) for c in categories)
    if total <= HORIZONTAL_LABEL_BUDGET:
        return chart_type, ""

    longest = max(categories, key=lambda c: len(str(c)))
    return "barh", (
        f". Drawn as horizontal bars: the category labels total {total} "
        f"characters (longest {str(longest)!r}), which will not print under "
        "vertical bars without being rotated or truncated"
    )


class BuildChartTool:
    """Validate and normalise a chart before it reaches a slide."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="build_chart",
            description=(
                "Build a validated chart for one slide. Call this INSTEAD OF "
                "writing chart JSON yourself whenever a slide's point is carried "
                "by numbers - a trend over time, a comparison across categories, "
                "a breakdown of a whole, or a relationship between two measures. "
                "The chart is attached to the slide automatically, so "
                "design_slide_content only has to write the title and a short "
                "interpretation. Only chart figures that actually appear in the "
                "source material or research; never invent or estimate values."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="slide_index", type="integer",
                    description="0-based index of the slide this chart belongs to",
                ),
                ToolParameter(
                    name="chart_type", type="string", enum=CHART_TYPES,
                    description=(
                        "line for change over time, bar for comparison across "
                        "categories, barh for the same comparison when the "
                        "category names are long (a ranked list of companies or "
                        "sectors), pie for parts of one whole, scatter for a "
                        "relationship between two measures, area for a cumulative "
                        "total. Ask for bar and the tool switches to barh by "
                        "itself when the labels need it."
                    ),
                ),
                ToolParameter(
                    name="categories", type="array", items={"type": "string"},
                    description=(
                        "The x-axis labels (or pie slice names), in the order they "
                        'should appear, e.g. ["2021", "2022", "2023"]'
                    ),
                ),
                ToolParameter(
                    name="series", type="array",
                    items={
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": (
                                    "What this series measures, including its unit, "
                                    'e.g. "Revenue (US$ billions)"'
                                ),
                            },
                            "values": {
                                "type": "array",
                                "items": {"type": "number"},
                                "description": (
                                    "One plain number per category, in the same order. "
                                    "No units, currency symbols or % signs inside the "
                                    "numbers - those belong in the series name."
                                ),
                            },
                            "forecastFrom": {
                                "type": "integer",
                                "description": (
                                    "0-based index from which the values are PROJECTED "
                                    "rather than measured. Those points render dashed "
                                    "and outlined so a reader can see which part of the "
                                    "chart is evidence. The slide must carry a footnote "
                                    "saying what the projection is based on, and a [^n] "
                                    "marker on the affected category or series name."
                                ),
                            },
                            "measured": {
                                "type": "array",
                                "items": {"type": "number"},
                                "description": (
                                    "Optional: the MEASURED portion of each value, where "
                                    "part of a figure is real and part is extrapolated "
                                    "(e.g. a full-year figure projected from a half-year "
                                    "actual). The bar is drawn solid to this and "
                                    "outlined to the full value."
                                ),
                            },
                        },
                        "required": ["name", "values"],
                    },
                    description=(
                        "One entry per data series. A pie takes exactly one. Every "
                        "series must have exactly as many values as there are categories."
                    ),
                ),
                ToolParameter(
                    name="y_axis_title", type="string", required=False,
                    description=(
                        "What the value axis measures, with its unit. Defaults to the "
                        "series name when there is only one series."
                    ),
                ),
                ToolParameter(
                    name="x_axis_title", type="string", required=False,
                    description="What the category axis represents, if not obvious from the labels",
                ),
                ToolParameter(
                    name="value_format", type="string", enum=VALUE_FORMATS, required=False,
                    description="How the values read: number (default), percent, or currency",
                ),
                ToolParameter(
                    name="title", type="string", required=False,
                    description=(
                        "A chart title. Omit it when the slide's own heading already "
                        "says the same thing - a repeated heading reads as a stutter."
                    ),
                ),
                ToolParameter(
                    name="unit_line", type="string", required=False,
                    description=(
                        "The unit, as a second line under the title - \"thousands\", "
                        "\"$ billion\". Use this rather than repeating the unit up the "
                        "side of the plot; it is what a reader looks for first and it "
                        "does not have to be read sideways."
                    ),
                ),
                ToolParameter(
                    name="label_mode", type="string", enum=LABEL_MODES, required=False,
                    description=(
                        "Where the numbers go: 'end' labels every bar at its end (which "
                        "is what lets a chart drop its axis entirely), 'terminal' labels "
                        "only the last point of a line, 'all' labels everything, 'none' "
                        "relies on the axis. Defaults to the deck's own convention."
                    ),
                ),
                region_param(),
                slot_param('"bridge" and "mix" on one financial page'),
            ],
        )

    @classmethod
    async def run(
        cls,
        slide_index: int,
        chart_type: str,
        categories,
        series,
        y_axis_title: str = "",
        x_axis_title: str = "",
        value_format: str = "number",
        title: str = "",
        unit_line: str = "",
        label_mode: str = "",
        region_id: str = "",
        slot: str = "",
    ) -> ToolResult:
        start = time.time()
        categories = [str(c) for c in _as_list(categories)]
        raw_series = _as_list(series)

        if not categories:
            return cls._error("categories is empty. Give one label per data point.", start)
        if len(categories) > MAX_CATEGORIES:
            return cls._error(
                f"{len(categories)} categories is too many to read on a slide "
                f"(max {MAX_CATEGORIES}). Aggregate them, or chart a subset.",
                start,
            )
        if not raw_series:
            return cls._error(
                'series is empty. Give at least one {"name": ..., "values": [...]}.',
                start,
            )
        if len(raw_series) > MAX_SERIES:
            return cls._error(
                f"{len(raw_series)} series is more than a slide-sized chart can "
                f"distinguish (max {MAX_SERIES}).",
                start,
            )

        chart_type = (chart_type or "bar").lower()
        if chart_type not in CHART_TYPES:
            return cls._error(
                f"chart_type {chart_type!r} is not supported. Use one of: "
                f"{', '.join(CHART_TYPES)}.",
                start,
            )
        if chart_type == "pie" and len(raw_series) > 1:
            return cls._error(
                "A pie shows parts of ONE whole, so it takes exactly one series. "
                "Use a bar chart to compare several series across categories.",
                start,
            )

        clean_series = []
        for position, entry in enumerate(raw_series):
            entry = coerce_json_arg(entry, entry)
            if not isinstance(entry, dict):
                return cls._error(
                    f"series[{position}] is not an object. Each series must be "
                    '{"name": "...", "values": [...]}.',
                    start,
                )
            name = str(entry.get("name") or "").strip()
            if not name:
                return cls._error(
                    f"series[{position}] has no name. The name is what labels the "
                    "value axis or the legend, and is where the unit belongs.",
                    start,
                )

            raw_values = _as_list(entry.get("values"))
            values = [_as_number(v) for v in raw_values]
            if any(v is None for v in values):
                bad = next(
                    raw for raw, v in zip(raw_values, values) if v is None
                )
                return cls._error(
                    f"series {name!r} has a value that is not a plain number: "
                    f"{bad!r}. Put units, currency symbols and % signs in the series "
                    "name, not in the values.",
                    start,
                )
            if len(values) != len(categories):
                return cls._error(
                    f"series {name!r} has {len(values)} values but there are "
                    f"{len(categories)} categories. Every series needs exactly one "
                    "value per category - use null only if you genuinely have no "
                    "figure, and prefer dropping the category instead.",
                    start,
                )
            if chart_type == "pie" and any(v < 0 for v in values):
                return cls._error(
                    "A pie cannot show negative values. Use a bar chart.", start
                )
            entry_out = {"name": name, "values": values}
            forecast_from = entry.get("forecastFrom")
            if forecast_from is not None:
                try:
                    index = int(forecast_from)
                except (TypeError, ValueError):
                    return cls._error(
                        f"series {name!r} has a forecastFrom that is not an "
                        f"index: {forecast_from!r}.",
                        start,
                    )
                if not 0 <= index < len(values):
                    return cls._error(
                        f"series {name!r} has forecastFrom={index} but only "
                        f"{len(values)} values. It is the 0-based index of the "
                        "first PROJECTED point.",
                        start,
                    )
                entry_out["forecastFrom"] = index
            measured = _as_list(entry.get("measured"))
            if measured:
                cleaned_measured = [_as_number(v) for v in measured]
                if any(v is None for v in cleaned_measured):
                    return cls._error(
                        f"series {name!r} has a measured value that is not a "
                        "plain number.",
                        start,
                    )
                if len(cleaned_measured) != len(values):
                    return cls._error(
                        f"series {name!r} has {len(cleaned_measured)} measured "
                        f"values but {len(values)} values - give one per point.",
                        start,
                    )
                entry_out["measured"] = cleaned_measured
            clean_series.append(entry_out)

        if chart_type == "pie" and sum(clean_series[0]["values"]) <= 0:
            return cls._error(
                "The pie's values sum to zero, so there are no shares to draw.", start
            )

        chart_type, orientation_note = _orient(chart_type, categories)

        chart: dict = {
            "chartType": chart_type,
            "categories": categories,
            "series": clean_series,
        }
        if y_axis_title.strip():
            chart["yAxisTitle"] = y_axis_title.strip()
        if x_axis_title.strip():
            chart["xAxisTitle"] = x_axis_title.strip()
        if (value_format or "").lower() in VALUE_FORMATS:
            chart["valueFormat"] = value_format.lower()
        if title.strip():
            chart["title"] = title.strip()
        if unit_line.strip():
            chart["unitLine"] = unit_line.strip()
        if (label_mode or "").lower() in LABEL_MODES:
            chart["labelMode"] = label_mode.lower()

        if any(s.get("forecastFrom") is not None or s.get("measured")
               for s in clean_series):
            chart["marksAProjection"] = True

        logger.info(
            "chart_built",
            slide_index=slide_index, chart_type=chart_type,
            series=len(clean_series), categories=len(categories),
        )
        return ToolResult(
            success=True,
            data={
                "slideIndex": slide_index,
                "content": bind({"type": "chart", "chart": chart}, region_id, slot),
                "summary": (
                    f"{chart_type} chart with {len(clean_series)} series over "
                    f"{len(categories)} categories, attached to slide "
                    f"{slide_index + 1}{orientation_note}"
                ),
            },
            duration_ms=int((time.time() - start) * 1000),
        )

    @staticmethod
    def _error(message: str, start: float) -> ToolResult:
        return ToolResult(
            success=False, error=message, duration_ms=int((time.time() - start) * 1000)
        )


class BuildTableTool:
    """Validate and normalise a table before it reaches a slide."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="build_table",
            description=(
                "Build a validated table for one slide. Call this INSTEAD OF writing "
                "table JSON yourself when the content is genuinely tabular - the same "
                "attributes compared across several things. The table is attached to "
                "the slide automatically. Do not use a table for prose that reads "
                "better as bullets, or for numbers a chart would show better."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="slide_index", type="integer",
                    description="0-based index of the slide this table belongs to",
                ),
                ToolParameter(
                    name="columns", type="array", items={"type": "string"},
                    description=(
                        "Column headers, kept short. Put units in the header "
                        '("Revenue ($B)") rather than repeating them in every cell.'
                    ),
                ),
                ToolParameter(
                    name="rows", type="array",
                    items={"type": "array", "items": {"type": "string"}},
                    description=(
                        "The data rows, each with exactly one cell per column. Keep "
                        "cell text to a few words - a table is not a place for "
                        "sentences. A final row labelled \"Total\" is picked out "
                        "automatically."
                    ),
                ),
                region_param(),
                slot_param('"revenue" and "headcount" on one comparison page'),
            ],
        )

    @classmethod
    async def run(cls, slide_index: int, columns, rows,
                  region_id: str = "", slot: str = "") -> ToolResult:
        start = time.time()
        columns = [str(c).strip() for c in _as_list(columns)]
        raw_rows = _as_list(rows)

        if len(columns) < 2:
            return cls._error(
                "A table needs at least two columns; with one, use bullets instead.",
                start,
            )
        if len(columns) > MAX_TABLE_COLUMNS:
            return cls._error(
                f"{len(columns)} columns will not read at slide size "
                f"(max {MAX_TABLE_COLUMNS}). Drop the least important ones.",
                start,
            )
        if not raw_rows:
            return cls._error("rows is empty - there is nothing to tabulate.", start)
        if len(raw_rows) > MAX_TABLE_ROWS:
            return cls._error(
                f"{len(raw_rows)} rows is more than fits legibly on one slide "
                f"(max {MAX_TABLE_ROWS}). Summarise, or split across slides.",
                start,
            )

        body: list[list[str]] = []
        for position, row in enumerate(raw_rows):
            cells = _as_list(row)
            if not cells and isinstance(row, dict):
                # Some models emit {"Segment": "...", "Revenue": "..."} instead
                # of a positional row; read it back in column order.
                cells = [row.get(column, "") for column in columns]
            if len(cells) != len(columns):
                return cls._error(
                    f"rows[{position}] has {len(cells)} cells but there are "
                    f"{len(columns)} columns. Every row needs one cell per column "
                    '(use "-" where you have no value).',
                    start,
                )
            body.append([str(c).strip() for c in cells])

        gap = cls._sparsity_error(columns, body)
        if gap:
            return cls._error(gap, start)

        logger.info(
            "table_built", slide_index=slide_index,
            columns=len(columns), rows=len(body),
        )
        return ToolResult(
            success=True,
            data={
                "slideIndex": slide_index,
                "content": bind(
                    {
                        "type": "table",
                        "table": {"headerRow": True, "rows": [columns, *body]},
                    },
                    region_id, slot,
                ),
                "summary": (
                    f"{len(body)}-row table across {len(columns)} columns, "
                    f"attached to slide {slide_index + 1}"
                ),
            },
            duration_ms=int((time.time() - start) * 1000),
        )

    @staticmethod
    def _sparsity_error(columns: list[str], body: list[list[str]]) -> str:
        """Refuse a table that is mostly "no value".

        A real run built a segment table with four of its six data cells set
        to "-", because the research had only turned up two figures. A table
        is a claim that these things are comparable; one made of dashes is a
        research gap presented as a finding, and it reads worse on a slide
        than the two numbers would as plain text.
        """
        cells = [cell for row in body for cell in row]
        if not cells:
            return ""

        empty = sum(1 for cell in cells if cell.strip().lower() in _EMPTY_CELLS)
        if empty / len(cells) > MAX_EMPTY_CELL_RATIO:
            return (
                f"{empty} of this table's {len(cells)} cells have no value. A table "
                "of dashes presents a gap in the research as a finding - drop the "
                "columns or rows you have no figures for, or state the few numbers "
                "you do have as bullets instead."
            )

        for index, column in enumerate(columns):
            values = [row[index] for row in body if index < len(row)]
            if values and all(v.strip().lower() in _EMPTY_CELLS for v in values):
                return (
                    f"The {column!r} column has no values at all. Remove it rather "
                    "than rendering an empty column."
                )
        return ""

    @staticmethod
    def _error(message: str, start: float) -> ToolResult:
        return ToolResult(
            success=False, error=message, duration_ms=int((time.time() - start) * 1000)
        )


class BuildKeyMetricsTool:
    """A row of headline figures - the one visual a qualitative slide can carry."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="build_key_metrics",
            description=(
                "Build a row of 2-4 headline figures for one slide, each a large "
                "number with a short label underneath. Use this when a slide has a "
                "handful of standout numbers that do not form a series - a single "
                "growth rate, a headcount, a market share - where a chart would have "
                "nothing to plot but plain bullets waste them. Attached to the slide "
                "automatically."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="slide_index", type="integer",
                    description="0-based index of the slide these figures belong to",
                ),
                ToolParameter(
                    name="metrics", type="array",
                    items={
                        "type": "object",
                        "properties": {
                            "value": {
                                "type": "string",
                                "description": 'The figure as it should read, e.g. "$36.2B", "+3%", "40,300"',
                            },
                            "label": {
                                "type": "string",
                                "description": "What the figure is, in a few words",
                            },
                        },
                        "required": ["value", "label"],
                    },
                    description="2-4 figures. More than four stop reading as headlines.",
                ),
                region_param(),
                slot_param('"headline" and "risk" on one scorecard page'),
            ],
        )

    @classmethod
    async def run(cls, slide_index: int, metrics,
                  region_id: str = "", slot: str = "") -> ToolResult:
        start = time.time()
        raw = _as_list(metrics)

        if len(raw) < 2:
            return cls._error(
                "A metric row needs at least two figures; a single number reads "
                "better in the slide's own copy.",
                start,
            )
        if len(raw) > MAX_METRICS:
            return cls._error(
                f"{len(raw)} figures is more than a headline row can carry "
                f"(max {MAX_METRICS}). Keep the most important ones.",
                start,
            )

        clean = []
        for position, entry in enumerate(raw):
            entry = coerce_json_arg(entry, entry)
            if not isinstance(entry, dict):
                return cls._error(
                    f"metrics[{position}] is not an object. Each is "
                    '{"value": "...", "label": "..."}.',
                    start,
                )
            value = str(entry.get("value") or "").strip()
            label = str(entry.get("label") or "").strip()
            if not value or not label:
                return cls._error(
                    f"metrics[{position}] needs both a value and a label - a figure "
                    "with nothing saying what it measures is not information.",
                    start,
                )
            clean.append({"value": value, "label": label})

        logger.info("key_metrics_built", slide_index=slide_index, count=len(clean))
        return ToolResult(
            success=True,
            data={
                "slideIndex": slide_index,
                "content": bind(
                    {"type": "metrics", "metrics": {"items": clean}}, region_id, slot
                ),
                "summary": (
                    f"{len(clean)} headline figures attached to slide {slide_index + 1}"
                ),
            },
            duration_ms=int((time.time() - start) * 1000),
        )

    @staticmethod
    def _error(message: str, start: float) -> ToolResult:
        return ToolResult(
            success=False, error=message, duration_ms=int((time.time() - start) * 1000)
        )


BUILD_VISUAL_TOOLS = ("build_chart", "build_table", "build_key_metrics")
