"""
Every argument a tool offers the model is one the tool can take.

design_slide_content offered `placement` in its schema and had no such
parameter, so every call that set it failed on an unexpected keyword - a
turn wasted each time, and a failure a weaker model may never recover from.
Arguments the loop consumes itself are declared in one place and stripped
before the tool runs; anything else the schema offers must be accepted.
"""

import inspect

import pytest

from app.agent.loop import _LOOP_ARGUMENTS, _tool_arguments
from app.agent.registry import get_registry


def _accepts(fn, name: str) -> bool:
    params = inspect.signature(fn).parameters
    return name in params or any(p.kind is p.VAR_KEYWORD for p in params.values())


@pytest.mark.parametrize("tool", sorted(t.name for t in get_registry().list_tools()))
def test_the_tool_accepts_everything_its_schema_offers(tool):
    definition, fn = get_registry().get(tool)
    consumed = _LOOP_ARGUMENTS.get(tool, frozenset())
    rejected = [p.name for p in definition.parameters
                if p.name not in consumed and not _accepts(fn, p.name)]
    assert not rejected, f"{tool} offers {rejected} but its function does not take them"


def test_what_the_loop_consumes_never_reaches_the_tool():
    args = {"slide_index": 2, "slide_outline": "{}", "placement": "insert"}
    assert _tool_arguments("design_slide_content", args) == {"slide_index": 2, "slide_outline": "{}"}
    # The loop still reads it from the call as the model made it.
    assert args["placement"] == "insert"
