"""The transactional session — the product loop end to end.

The behaviour that matters most: a deck that fails verification must not reach
the user by any route, including export.
"""

from __future__ import annotations

import pytest

from slide_wright.changeset import Change, Op
from slide_wright.inspect import inspect
from slide_wright.package import UnsafePackageError
from slide_wright.session import Session, SessionError


@pytest.fixture
def session(adversarial_deck, tmp_path) -> Session:
    return Session.open(adversarial_deck, workspace=tmp_path / "ws")


def cell_change(session: Session, cid="c1", after="11.8x") -> Change:
    table = next(s for s in session.deck().all_shapes() if s.kind == "table")
    return Change(id=cid, op=Op.SET_TABLE_CELL, slide=3,
                  target=f"{table.id}/r1/c1", before="9.4x", after=after)


class TestOpen:
    def test_snapshots_the_original_as_version_zero(self, session, adversarial_deck):
        assert session.current.number == 0
        assert session.current.is_original
        assert session.current.path.read_bytes() == adversarial_deck.read_bytes()

    def test_snapshot_is_a_copy_not_the_source(self, session, adversarial_deck):
        assert session.current.path != adversarial_deck

    def test_hostile_input_is_refused_at_open(self, tmp_path):
        bad = tmp_path / "bad.pptx"
        bad.write_text("not a zip")
        with pytest.raises(UnsafePackageError):
            Session.open(bad, workspace=tmp_path / "ws")


class TestProposeReviewApply:
    def test_full_loop_commits_a_version(self, session):
        cs = session.propose("update the Alpha Corp multiple")
        cs.add(cell_change(session))
        cs.approve_all()
        report = session.apply()

        assert report.deliverable
        assert session.current.number == 1
        assert "11.8x" in inspect(session.current.path).slide(3).text

    def test_apply_without_approval_is_refused(self, session):
        cs = session.propose()
        cs.add(cell_change(session))  # left proposed
        with pytest.raises(SessionError, match="no approved changes"):
            session.apply()

    def test_apply_without_a_changeset_is_refused(self, session):
        with pytest.raises(SessionError, match="no change set"):
            session.apply()

    def test_rejected_change_is_not_written(self, session):
        cs = session.propose()
        cs.add(cell_change(session, cid="c1", after="99.9x"))
        cs.add(cell_change(session, cid="c2", after="11.8x"))
        cs.approve("c2")
        cs.reject("c1")
        session.apply()
        text = inspect(session.current.path).slide(3).text
        assert "11.8x" in text and "99.9x" not in text

    def test_report_attributes_every_change(self, session):
        cs = session.propose("revise comps")
        cs.add(cell_change(session))
        cs.approve_all()
        report = session.apply()
        assert report.unrequested_slide_changes == []
        assert "VERIFIED" in report.render()


class TestLocksAreEnforcedEndToEnd:
    def test_a_locked_slide_cannot_be_edited(self, session):
        cs = session.propose()
        cs.lock("slide", "3", reason="signed off")
        cs.add(cell_change(session))
        with pytest.raises(SessionError, match="no approved changes"):
            cs.approve_all()
            session.apply()

    def test_numbers_lock_protects_figures(self, session):
        cs = session.propose("polish but do not touch the numbers")
        cs.lock("numbers", reason="audited")
        cs.add(cell_change(session))
        cs.approve_all()
        with pytest.raises(SessionError, match="no approved changes"):
            session.apply()


class TestHistoryAndRollback:
    def test_versions_accumulate(self, session):
        for i, value in enumerate(("10.1x", "11.8x"), start=1):
            cs = session.propose()
            cs.add(Change(id=f"c{i}", op=Op.SET_TABLE_CELL, slide=3,
                          target=f"{next(s for s in session.deck().all_shapes() if s.kind=='table').id}/r1/c1",
                          before="9.4x" if i == 1 else "10.1x", after=value))
            cs.approve_all()
            session.apply()
        assert [v.number for v in session.versions] == [0, 1, 2]

    def test_rollback_to_original(self, session, adversarial_deck):
        cs = session.propose()
        cs.add(cell_change(session))
        cs.approve_all()
        session.apply()
        assert session.current.number == 1

        session.rollback(0)
        assert session.current.number == 0
        assert session.current.path.read_bytes() == adversarial_deck.read_bytes()

    def test_rollback_to_unknown_version_is_refused(self, session):
        with pytest.raises(SessionError, match="no version"):
            session.rollback(7)

    def test_history_renders(self, session):
        cs = session.propose("first edit")
        cs.add(cell_change(session))
        cs.approve_all()
        session.apply()
        out = session.history()
        assert "v000" in out and "v001" in out and "first edit" in out


class TestExportGate:
    def test_exports_a_verified_deck(self, session, tmp_path):
        cs = session.propose()
        cs.add(cell_change(session))
        cs.approve_all()
        session.apply()
        out = session.export(tmp_path / "final" / "deck.pptx")
        assert out.is_file()
        assert "11.8x" in inspect(out).slide(3).text

    def test_export_is_refused_after_a_blocked_apply(self, session, tmp_path):
        cs = session.propose()
        cs.add(cell_change(session))
        cs.approve_all()
        session.apply()
        # Simulate verification having failed on the last apply.
        session.last_report.fidelity.output_census.tables = 0
        with pytest.raises(SessionError, match="failed verification"):
            session.export(tmp_path / "leak.pptx")

    def test_original_can_always_be_exported(self, session, tmp_path):
        out = session.export(tmp_path / "orig.pptx")
        assert out.is_file()


class TestAudit:
    def test_audit_runs_the_gate_on_the_current_version(self, session):
        assert session.audit().passed, "the corpus deck should pass cleanly"
