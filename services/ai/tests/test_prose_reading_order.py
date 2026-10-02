"""
A page's paragraphs are sequenced through the slots a paragraph sits in.

`reading_order` counted every text slot, so an eyebrow over a cover's title
was the first place to read - and a lone subtitle, at position 0 of one,
was cheaper there than in the standfirst built for it below the title. EY's
composed cover set "Where the next phase of growth comes from" in 11pt
capitals above the headline, with the standfirst left empty.
"""
from app.design import run as design_run
from app.design.select import NOT_PROSE_FRAME_ROLES, _slot_frame, reading_order


def test_only_prose_slots_are_ordered():
    for pack_id in design_run.available():
        pack = design_run.load(pack_id)
        for archetype in pack.archetypes:
            order = reading_order(archetype, pack, archetype.grid or pack.grid)
            for slot in archetype.slots:
                frame = _slot_frame(archetype, slot)
                if frame is not None and frame.role in NOT_PROSE_FRAME_ROLES:
                    assert slot.frame_id not in order, (pack_id, archetype.id, frame.id)


def test_a_covers_subtitle_goes_to_its_standfirst_not_its_eyebrow():
    pack = design_run.load("ey-iif-risk")
    doc = design_run.compose([
        {"slideIndex": 0, "role": "cover", "heading": "The year ahead",
         "contents": [{"type": "text", "runs": [
             {"text": "Where the next phase of growth comes from"}]}]},
    ], pack)
    bound = {frame.role: [b.text for b in frame.content if hasattr(b, "text")]
             for frame in doc.pages[0].frames}
    assert bound.get("standfirst") == ["Where the next phase of growth comes from"]
    assert not bound.get("eyebrow")
