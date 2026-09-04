"""End to end: a real deck, a real instruction, a verified result.

Everything else tests a layer. This tests the promise:

    open -> plan -> review -> apply -> verify -> export -> rollback

with the model's output going through the same validation a real one would.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slide_wright.inspect import inspect
from slide_wright.llm.client import StubProvider
from slide_wright.package import Package
from slide_wright.planner import plan
from slide_wright.session import Session

REAL_DECK = Path(r"C:/Users/mukun/Downloads/AgroLens - Project Phase-I final.pptx")


class TestSyntheticEndToEnd:
    def test_plan_review_apply_verify_export(self, adversarial_deck, tmp_path):
        session = Session.open(adversarial_deck, workspace=tmp_path / "ws")
        deck = session.deck()
        table = next(s for s in deck.all_shapes() if s.kind == "table")

        # A model proposes two changes; one is a hallucination.
        response = json.dumps([
            {"op": "set_table_cell", "slide": 3, "target": f"{table.id}/r1/c1",
             "before": "9.4x", "after": "11.8x", "rationale": "revised comps"},
            {"op": "set_text", "slide": 3, "target": "99999",
             "before": "nope", "after": "nope"},
        ])
        result = plan(deck, "update the Alpha Corp multiple to 11.8x",
                      deck_path=str(session.current.path),
                      provider=StubProvider([response]))

        assert len(result.dropped) == 1, "the hallucinated change must be dropped"

        changeset = session.propose("update the Alpha Corp multiple")
        for change in result.changeset.changes:
            changeset.add(change)
        changeset.approve_all()

        report = session.apply()

        assert report.deliverable
        assert report.unrequested_slide_changes == []
        assert report.fidelity.fidelity_score > 98.0
        assert not report.fidelity.native_losses

        out = session.export(tmp_path / "final.pptx")
        assert "11.8x" in inspect(out).slide(3).text

        # And the original is still reachable, untouched.
        session.rollback(0)
        assert session.current.path.read_bytes() == adversarial_deck.read_bytes()

    def test_integrity_survives_the_whole_loop(self, adversarial_deck, tmp_path):
        session = Session.open(adversarial_deck, workspace=tmp_path / "ws")
        table = next(s for s in session.deck().all_shapes() if s.kind == "table")

        changeset = session.propose("revise")
        from slide_wright.changeset import Change, Op

        changeset.add(Change(id="c1", op=Op.SET_TABLE_CELL, slide=3,
                             target=f"{table.id}/r1/c1", before="9.4x", after="11.8x"))
        changeset.approve_all()
        session.apply()
        out = session.export(tmp_path / "out.pptx")

        before, after = Package.open(adversarial_deck), Package.open(out)
        assert before.slide_count == after.slide_count
        assert before.part_count == after.part_count


@pytest.mark.skipif(not REAL_DECK.is_file(), reason="external corpus deck not present")
class TestRealDeckEndToEnd:
    """The same loop on a 19-slide deck authored in a different tool."""

    def test_one_title_edit_touches_one_part(self, tmp_path):
        session = Session.open(REAL_DECK, workspace=tmp_path / "ws")
        deck = session.deck()

        target = next(
            (s for sl in deck.slides for s in sl.shapes
             if s.has_text and len(s.text) > 10),
            None,
        )
        assert target is not None
        slide_no = next(sl.number for sl in deck.slides if target in sl.shapes)

        from slide_wright.changeset import Change, Op

        changeset = session.propose("retitle one slide")
        changeset.add(Change(id="c1", op=Op.SET_TEXT, slide=slide_no,
                             target=target.id, before=target.text,
                             after="Slide-Wright end-to-end marker"))
        changeset.approve_all()
        report = session.apply()

        assert report.deliverable
        assert len(report.fidelity.changed) == 1, "exactly one part should change"
        assert report.fidelity.fidelity_score > 98.0
        assert not report.fidelity.native_losses
        assert report.fidelity.output_census.tables == report.fidelity.source_census.tables

    def test_the_source_deck_is_never_modified(self, tmp_path):
        original = REAL_DECK.read_bytes()
        session = Session.open(REAL_DECK, workspace=tmp_path / "ws")
        session.audit()
        session.deck()
        assert REAL_DECK.read_bytes() == original
