"""
#380: a re-placement must not make a page worse.

On the last validation round of technical-http-caching/20261012-162837-claude-code
a `replace` ruled out the two arrangements that held slide 3's copy, and the
cheapest one left set five sentences in a caption slot a third their size;
repair cannot set copy below its designed floor, and the last line ran off
the page. Reproduced here on that run's own language.
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.agent.tools._json_extract import extract_json_object
from app.agent.tools.build_diagram import BuildDiagramTool
from app.design import author_run, authoring, deck_format
from app.design import run as design_run
from app.design.repair import overflow_findings

RUN = (Path(__file__).resolve().parents[3] / "benchmark" / "evidence"
       / "technical-http-caching" / "20261012-162837-claude-code" / "cassette.jsonl")


@pytest.fixture(scope="module")
def pack():
    for line in RUN.read_text().splitlines():
        response = json.loads(line).get("response")
        content = response.get("content") if isinstance(response, dict) else ""
        if content and '"pages"' in content:
            languages = [language for language in
                         authoring.read(extract_json_object(content) or {})
                         if language.name == "Engineering Report"]
            fmt = deck_format.by_id(None)
            return author_run.packs_for(languages, fmt.canvas)[0]
    raise AssertionError("the run's languages are not in its cassette")


@pytest.fixture(scope="module")
def slide():
    flow = asyncio.run(BuildDiagramTool.run(
        slide_index=0, kind="flow",
        items=["200 + ETag", "Copy goes stale", "If-None-Match", "304 Not Modified"]))
    body = [
        "The origin sends an ETag with the body: an opaque validator naming this exact version of the response.",
        "While the copy is fresh, the cache serves it without asking; once it is stale, it must check before reuse.",
        "To check, the cache sends the request again with If-None-Match carrying the ETag it holds.",
        "If nothing changed, the origin answers 304 Not Modified with no body, and the cache keeps serving its copy.",
        "If it changed, the origin sends 200 with the new body and a new ETag, which replaces the stored copy.",
    ]
    return {"slideIndex": 0, "role": "content", "contents": [
        {"type": "text", "runs": [{"text": "An ETag turns a full re-download into a 304 Not Modified"}]},
        {"type": "text", "runs": [{"text": t, "bullet": False} for t in body]},
        flow.data["content"],
    ]}


def test_a_replace_that_would_overflow_keeps_the_arrangement_that_held_the_page(pack, slide):
    holding = frozenset({"claim-evidence", "claim-evidence-stacked"})

    doc = design_run.compose([slide], pack, avoid={0: holding})

    (page,) = doc.pages
    assert page.provenance.heading_frame.startswith("claim-evidence")
    assert not overflow_findings(doc, page, pack)
    # Nowhere else holds it, so the render reports it as unmovable and the
    # finding goes on to a redraft rather than another replace.
    assert page.provenance.alternatives == ()


def test_an_arrangement_the_copy_overflows_is_not_an_alternative(pack, slide):
    doc = design_run.compose([slide], pack)

    (page,) = doc.pages
    assert "exhibit" not in page.provenance.alternatives
    assert "claim-evidence-stacked" in page.provenance.alternatives
