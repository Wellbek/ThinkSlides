"""
Tool: compose_slide_regions

Divide a page, so that more than one exhibit can sit on it.

The region grammar offers the compositions measured off the template's own
pages, to be copied verbatim. That is the right first answer and it stays the
preferred one - a deck should look like its template. It is simply not an
answer at all when the template contains nothing resembling what this slide
needs, and the fallback until now was "leave regions out", which renders the
slide in its placeholders and caps it at one exhibit.

So this is the second answer, not a replacement for the first. The model names
a composition and a proportion; ``renderer.compositions`` turns that into
fractional geometry; ``renderer.regions.validate_regions`` checks it before it
can reach a slide. Nothing here lets a model write coordinates, because a model
asked for coordinates produces a rail of 0.31 beside a panel of 0.71 and the
overlap is only noticed when content is drawn over content.

The returned region ids are the point of the call: they are what
``build_chart(region_id=...)`` and the block builders bind to.
"""

from __future__ import annotations

import time

from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.tools._json_extract import coerce_json_arg
from app.observability.logging import get_logger
from app.renderer import compositions, regions as region_model

logger = get_logger(__name__)

COMPOSITION_NAMES = sorted(compositions.COMPOSITIONS)


class ComposeSlideRegionsTool:
    """Resolve a named composition onto one slide."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="compose_slide_regions",
            description=(
                "Divide one slide into named areas, so it can carry more than one "
                "exhibit. Call this BEFORE the build_* tools for that slide, then "
                "pass the region ids it returns as their region_id. "
                "Use it only when the slide genuinely has two or more different "
                "things to show and none of the template's own compositions fits - "
                "a slide with one chart on it needs no regions at all, and a page "
                "divided into four areas that say the same thing reads worse than "
                "one that says it once."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="slide_index", type="integer",
                    description="0-based index of the slide being composed",
                ),
                ToolParameter(
                    name="composition", type="string", enum=COMPOSITION_NAMES,
                    description=(
                        "How to divide the page. " + compositions.describe()
                    ),
                ),
                ToolParameter(
                    name="proportion", type="number", required=False,
                    description=(
                        "How the page is divided, between 0 and 1. What it measures "
                        "depends on the composition: the left half's share of the "
                        "width for split, the rail's share for railAndPanel, the "
                        "strip's share of the height for stripAndGrid, the top row's "
                        "share for exhibitGrid. Omit for a balanced default."
                    ),
                ),
                ToolParameter(
                    name="parts", type="integer", required=False,
                    description=(
                        "How many bands, for bandStack only (2 to 6). Ignored by "
                        "every other composition, which have a fixed number of parts."
                    ),
                ),
                ToolParameter(
                    name="grounds", type="object", required=False,
                    description=(
                        'Optional {"regionId": "#RRGGBB"} giving a part its own '
                        "background, e.g. a dark rail. Use the deck's own colours; "
                        "a ground invented for one slide is a slide that does not "
                        "look like the rest of the deck."
                    ),
                ),
            ],
        )

    @classmethod
    async def run(cls, slide_index: int, composition: str,
                  proportion: float | None = None,
                  parts: int | None = None,
                  grounds=None) -> ToolResult:
        start = time.time()

        proportion = _as_number(proportion)
        parts = _as_int(parts)
        grounds = coerce_json_arg(grounds, None)
        if grounds is not None and not isinstance(grounds, dict):
            return _fail(
                'grounds must be an object of {"regionId": "#RRGGBB"}.', start
            )

        try:
            tree = compositions.compose(
                str(composition or ""), proportion, parts, grounds
            )
        except compositions.CompositionError as error:
            return _fail(str(error), start)

        # The composer is trusted to produce sound geometry and is checked
        # anyway. This is the same validation the slide would face at render
        # time, run here so a bad composition fails at the call that made it
        # rather than surfacing three tools later as a dropped exhibit.
        errors, warnings = region_model.validate_regions(tree)
        if errors:
            logger.error(
                "composition_produced_invalid_regions",
                composition=composition, proportion=proportion, errors=errors,
            )
            return _fail(
                "that composition did not resolve to a usable page: "
                + "; ".join(errors),
                start,
            )

        bindable = [r["id"] for r in tree if r["id"] not in compositions.RESERVED]
        logger.info(
            "slide_composed", slide_index=slide_index,
            composition=composition, regions=len(tree),
        )
        return ToolResult(
            success=True,
            data={
                "slideIndex": slide_index,
                "regions": tree,
                "regionIds": bindable,
                "warnings": warnings,
                "summary": (
                    f"slide {slide_index + 1} composed as {composition} with "
                    f"regions {', '.join(bindable)} (plus a reserved footnote "
                    "band). Bind each exhibit with region_id."
                ),
            },
            duration_ms=int((time.time() - start) * 1000),
        )


def _as_number(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fail(message: str, start: float) -> ToolResult:
    return ToolResult(success=False, error=message,
                      duration_ms=int((time.time() - start) * 1000))
