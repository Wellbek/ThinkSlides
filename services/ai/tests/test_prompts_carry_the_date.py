"""
Every prompt that reaches a model states today's date, dynamically.

Two failures motivated this, a week apart in the same run: a search issued as
"latest Green Climate Fund approved projects 2023", and a slide planned as
"Key Facts: 2023 GCF Project Approvals". In both, the model wrote the year its
training data ended because the prompt in front of it did not say otherwise -
the agent loop's system prompt did, but each tool builds its own prompt and
makes its own call.

So this is a structural check rather than a behavioural one: a new call site
that forgets the date is a bug that only shows up as a wrong year on a slide,
months later, and only if somebody notices.
"""

import ast
import datetime
import pathlib
import re

import pytest

APP = pathlib.Path(__file__).resolve().parent.parent / "app"

# Methods that send a prompt to a model.
_LLM_CALLS = ("chat", "chat_with_images", "create")

# Files that legitimately send a prompt without the date, and why. Anything
# not listed here has to carry it.
_EXEMPT = {
    # The transport layer. It forwards whatever it is handed and composes no
    # prompt of its own.
    "llm/provider.py": "builds no prompt",
    # "Reply with exactly: OK" - a connectivity probe whose entire point is a
    # fixed, minimal answer. A date paragraph would both cost tokens on every
    # click of "Test model" and give the model something else to reply with.
    "api/settings.py": "fixed-answer credential probe, generates no content",
    # The visual judge grades craft from rendered pixels - hierarchy,
    # composition, whether a chart is labelled. It writes no content and
    # reasons about nothing time-bound, so there is no year for it to get
    # wrong. It is also an eval-harness call rather than part of a run, so a
    # date paragraph would only add tokens to a call made many times over an
    # iteration.
    "eval/visual_judge.py": "grades rendered pages, generates no content",
}


def _llm_call_sites() -> dict[str, list[int]]:
    """Every source file under app/ that sends a prompt to a model."""
    found: dict[str, list[int]] = {}
    for path in APP.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - would fail the whole suite anyway
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in _LLM_CALLS:
                continue
            # `create` is common enough to need narrowing to the LLM SDKs.
            if node.func.attr == "create":
                src = ast.unparse(node.func)
                if not re.search(r"(completions|responses|messages)\.create$", src):
                    continue
            rel = str(path.relative_to(APP))
            found.setdefault(rel, []).append(node.lineno)
    return found


def test_the_scan_finds_the_call_sites_it_is_meant_to_guard():
    """A scan that silently matches nothing would pass every other test here."""
    sites = _llm_call_sites()

    assert "agent/loop.py" in sites
    assert "agent/tools/plan_presentation_structure.py" in sites
    assert len(sites) >= 10, sites


@pytest.mark.parametrize("rel", sorted(set(_llm_call_sites()) - set(_EXEMPT)))
def test_every_prompt_building_module_states_the_date(rel):
    source = (APP / rel).read_text()

    if rel == "agent/loop.py":
        # The loop states it inline in _build_system_prompt rather than through
        # the shared helper, because it also carries the "treat this as
        # authoritative" framing the tools do not need.
        assert "The current date is" in source
        assert "strftime" in source
        return

    assert "today_line" in source, (
        f"app/{rel} sends a prompt to a model without stating today's date. "
        "Prepend today_line() from app.agent.tools._today, or add it to "
        "_EXEMPT here with the reason."
    )


def _string_literals(path: pathlib.Path) -> list[tuple[int, str]]:
    """Every string in a file except the ones written for a human reader.

    A docstring or a comment-shaped literal explains the bug; only strings
    that get assembled into a prompt can carry a stale year to a model.
    """
    tree = ast.parse(path.read_text())
    prose = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
    ]


def test_no_prompt_hardcodes_a_year():
    """A literal year in an example does not merely go stale - it primes the
    model toward exactly the years it already wrongly believes are current."""
    offenders = []
    for rel in sorted(set(_llm_call_sites()) - set(_EXEMPT)):
        for lineno, value in _string_literals(APP / rel):
            # A URL dated by whoever published it - an XML namespace, an API
            # version - is not this file's year to choose.
            if "://" in value:
                continue
            if re.search(r"\b(?:19|20)\d{2}\b", value):
                offenders.append(f"app/{rel}:{lineno}: {value[:90]}")

    assert not offenders, (
        "hardcoded years in prompt-building modules - derive them from "
        "app.agent.tools._today instead:\n" + "\n".join(offenders)
    )


def test_the_rendered_tool_schemas_carry_no_stale_year():
    """The schemas are what the model reads while choosing its arguments."""
    import json

    from app.agent.registry import get_registry

    this_year = datetime.date.today().year
    schemas = json.dumps(get_registry().get_function_schemas())
    years = {int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", schemas)}

    assert years, "the scan found no years at all - the examples moved"
    assert all(this_year - 5 <= y <= this_year for y in years), sorted(years)


def test_the_specimen_deck_is_dated_from_today():
    """Its sample chart is shown to the user when they pick a direction; an
    axis that stops years ago reads as a stale product."""
    from app.renderer.specimen import default_content

    assert default_content().categories[-1] == str(datetime.date.today().year)


def test_the_generated_example_years_end_at_this_year():
    from app.agent.tools._today import example_quarter, recent_years

    assert recent_years()[-1] == str(datetime.date.today().year)
    assert str(datetime.date.today().year) in example_quarter()
