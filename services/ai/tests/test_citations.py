"""
Citations have to reach the .pptx, not stop at the chat.

web_search results were already tracked and shown in the chat UI, but a slide
bullet saying "15% increase in revenue" gave someone reading the deck later no
way back to the source. These tests pin the pass that puts the trail in the
file itself - and, just as importantly, pin that it stays out of the way when
there is nothing to cite.
"""


from app.renderer.citations import (
    MAX_SOURCES_PER_SLIDE,
    SOURCES_SLIDE_TITLE,
    apply_source_citations,
    collect_sources,
)


def _research(*urls: str) -> list[dict]:
    return [
        {"url": url, "title": f"Report on {url.split('//')[-1].split('/')[0]}"}
        for url in urls
    ]


def _slide(*texts: str, refs: list[int | None] | None = None) -> dict:
    refs = refs or [None] * len(texts)
    runs = []
    for text, ref in zip(texts, refs):
        run: dict = {"text": text}
        if ref is not None:
            run["sourceRef"] = ref
        runs.append(run)
    return {
        "slideIndex": 0,
        "contents": [
            {"type": "text", "placeholderIdx": 0, "runs": [{"text": "Revenue"}]},
            {"type": "text", "placeholderIdx": 1, "runs": runs},
        ],
    }


def _body_runs(slide: dict) -> list[dict]:
    return slide["contents"][1]["runs"]


class TestCollectSources:
    def test_distinct_urls_keep_the_order_they_were_seen_in(self):
        sources = collect_sources(_research("https://a.com/x", "https://b.com/y"))
        assert [s["url"] for s in sources] == ["https://a.com/x", "https://b.com/y"]

    def test_a_repeated_url_is_one_reference(self):
        """Numbering has to be stable; the same page cited twice is [1] twice."""
        sources = collect_sources(_research("https://a.com/x", "https://a.com/x"))
        assert len(sources) == 1

    def test_a_source_without_a_title_falls_back_to_its_domain(self):
        sources = collect_sources([{"url": "https://www.example.com/report"}])
        assert sources[0]["title"] == "example.com"
        assert sources[0]["domain"] == "example.com"

    def test_entries_without_a_url_are_skipped(self):
        assert collect_sources([{"title": "no link"}, {"url": "  "}, None]) == []


class TestDisabledAndEmpty:
    def test_off_is_a_pass_through(self):
        slides = [_slide("Revenue grew 15%", refs=[1])]
        got, sources = apply_source_citations(slides, _research("https://a.com"), enabled=False)
        assert got is slides
        assert sources == []
        assert _body_runs(got[0])[0]["text"] == "Revenue grew 15%"

    def test_no_research_adds_no_appendix(self):
        """Simple mode cites nothing, so the toggle must be a no-op."""
        slides = [_slide("Revenue grew 15%")]
        got, sources = apply_source_citations(slides, [], enabled=True)
        assert len(got) == 1
        assert sources == []

    def test_research_that_turned_nothing_up_adds_no_appendix(self):
        slides = [_slide("Revenue grew 15%")]
        got, _ = apply_source_citations(slides, [{"title": "no link"}], enabled=True)
        assert len(got) == 1


class TestMarkers:
    def test_a_tagged_run_gets_its_marker(self):
        slides = [_slide("Revenue grew 15%", refs=[1])]
        got, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
        assert _body_runs(got[0])[0]["text"] == "Revenue grew 15% [1]"

    def test_an_untagged_run_is_left_alone(self):
        slides = [_slide("Revenue grew 15%", "Our strategy is working", refs=[1, None])]
        got, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
        runs = _body_runs(got[0])
        assert runs[0]["text"] == "Revenue grew 15% [1]"
        assert runs[1]["text"] == "Our strategy is working"

    def test_the_marker_matches_the_reference_number(self):
        slides = [_slide("From the second source", refs=[2])]
        got, sources = apply_source_citations(
            slides, _research("https://a.com", "https://b.com"), enabled=True
        )
        assert _body_runs(got[0])[0]["text"].endswith("[2]")
        assert sources[1]["url"] == "https://b.com"

    def test_a_ref_past_the_end_is_dropped(self):
        """A marker with no entry behind it implies a source that is not there."""
        slides = [_slide("Revenue grew 15%", refs=[9])]
        got, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
        assert _body_runs(got[0])[0]["text"] == "Revenue grew 15%"

    def test_the_internal_field_does_not_reach_the_renderer(self):
        """sourceRef is our own; the renderer must never see an unknown key."""
        slides = [_slide("Revenue grew 15%", refs=[1])]
        got, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
        assert "sourceRef" not in _body_runs(got[0])[0]

    def test_applying_twice_does_not_double_mark(self):
        slides = [_slide("Revenue grew 15%", refs=[1])]
        once, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
        twice, _ = apply_source_citations(once, _research("https://a.com"), enabled=True)
        assert _body_runs(twice[0])[0]["text"] == "Revenue grew 15% [1]"


class TestAppendix:
    def test_a_sources_slide_is_appended(self):
        slides = [_slide("Revenue grew 15%", refs=[1])]
        got, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
        assert len(got) == 2
        appendix = got[-1]
        assert appendix["contents"][0]["runs"][0]["text"] == SOURCES_SLIDE_TITLE

    def test_the_appendix_numbering_matches_the_markers(self):
        slides = [_slide("a", "b", refs=[1, 2])]
        got, _ = apply_source_citations(
            slides, _research("https://a.com", "https://b.com"), enabled=True
        )
        listed = [r["text"] for r in got[-1]["contents"][1]["runs"]]
        assert listed[0].startswith("[1] ")
        assert listed[1].startswith("[2] ")

    def test_the_appendix_continues_onto_another_slide_when_long(self):
        """Every source is cited; a long list overflows rather than being cut."""
        urls = [f"https://s{i}.com" for i in range(MAX_SOURCES_PER_SLIDE + 3)]
        got, sources = apply_source_citations([_slide("a")], _research(*urls), enabled=True)
        appendix = got[1:]
        assert len(appendix) == 2
        assert "continued" in appendix[1]["contents"][0]["runs"][0]["text"]
        listed = sum(len(s["contents"][1]["runs"]) for s in appendix)
        assert listed == len(sources) == len(urls)

    def test_appended_slides_are_indexed_after_the_deck(self):
        slides = [_slide("a"), _slide("b")]
        got, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
        assert got[-1]["slideIndex"] == 2

    def test_the_appendix_uses_an_ordinary_layout(self):
        """
        The renderer clones real layout XML; asking for a layout the template
        has is what keeps this looking intentional rather than bolted on.
        """
        got, _ = apply_source_citations([_slide("a")], _research("https://a.com"), enabled=True)
        assert got[-1]["layoutName"] == "Title and Content"
        assert [c["type"] for c in got[-1]["contents"]] == ["text", "text"]


def test_the_original_deck_is_not_mutated_in_length():
    """Callers keep their own list; only the returned one carries the appendix."""
    slides = [_slide("a", refs=[1])]
    got, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
    assert len(slides) == 1
    assert len(got) == 2
