"""
Citations have to reach the .pptx, not stop at the chat.

web_search results were already tracked and shown in the chat UI, but a slide
bullet saying "15% increase in revenue" gave someone reading the deck later no
way back to the source. These tests pin the pass that puts the trail in the
file itself - and, just as importantly, pin that it stays out of the way when
there is nothing to cite.

The appendix's layout is pinned hard here, because getting it wrong is what
produced the two defects reported against the first version: reference slides
wearing the title-slide design, and a 13th source stranded alone on a
continuation page that rendered as a full-bleed brand-coloured cover.
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


def _slide(*texts: str, refs: list[int | None] | None = None, layout_id: str = "7") -> dict:
    """A body slide shaped the way design_slide_content emits them."""
    refs = refs or [None] * len(texts)
    runs = []
    for text, ref in zip(texts, refs):
        run: dict = {"text": text}
        if ref is not None:
            run["sourceRef"] = ref
        runs.append(run)
    return {
        "slideIndex": 0,
        "layoutId": layout_id,
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

    def test_a_publication_date_is_carried_when_the_backend_gave_one(self):
        sources = collect_sources([
            {"url": "https://a.com", "title": "Q3", "published": "2026-03-11"}
        ])
        assert sources[0]["published"] == "2026-03-11"

    def test_a_source_with_no_date_claims_none(self):
        assert collect_sources(_research("https://a.com"))[0]["published"] == ""


class TestDisabledAndEmpty:
    def test_off_is_a_pass_through(self):
        slides = [_slide("Revenue grew 15%", refs=[1])]
        got, sources = apply_source_citations(slides, _research("https://a.com"), enabled=False)
        assert got is slides
        assert sources == []
        assert "citation" not in _body_runs(got[0])[0]

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
    """
    A marker is its own run, not text glued onto the bullet, so that the "[1]"
    is the only clickable part - a paragraph-level link would have made the
    whole sentence a hyperlink.
    """

    def test_a_tagged_run_gets_a_linked_marker(self):
        slides = [_slide("Revenue grew 15%", refs=[1])]
        got, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
        run = _body_runs(got[0])[0]
        assert run["text"] == "Revenue grew 15%"
        assert run["citation"] == {"text": " [1]", "link": "https://a.com"}

    def test_an_untagged_run_is_left_alone(self):
        slides = [_slide("Revenue grew 15%", "Our strategy is working", refs=[1, None])]
        got, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
        runs = _body_runs(got[0])
        assert runs[0]["citation"]["text"] == " [1]"
        assert "citation" not in runs[1]

    def test_the_marker_links_to_the_source_it_numbers(self):
        slides = [_slide("From the second source", refs=[2])]
        got, sources = apply_source_citations(
            slides, _research("https://a.com", "https://b.com"), enabled=True
        )
        assert _body_runs(got[0])[0]["citation"] == {
            "text": " [2]",
            "link": "https://b.com",
        }
        assert sources[1]["url"] == "https://b.com"

    def test_a_ref_past_the_end_is_dropped(self):
        """A marker with no entry behind it implies a source that is not there."""
        slides = [_slide("Revenue grew 15%", refs=[9])]
        got, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
        assert "citation" not in _body_runs(got[0])[0]

    def test_the_internal_field_does_not_reach_the_renderer(self):
        """sourceRef is our own; the renderer must never see an unknown key."""
        slides = [_slide("Revenue grew 15%", refs=[1])]
        got, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
        assert "sourceRef" not in _body_runs(got[0])[0]

    def test_applying_twice_does_not_double_mark(self):
        slides = [_slide("Revenue grew 15%", refs=[1])]
        once, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
        twice, _ = apply_source_citations(once, _research("https://a.com"), enabled=True)
        assert _body_runs(twice[0])[0]["citation"]["text"] == " [1]"


class TestAppendix:
    def test_a_sources_slide_is_appended(self):
        slides = [_slide("Revenue grew 15%", refs=[1])]
        got, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
        assert len(got) == 2
        assert got[-1]["contents"][0]["runs"][0]["text"] == SOURCES_SLIDE_TITLE

    def test_the_appendix_numbering_matches_the_markers(self):
        slides = [_slide("a", "b", refs=[1, 2])]
        got, _ = apply_source_citations(
            slides, _research("https://a.com", "https://b.com"), enabled=True
        )
        listed = [r["text"] for r in got[-1]["contents"][1]["runs"]]
        assert listed[0].startswith("[1] ")
        assert listed[1].startswith("[2] ")

    def test_every_reference_line_is_a_link_to_its_source(self):
        got, _ = apply_source_citations(
            [_slide("a")], _research("https://a.com", "https://b.com"), enabled=True
        )
        links = [r["link"] for r in got[-1]["contents"][1]["runs"]]
        assert links == ["https://a.com", "https://b.com"]

    def test_a_very_long_title_is_trimmed_at_a_word_boundary(self):
        """A reference has to be recognisable, not complete - the link has the rest."""
        title = "Starbucks Corporation reports record fourth quarter and full year "
        title += "fiscal 2025 results including segment detail"
        got, _ = apply_source_citations(
            [_slide("a")], [{"url": "https://a.example/x", "title": title}], enabled=True
        )
        line = got[-1]["contents"][1]["runs"][0]["text"]
        assert "full year... - a.example" in line
        assert " ..." not in line, "trimmed mid-space rather than at a word"
        assert len(line) < len(title)

    def test_a_reference_names_its_domain_and_date_when_known(self):
        got, _ = apply_source_citations(
            [_slide("a")],
            [{"url": "https://ir.example.com/q3", "title": "Q3 results",
              "published": "2026-03-11"}],
            enabled=True,
        )
        assert got[-1]["contents"][1]["runs"][0]["text"] == (
            "[1] Q3 results - ir.example.com - 2026-03-11"
        )

    def test_a_reference_without_a_date_does_not_invent_one(self):
        got, _ = apply_source_citations(
            [_slide("a")],
            [{"url": "https://ir.example.com/q3", "title": "Q3 results"}],
            enabled=True,
        )
        assert got[-1]["contents"][1]["runs"][0]["text"] == "[1] Q3 results - ir.example.com"

    def test_a_domain_already_in_the_title_is_not_repeated(self):
        got, _ = apply_source_citations([_slide("a")], _research("https://a.com"), enabled=True)
        assert got[-1]["contents"][1]["runs"][0]["text"] == "[1] Report on a.com"

    def test_appended_slides_are_indexed_after_the_deck(self):
        slides = [_slide("a"), _slide("b")]
        got, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
        assert got[-1]["slideIndex"] == 2


class TestTheAppendixLooksLikeTheRestOfTheDeck:
    """
    Reported against the first version: "the last page had the first page
    design", and "the continued page had fully green background being
    inconsistent with the rest".

    Both came from the appendix naming no layout. The template renderer falls
    back to layout "1" - the title slide - for a slide with no layoutId, and
    the default renderer infers one from the content, where a page holding a
    single unbulleted line is indistinguishable from a title slide.
    """

    def test_the_appendix_borrows_the_decks_own_body_layout(self):
        got, _ = apply_source_citations(
            [_slide("a", "b", layout_id="7")], _research("https://a.com"), enabled=True
        )
        assert got[-1]["layoutId"] == "7"

    def test_the_most_used_body_layout_wins(self):
        slides = [
            _slide("a", "b", layout_id="3"),
            _slide("c", "d", layout_id="7"),
            _slide("e", "f", layout_id="7"),
        ]
        got, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
        assert got[-1]["layoutId"] == "7"

    def test_a_cover_slide_is_not_mistaken_for_a_body_layout(self):
        """One standfirst line is not a body; borrowing it is the original bug."""
        cover = {
            "slideIndex": 0,
            "layoutId": "1",
            "contents": [
                {"type": "text", "placeholderIdx": 0, "runs": [{"text": "Starbucks"}]},
                {"type": "text", "placeholderIdx": 1, "runs": [{"text": "FY2025"}]},
            ],
        }
        got, _ = apply_source_citations(
            [cover, _slide("a", "b", layout_id="7")], _research("https://a.com"), enabled=True
        )
        assert got[-1]["layoutId"] == "7"

    def test_the_placeholders_come_from_the_slide_the_layout_came_from(self):
        slide = {
            "slideIndex": 0,
            "layoutId": "9",
            "contents": [
                {"type": "text", "placeholderIdx": 4, "runs": [{"text": "Title"}]},
                {"type": "text", "placeholderIdx": 5,
                 "runs": [{"text": "a"}, {"text": "b"}]},
            ],
        }
        got, _ = apply_source_citations([slide], _research("https://a.com"), enabled=True)
        assert [c["placeholderIdx"] for c in got[-1]["contents"]] == [4, 5]

    def test_a_deck_with_no_layout_to_borrow_still_names_one(self):
        """Naming none is what let the renderer fall back to the title slide."""
        bare = {"slideIndex": 0, "contents": []}
        got, _ = apply_source_citations([bare], _research("https://a.com"), enabled=True)
        assert got[-1]["layoutId"] == "content"

    def test_every_appendix_page_uses_the_same_layout(self):
        urls = [f"https://s{i}.com" for i in range(MAX_SOURCES_PER_SLIDE + 3)]
        got, _ = apply_source_citations(
            [_slide("a", "b", layout_id="7")], _research(*urls), enabled=True
        )
        assert {s["layoutId"] for s in got[1:]} == {"7"}


class TestChunking:
    def test_the_appendix_continues_onto_another_slide_when_long(self):
        """Every source is cited; a long list overflows rather than being cut."""
        urls = [f"https://s{i}.com" for i in range(MAX_SOURCES_PER_SLIDE + 3)]
        got, sources = apply_source_citations([_slide("a")], _research(*urls), enabled=True)
        appendix = got[1:]
        assert len(appendix) == 2
        assert "continued" in appendix[1]["contents"][0]["runs"][0]["text"]
        listed = sum(len(s["contents"][1]["runs"]) for s in appendix)
        assert listed == len(sources) == len(urls)

    def test_the_pages_are_balanced_rather_than_filled_to_the_cap(self):
        """
        One over the cap used to render a full page plus a page of one, and
        that lone page was the one that came out looking like a cover.
        """
        urls = [f"https://s{i}.com" for i in range(MAX_SOURCES_PER_SLIDE + 1)]
        got, _ = apply_source_citations([_slide("a")], _research(*urls), enabled=True)
        per_page = [len(s["contents"][1]["runs"]) for s in got[1:]]
        assert min(per_page) >= len(urls) // 2
        assert sum(per_page) == len(urls)

    def test_no_page_ever_exceeds_the_cap(self):
        for count in range(1, 40):
            urls = [f"https://s{i}.com" for i in range(count)]
            got, _ = apply_source_citations([_slide("a")], _research(*urls), enabled=True)
            per_page = [len(s["contents"][1]["runs"]) for s in got[1:]]
            assert max(per_page) <= MAX_SOURCES_PER_SLIDE, count
            assert sum(per_page) == count

    def test_numbering_continues_across_pages(self):
        urls = [f"https://s{i}.com" for i in range(MAX_SOURCES_PER_SLIDE + 1)]
        got, _ = apply_source_citations([_slide("a")], _research(*urls), enabled=True)
        listed = [r["text"] for s in got[1:] for r in s["contents"][1]["runs"]]
        assert [line.split("]")[0][1:] for line in listed] == [
            str(n) for n in range(1, len(urls) + 1)
        ]


def test_the_original_deck_is_not_mutated_in_length():
    """Callers keep their own list; only the returned one carries the appendix."""
    slides = [_slide("a", refs=[1])]
    got, _ = apply_source_citations(slides, _research("https://a.com"), enabled=True)
    assert len(slides) == 1
    assert len(got) == 2
