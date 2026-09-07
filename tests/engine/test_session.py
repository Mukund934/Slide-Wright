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


class TestHistorySurvivesTheProcess:
    """A session that forgets its versions cannot honour "revert".

    Session.open used to rebuild the version list from scratch every time, so
    reopening the same workspace saw only version 0. Two consequences, both
    silent: the second edit wrote over the first edit's file, and it was
    applied to the original rather than to the first edit's result.
    """

    def _edit(self, deck, workspace, after, note):
        session = Session.open(deck, workspace=workspace)
        table = next(s for s in session.deck().all_shapes() if s.kind == "table")
        current = next(r.text for r in table.runs if "x" in r.text)
        cs = session.propose(note)
        cs.add(Change(id="c1", op=Op.SET_TABLE_CELL, slide=3,
                      target=f"{table.id}/r1/c1", before=current, after=after))
        cs.approve_all()
        session.apply(note)
        return session

    def test_a_reopened_session_sees_earlier_versions(self, adversarial_deck, tmp_path):
        ws = tmp_path / "ws"
        self._edit(adversarial_deck, ws, "11.8x", "first edit")
        second = self._edit(adversarial_deck, ws, "12.5x", "second edit")
        assert [v.number for v in second.versions] == [0, 1, 2]
        assert "first edit" in second.history()

    def test_the_earlier_artifact_is_not_overwritten(self, adversarial_deck, tmp_path):
        ws = tmp_path / "ws"
        self._edit(adversarial_deck, ws, "11.8x", "first edit")
        self._edit(adversarial_deck, ws, "12.5x", "second edit")
        files = sorted(p.name for p in ws.glob("*.pptx"))
        assert files == ["v000-original.pptx", "v001-edited.pptx", "v002-edited.pptx"]

    def test_the_second_edit_builds_on_the_first(self, adversarial_deck, tmp_path):
        """Not on the original. Otherwise edits silently do not accumulate."""
        ws = tmp_path / "ws"
        self._edit(adversarial_deck, ws, "11.8x", "first edit")
        second = self._edit(adversarial_deck, ws, "12.5x", "second edit")
        # The second change named 11.8x as its `before` and applied cleanly,
        # which is only possible if it ran against the first edit's output.
        assert second.current.number == 2

    def test_rollback_reaches_a_version_from_an_earlier_process(
        self, adversarial_deck, tmp_path
    ):
        ws = tmp_path / "ws"
        self._edit(adversarial_deck, ws, "11.8x", "first edit")
        second = self._edit(adversarial_deck, ws, "12.5x", "second edit")
        second.rollback(1)
        assert second.current.number == 1
        table = next(s for s in second.deck().all_shapes() if s.kind == "table")
        assert any("11.8x" in r.text for r in table.runs)

    def test_version_numbers_are_never_reused_after_a_rollback(
        self, adversarial_deck, tmp_path
    ):
        ws = tmp_path / "ws"
        session = self._edit(adversarial_deck, ws, "11.8x", "first edit")
        session.rollback(0)
        table = next(s for s in session.deck().all_shapes() if s.kind == "table")
        cs = session.propose("after rollback")
        cs.add(Change(id="c1", op=Op.SET_TABLE_CELL, slide=3,
                      target=f"{table.id}/r1/c1", before="9.4x", after="13.1x"))
        cs.approve_all()
        session.apply("after rollback")
        assert session.current.number == 2, "reusing v001 would overwrite it"
        assert (ws / "v001-edited.pptx").is_file(), "the discarded version is evidence"

    def test_a_missing_version_file_is_refused_not_ignored(
        self, adversarial_deck, tmp_path
    ):
        """A history that cannot be rolled back to must not be presented."""
        ws = tmp_path / "ws"
        self._edit(adversarial_deck, ws, "11.8x", "first edit")
        (ws / "v001-edited.pptx").unlink()
        with pytest.raises(SessionError, match="missing from the workspace"):
            Session.open(adversarial_deck, workspace=ws)

    def test_unreadable_history_is_refused(self, adversarial_deck, tmp_path):
        ws = tmp_path / "ws"
        self._edit(adversarial_deck, ws, "11.8x", "first edit")
        (ws / "history.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(SessionError, match="unreadable"):
            Session.open(adversarial_deck, workspace=ws)


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
        with pytest.raises(SessionError, match="was blocked"):
            session.export(tmp_path / "leak.pptx")

    def test_original_can_always_be_exported(self, session, tmp_path):
        out = session.export(tmp_path / "orig.pptx")
        assert out.is_file()


class TestAudit:
    def test_audit_runs_the_gate_on_the_current_version(self, session):
        assert session.audit().passed, "the corpus deck should pass cleanly"


class TestProgress:
    """Progress narration exists so a UI can say what is happening honestly.

    A ten-minute apply behind a spinner is the shape of every AI tool that
    asks to be trusted and gives no reason. These stages are the reason.
    """

    def test_reports_every_stage_of_a_successful_apply(self, session):
        seen = []
        cs = session.propose()
        cs.add(cell_change(session))
        cs.approve_all()
        session.apply(on_progress=lambda stage, detail, **facts: seen.append(stage))

        assert seen == ["applying", "applied", "verifying", "verified"]

    def test_stages_carry_the_slides_they_touch(self, session):
        facts = {}
        cs = session.propose()
        cs.add(cell_change(session))
        cs.approve_all()
        session.apply(
            on_progress=lambda stage, detail, **kw: facts.setdefault(stage, kw)
        )

        assert facts["applying"]["slides"] == [3]
        assert facts["applied"]["slides"] == [3]
        assert facts["verified"]["version"] == 1

    def test_a_refusal_is_announced_before_it_is_raised(self, session):
        """The UI must be able to explain a refusal, not just show an error."""
        seen = []
        cs = session.propose()
        change = cell_change(session)
        change.target = "9999"  # no such shape
        cs.add(change)
        cs.approve_all()
        with pytest.raises(SessionError):
            session.apply(on_progress=lambda stage, detail, **kw: seen.append(stage))

        assert seen[-1] == "refused"

    def test_a_broken_listener_does_not_break_the_edit(self, session):
        """The version is already on disk. Nobody watching is not a failure."""
        def explode(*args, **kwargs):
            raise RuntimeError("the client closed the tab")

        cs = session.propose()
        cs.add(cell_change(session))
        cs.approve_all()
        report = session.apply(on_progress=explode)

        assert report.deliverable
        assert session.current.number == 1

    def test_no_listener_is_the_default_and_costs_nothing(self, session):
        cs = session.propose()
        cs.add(cell_change(session))
        cs.approve_all()
        assert session.apply().deliverable


def a_moveable_shape(session: Session):
    """A shape with a box of its own, so a MOVE has somewhere to land."""
    for slide in session.deck().slides:
        for shape in slide.shapes:
            if (shape.x is not None and not shape.geometry_inherited
                    and shape.kind not in ("chart", "table")):
                return slide.number, shape
    pytest.skip("fixture has no independently positioned shape")


def nudge(session: Session, number: int, shape, dx: int, cid="m1") -> Change:
    return Change(id=cid, op=Op.MOVE, slide=number, target=shape.id,
                  before=(shape.x, shape.y), after=(shape.x + dx, shape.y))


class TestAChangeSetDescribesOneVersion:
    """Every `before` in a change set was read from a particular file.

    The applier writes against whatever is current, so a set that outlives its
    parent writes coordinates and replacements computed from a version nobody
    is looking at any more — and the report calls it verified, because the
    slides it touched are exactly the slides it said it would touch.

    Measured before the check: propose against v000, apply (v001 committed, set
    still live), add one more move to the same set and apply again. The shape
    landed on `original + 200000` rather than `current + 200000`, discarding
    v001's own edit in silence.
    """

    def test_a_committed_set_is_closed(self, session):
        number, shape = a_moveable_shape(session)
        cs = session.propose("nudge")
        cs.add(nudge(session, number, shape, 100_000))
        cs.approve_all()
        session.apply("v1")
        assert session.changeset is None

    def test_reusing_one_is_refused_and_says_which_version(self, session):
        number, shape = a_moveable_shape(session)
        cs = session.propose("nudge")
        cs.add(nudge(session, number, shape, 100_000))
        cs.approve_all()
        session.apply("v1")

        session.changeset = cs            # as a caller holding a reference would
        cs.add(nudge(session, number, shape, 200_000, cid="m2"))
        cs.approve_all()
        with pytest.raises(SessionError, match="v000-original"):
            session.apply("again")

    def test_a_blocked_set_is_kept(self, session, monkeypatch):
        """The user's work, and they may want to adjust it rather than redo it."""
        cs = session.propose("edit")
        cs.add(cell_change(session))
        cs.approve_all()
        monkeypatch.setattr(
            "slide_wright.report.ChangeReport.deliverable", property(lambda self: False)
        )
        session.apply("blocked")
        assert session.changeset is cs

    def test_proposing_again_clears_a_blocked_verdict(self, session, monkeypatch):
        """A blocked apply never advances the session, so the current version
        passed verification — but the standing verdict went on refusing to let
        it out, with no way forward but a rollback nobody would think of."""
        cs = session.propose("edit")
        cs.add(cell_change(session))
        cs.approve_all()
        monkeypatch.setattr(
            "slide_wright.report.ChangeReport.deliverable", property(lambda self: False)
        )
        session.apply("blocked")
        monkeypatch.undo()

        session.propose("start over")
        assert session.last_report is None


class TestTwoSessionsOverOneWorkspace:
    """Version numbers are handed out from a session's own view of history.

    Two sessions on one workspace both hand out the same number, write the same
    filename, and the later write wins. Measured: each committed "version 1",
    each believed it held its own edit, and the file on disk was one of them.
    Neither was told, and a third session reading the workspace saw a history
    that described the loser's work and a file containing the winner's.

    Loopback and local is not the same as single-user: a CLI run beside the app,
    or two browser tabs, is the ordinary case.
    """

    def _second(self, session, adversarial_deck):
        return Session.open(adversarial_deck, workspace=session.workspace)

    def test_the_second_session_is_refused_not_silently_merged(
        self, session, adversarial_deck
    ):
        other = self._second(session, adversarial_deck)
        number, shape = a_moveable_shape(session)

        cs = session.propose("first")
        cs.add(nudge(session, number, shape, 100_000))
        cs.approve_all()
        session.apply("first")

        cs2 = other.propose("second")
        cs2.add(nudge(other, number, shape, 500_000))
        cs2.approve_all()
        with pytest.raises(SessionError, match="changed since this session opened it"):
            other.apply("second")

    def test_the_first_session_s_work_survives(self, session, adversarial_deck):
        other = self._second(session, adversarial_deck)
        number, shape = a_moveable_shape(session)

        cs = session.propose("first")
        cs.add(nudge(session, number, shape, 100_000))
        cs.approve_all()
        session.apply("first")

        cs2 = other.propose("second")
        cs2.add(nudge(other, number, shape, 500_000))
        cs2.approve_all()
        with pytest.raises(SessionError):
            other.apply("second")

        reopened = Session.open(adversarial_deck, workspace=session.workspace)
        landed = next(s for s in inspect(reopened.current.path).slides[number - 1].shapes
                      if s.id == shape.id)
        assert landed.x == shape.x + 100_000

    def test_rollback_is_guarded_too(self, session, adversarial_deck):
        """It rewrites history, so it can discard another session's versions
        just as an apply can overwrite them."""
        other = self._second(session, adversarial_deck)
        number, shape = a_moveable_shape(session)

        cs = session.propose("first")
        cs.add(nudge(session, number, shape, 100_000))
        cs.approve_all()
        session.apply("first")

        with pytest.raises(SessionError, match="changed since this session opened it"):
            other.rollback(0)

    def test_two_sessions_that_agree_are_left_alone(self, adversarial_deck, tmp_path):
        """The check must not fire on a state both sides actually share.

        Two sessions opening the same fresh workspace both write version 0.
        Fingerprinting the file's bytes would have them differ by a second on
        `created_at` and refuse each other over nothing.
        """
        ws = tmp_path / "shared"
        first = Session.open(adversarial_deck, workspace=ws)
        second = Session.open(adversarial_deck, workspace=ws)
        number, shape = a_moveable_shape(second)

        cs = second.propose("only edit")
        cs.add(nudge(second, number, shape, 100_000))
        cs.approve_all()
        second.apply("fine")
        assert [v.number for v in second.versions] == [0, 1]
        assert first.current.number == 0
