"""
What containment does when a script gets past the gate.

`gate.py` is the layer that has to be right, and `test_sandbox_gate.py` covers
it. These scripts deliberately name things the gate would refuse - `os`,
`socket`, a 3GB allocation - because the question here is different: if
something did get through, what stops it. A containment layer nobody tested
under load is a claim rather than a control.

Run against the real process, not a mock. A `setrlimit` that was applied to a
fake subprocess proves nothing at all.
"""

import io
import os

import pytest

from app.agent.sandbox import adopt, runner


def _deck(slides: int = 1) -> bytes:
    from pptx import Presentation

    buffer = io.BytesIO()
    deck = Presentation()
    for _ in range(slides):
        deck.slides.add_slide(deck.slide_layouts[6])
    deck.save(buffer)
    return buffer.getvalue()


class TestTheScriptRuns:
    def test_a_working_script_returns_what_it_printed(self):
        result = runner.run("print('the deck is fine')")

        assert result.ok, result.failure
        assert "the deck is fine" in result.stdout

    def test_it_may_use_the_libraries_the_gate_allows(self):
        result = runner.run(
            "import statistics\n"
            "print(statistics.mean([1.0, 2.0, 6.0]))\n"
        )

        assert result.ok, result.failure
        assert "3" in result.stdout

    def test_a_script_that_raises_hands_back_its_traceback(self):
        """`draw_svg` states why: the model gets to try again while it still
        has the context to, and it cannot fix what it cannot see."""
        result = runner.run("raise ValueError('no figures for this slide')")

        assert not result.ok
        assert "no figures for this slide" in result.failure
        assert "ValueError" in result.failure


class TestTheProcessIsContained:
    def test_the_environment_carries_no_secret(self):
        """
        `preview_generator.py` passes `**os.environ` to LibreOffice. That is
        fine for LibreOffice and would be handing `ZHIPU_API_KEY`,
        `DATABASE_URL` and `SETTINGS_ENCRYPTION_KEY` to agent-written code.
        """
        result = runner.run("import os\nprint(sorted(os.environ))")

        assert result.ok, result.failure
        for leaked in ("KEY", "SECRET", "DATABASE", "TOKEN", "PASSWORD"):
            assert leaked not in result.stdout.upper(), leaked

    def test_it_inherits_none_of_this_processs_own_variables(self, monkeypatch):
        monkeypatch.setenv("TS_A_SECRET_FOR_THIS_TEST", "hunter2")

        result = runner.run("import os\nprint(os.environ.get('TS_A_SECRET_FOR_THIS_TEST'))")

        assert result.ok, result.failure
        assert "hunter2" not in result.stdout

    def test_the_network_is_unreachable(self):
        result = runner.run(
            "import socket\n"
            "s = socket.socket()\n"
            "s.settimeout(3)\n"
            "s.connect(('1.1.1.1', 53))\n"
            "print('REACHED')\n"
        )

        if not result.networked:
            pytest.skip("this host does not allow unprivileged network namespaces")
        assert not result.ok
        assert "REACHED" not in result.stdout

    def test_a_script_that_never_finishes_is_stopped(self):
        """The one the AST gate deliberately does not try to catch: no static
        check decides whether a loop terminates, so the wall clock does."""
        result = runner.run("while True:\n    pass")

        assert not result.ok
        assert result.failure

    def test_a_script_that_asks_for_all_the_memory_is_stopped(self):
        result = runner.run("x = bytearray(3_000_000_000)")

        assert not result.ok

    def test_a_fork_bomb_runs_out_of_processes(self):
        result = runner.run(
            "import os\n"
            "for _ in range(500):\n"
            "    try:\n"
            "        os.fork()\n"
            "    except OSError:\n"
            "        print('REFUSED')\n"
            "        break\n"
        )

        # Either the limit refused a fork or the whole thing was killed.
        # What must not happen is 500 processes.
        assert "REFUSED" in result.stdout or not result.ok

    def test_it_cannot_write_outside_its_work_dir(self):
        """Belt and braces: the gate refuses the path, and the process is not
        allowed to write there either."""
        result = runner.run(
            "try:\n"
            "    open('/etc/ts-sandbox-probe', 'w').write('x')\n"
            "    print('WROTE')\n"
            "except OSError as e:\n"
            "    print('REFUSED', e.errno)\n"
        )

        assert "WROTE" not in result.stdout
        assert not os.path.exists("/etc/ts-sandbox-probe")


