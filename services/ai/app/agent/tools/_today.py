"""
The current date, for the prompts that write what the reader sees.

The agent loop's own system prompt states today's date, but the tools that
produce deck copy - the plan, the slide bodies - each make their own LLM call
with their own prompt, and none of them said what day it is. So a run whose
research correctly returned 2026 sources still planned a slide called "Key
Facts: 2023 GCF Project Approvals": the model dated the headline from the year
its training data ended, because nothing in that particular prompt contradicted
it.

A date on a slide is a claim to the reader like any other figure, so it belongs
in every prompt that can write one, not only in the loop's.
"""

from __future__ import annotations

import datetime


def today_line() -> str:
    """One paragraph stating the date, for the top of a copy-writing prompt."""
    today = datetime.datetime.now().astimezone()
    return (
        f"Today is {today.strftime('%A, %d %B %Y')} ({today.strftime('%Y-%m-%d')}). "
        "This is later than your training data. Any year you write - in a title, a "
        f"section name, a bullet, a label - comes from the material you were given or "
        f"from this date, never from memory: the year that feels like 'now' to you is "
        f"the year your training data ended, not {today.year}. If the material does not "
        "date a fact, do not date it yourself."
    )
