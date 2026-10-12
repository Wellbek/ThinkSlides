"""
The date reaches the prompts that write what the reader sees.

Regression: a run whose research correctly returned 2026 sources planned a
slide called "Key Facts: 2023 GCF Project Approvals". The agent loop's system
prompt states today's date, but plan_presentation_structure and
design_slide_content each make their own LLM call with their own prompt, and
neither said what day it was - so the model dated the headline from the year
its training data ended.
"""

import datetime
import re

import pytest

from app.agent.tools._today import today_line


def test_the_line_names_todays_date_both_ways():
    today = datetime.date.today()
    line = today_line()

    assert today.strftime("%Y-%m-%d") in line
    assert today.strftime("%d %B %Y").lstrip("0") in line or today.strftime("%d %B %Y") in line


def test_the_line_says_where_a_year_may_come_from():
    """Stating the date is not enough on its own - the failure was a model
    that had been told the date and still wrote a remembered year."""
    line = today_line().lower()

    assert "never from memory" in line
    assert str(datetime.date.today().year) in line


@pytest.mark.parametrize(
    "module,attr",
    [
        ("app.agent.tools.plan_presentation_structure", "PlanPresentationStructureTool"),
        ("app.agent.tools.design_slide_content", "DesignSlideContentTool"),
    ],
)
def test_the_copy_writing_tools_import_it(module, attr):
    """A tool that can write a year into the deck has to carry the date."""
    import importlib

    mod = importlib.import_module(module)

    assert hasattr(mod, "today_line"), f"{attr}'s module must prepend today_line()"
    source = open(mod.__file__).read()
    if attr == "DesignSlideContentTool":
        # Its prompt is built by compose_prompt, which states the date unless
        # compile (#323) has already stated it in the prompt every slide shares.
        prompt, _ = mod.compose_prompt({"title": "One"}, 0, {}, {}, "concise", {}, [])
        assert prompt.startswith(mod.today_line()), (
            f"{attr} must put today_line at the top of its prompt")
        return
    assert re.search(r'prompt = f"""\{today_line\(\)\}', source), (
        f"{attr} imports today_line but does not put it at the top of its prompt"
    )
