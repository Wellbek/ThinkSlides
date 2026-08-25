"""
Where a figure can come from, and which to reach for first.

Five sources now, and the most convenient is not the best. Left unstated, the
most expressive tool becomes the default and a deck loses the one thing that
made its figures look like a set.

The order is by how much of the figure the deck still controls afterwards.

1. **A typed diagram.** `build_diagram`'s eight kinds are laid out to the
   deck's own grid in the deck's own colours. Nothing else is on-brand by
   construction.
2. **A figure from an MCP server.** A customer's own server knows things this
   service does not. When it returns SVG it arrives as native shapes in the
   deck's palette, like anything else that goes through the importer.
3. **Mermaid.** Right for a graph, where the number of nodes is what the
   content decides. It brings its own layout engine, which has never seen the
   pack - so it decides position, size and emphasis, and that is the cost.
4. **A drawn figure.** The most expressive and the least constrained: whatever
   the model draws is what appears, inside the pack's palette and its
   legibility floor.
5. **A generated image.** Last, and not because it looks worst. It is the only
   source that cannot be recoloured, checked or edited afterwards - a raster
   is a raster - and the only one that can invent something that looks like
   evidence, which is why `generate_image` refuses any subject that reads as a
   chart.

The first four end as shapes. The fifth cannot, which is the whole of the
difference: it needs the deck's illustration style rather than the palette
remapping the importer does, and that is issue #271.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    """One way a figure reaches a page."""

    name: str
    tool: str
    vector: bool
    why: str


ORDER: tuple[Source, ...] = (
    Source("typed diagram", "build_diagram", True,
           "laid out to the deck's grid; on-brand by construction"),
    Source("MCP figure", "mcp", True,
           "a customer's own server, imported as shapes"),
    Source("graph", "build_mermaid", True,
           "right for a graph; brings its own layout engine"),
    Source("drawn figure", "draw_svg", True,
           "most expressive, least constrained"),
    Source("generated image", "generate_image", False,
           "cannot be recoloured, checked or edited afterwards"),
)

BY_TOOL = {source.tool: source for source in ORDER}


def rank(tool: str) -> int:
    """How far down the order this tool sits. Lower is preferred."""
    for index, source in enumerate(ORDER):
        if source.tool == tool:
            return index
    return len(ORDER)


def preference_note() -> str:
    """The order, in words a tool description can carry."""
    return " ".join(
        f"{index + 1}. {source.name} ({source.tool}) - {source.why}."
        for index, source in enumerate(ORDER)
    )