class TestTheDeckCrossesAsAFile:
    def test_a_script_can_edit_the_deck_and_the_edit_comes_back(self):
        result = runner.run(
            "from pptx import Presentation\n"
            "from pptx.util import Inches\n"
            "deck = Presentation('deck.pptx')\n"
            "box = deck.slides[0].shapes.add_textbox("
            "Inches(1), Inches(1), Inches(4), Inches(1))\n"
            "box.text_frame.text = 'Revenue held at 14%'\n"
            "deck.save('deck.pptx')\n",
            deck=_deck(),
        )

        assert result.ok, result.failure
        edited = result.outputs.get(runner.DECK_NAME)
        assert edited

        from pptx import Presentation

        shapes = Presentation(io.BytesIO(edited)).slides[0].shapes
        assert any(shape.has_text_frame
                   and "Revenue held at 14%" in shape.text_frame.text
                   for shape in shapes)

    def test_the_work_dir_is_gone_afterwards(self):
        result = runner.run("open('leftover.txt', 'w').write('x')",
                            wants=("leftover.txt",))

        assert result.ok, result.failure
        # The file came back, and nothing of it is left on disk.
        assert result.outputs.get("leftover.txt") == b"x"

    def test_a_file_the_caller_did_not_ask_for_does_not_come_back(self):
        result = runner.run("open('private.txt', 'w').write('x')")

        assert result.ok, result.failure
        assert "private.txt" not in result.outputs


class TestTheDeckIsCheckedBeforeItIsBelieved:
    def test_an_ordinary_deck_is_adopted(self):
        assert adopt.problem(_deck()) is None

    def test_a_deck_python_pptx_wrote_is_not_refused_for_its_own_parts(self):
        """
        A blanket `.bin` rule refused every real deck.

        `ppt/printerSettings/printerSettings1.bin` is an ordinary OOXML part
        that python-pptx writes into everything it saves, and so is the
        `/package/2006/relationships/` namespace that a substring match for
        "/package" hit. Both were caught by testing against a deck this
        service actually produces rather than against a hand-written fixture.
        """
        assert adopt.problem(_deck(3)) is None

    @pytest.mark.parametrize("part", [
        "ppt/vbaProject.bin",
        "ppt/embeddings/workbook.xlsx",
        "ppt/media/payload.exe",
        "ppt/activeX/activeX1.xml",
    ])
    def test_a_part_that_carries_code_is_refused_by_name(self, part):
        import zipfile

        buffer = io.BytesIO(_deck())
        with zipfile.ZipFile(buffer, "a") as archive:
            archive.writestr(part, b"MZ")

        problem = adopt.problem(buffer.getvalue())

        assert problem is not None
        assert part in problem

    def test_a_deck_that_fetches_something_when_opened_is_refused(self):
        import zipfile

        buffer = io.BytesIO(_deck())
        with zipfile.ZipFile(buffer, "a") as archive:
            archive.writestr(
                "ppt/_rels/extra.xml.rels",
                '<?xml version="1.0"?><Relationships xmlns='
                '"http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rX" Type="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships/attachedTemplate" '
                'Target="http://elsewhere/x.potx" TargetMode="External"/>'
                "</Relationships>",
            )

        problem = adopt.problem(buffer.getvalue())

        assert problem is not None
        assert "attachedTemplate" in problem

    def test_a_hyperlink_is_still_allowed(self):
        """A deck citing a real page is the point of citing it."""
        from pptx import Presentation
        from pptx.util import Inches

        buffer = io.BytesIO()
        deck = Presentation()
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
        run = box.text_frame.paragraphs[0].add_run()
        run.text = "source"
        run.hyperlink.address = "https://example.com/report"
        deck.save(buffer)

        assert adopt.problem(buffer.getvalue()) is None

    @pytest.mark.parametrize("data,expected", [
        (b"", "no deck"),
        (b"not a zip at all", "not a presentation"),
    ])
    def test_something_that_is_not_a_deck_is_refused(self, data, expected):
        problem = adopt.problem(data)

        assert problem is not None
        assert expected in problem

    def test_a_deck_past_the_size_limit_is_refused(self):
        problem = adopt.problem(b"x" * (adopt.MAX_BYTES + 1))

        assert problem is not None
        assert "limit" in problem

    def test_more_slides_than_the_run_declared_is_refused(self):
        problem = adopt.problem(_deck(3), max_slides=2)

        assert problem is not None
        assert "3 slides" in problem
