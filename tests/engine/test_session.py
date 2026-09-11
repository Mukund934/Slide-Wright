"""The transactional session — the product loop end to end.

The behaviour that matters most: a deck that fails verification must not reach
the user by any route, including export.
"""

from __future__ import annotations

import time

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


class TestExportSaysWhereItActuallyWrote:
    """`shutil.copy` writes *into* a folder rather than refusing it.

    Passing a directory produced a file called `v000-original.pptx` inside it —
    the workspace's own internal name, which the user never chose and would not
    recognise — while the caller was handed back the folder as if that were the
    file. Told one path, given another, under a third name.
    """

    def test_a_folder_is_refused_rather_than_guessed_at(self, session, tmp_path):
        folder = tmp_path / "out"
        folder.mkdir()
        with pytest.raises(SessionError, match="is a folder"):
            session.export(folder)

    def test_the_refusal_says_what_to_do_instead(self, session, tmp_path):
        folder = tmp_path / "out"
        folder.mkdir()
        with pytest.raises(SessionError, match="name to write"):
            session.export(folder)

    def test_an_ordinary_export_still_returns_the_file_it_wrote(self, session, tmp_path):
        written = session.export(tmp_path / "deck.pptx")
        assert written.is_file()
        assert written == tmp_path / "deck.pptx"


class TestOneApplyAtATime:
    """`apply` reads `next_number`, writes that file, verifies it, and only then
    advances the counter — so two calls overlapping both aim at the same version.

    Measured through the API with three requests at once, before the lock: one
    committed, and two died on the half-written file with
    `PermissionError: the process cannot access the file because it is being
    used by another process`, which reached the client as a raw 500. The work in
    those two was simply lost.

    The client guards its own button. Two browser tabs on one document do not,
    and neither does a script.
    """

    def _tidy(self, session):
        from slide_wright.brand import plan_conformance, read_profile
        from slide_wright.layout import plan_alignment

        info = session.deck()
        changeset = session.propose("tidy")
        try:
            for change in plan_conformance(info, read_profile(session.current.path)).changes:
                changeset.add(change)
        except Exception:
            pass
        for change in plan_alignment(info).changes:
            changeset.add(change)
        if not changeset.changes:
            changeset.add(cell_change(session))
        changeset.approve_all()
        return changeset

    def test_a_second_apply_is_refused_while_one_is_running(self, session):
        import threading

        self._tidy(session)
        outcomes: list[str] = []
        started = threading.Event()

        real = session.verify

        def slow_verify(*args, **kwargs):
            started.set()
            time.sleep(0.4)          # hold the lock long enough to collide
            return real(*args, **kwargs)

        session.verify = slow_verify  # type: ignore[method-assign]

        def first():
            try:
                session.apply("first")
                outcomes.append("first committed")
            except SessionError as exc:
                outcomes.append(f"first refused: {exc}")

        worker = threading.Thread(target=first)
        worker.start()
        assert started.wait(5), "the first apply never began"
        with pytest.raises(SessionError, match="already running"):
            session.apply("second")
        worker.join(10)

        assert outcomes == ["first committed"]

    def test_the_refusal_says_where_the_result_will_appear(self, session):
        session._applying.acquire()
        try:
            with pytest.raises(SessionError, match="appear in the history"):
                session.apply("blocked")
        finally:
            session._applying.release()

    def test_the_lock_is_released_when_an_apply_fails(self, session):
        """A refusal that leaks the lock turns one bad apply into a dead
        document."""
        with pytest.raises(SessionError):
            session.apply("nothing proposed")   # no change set at all
        assert session._applying.acquire(blocking=False)
        session._applying.release()

    def test_an_ordinary_apply_still_works(self, session):
        changeset = self._tidy(session)
        assert changeset.approved
        report = session.apply("fine")
        assert report.deliverable
        assert [v.number for v in session.versions] == [0, 1]


class TestLocksAreEnforcedAtTheVerifier:
    """The README has claimed this since it was written. Nothing was doing it.

    A lock was consulted in exactly one place -- `ChangeSet.add`, where changes
    are admitted -- so the guarantee was about the plan and not about the file.
    The two came apart this morning: `formatting` permits text edits *by design*,
    an applier stripped run styling while making one, and the lock was declared,
    honoured, reported honoured, and worthless.

    So the deck is asked as well as the plan. These tests construct outputs that
    break a lock without going through the gate that would have refused them,
    which is the only way to test a second line of defence -- if the first line
    is working, the second never fires on anything it produces.
    """

    def _edited(self, deck, tmp_path, change, name):
        """An output produced with no lock held, so the gate admits the change."""
        from slide_wright.apply import apply_changes
        from slide_wright.changeset import ChangeSet

        cs = ChangeSet(deck=str(deck))
        cs.add(change)
        cs.approve_all()
        out = tmp_path / name
        assert not apply_changes(deck, cs, out).failed
        return out

    def _locked(self, deck, scope, target=""):
        from slide_wright.changeset import ChangeSet

        cs = ChangeSet(deck=str(deck))
        cs.lock(scope, target)
        return cs

    def _styled_shape(self, deck):
        return next(
            (sl.number, s)
            for sl in inspect(deck).slides
            for s in sl.shapes
            if s.kind not in {"table", "group", "chart", "picture"} and len(s.runs) > 1
        )

    def test_a_restyled_run_blocks_delivery_under_a_formatting_lock(
        self, session, adversarial_deck, tmp_path
    ):
        number, shape = self._styled_shape(adversarial_deck)
        # Size rather than typeface: the corpus run carries weight and size in
        # its `rPr` and no `a:latin`, and the applier deliberately declines to
        # invent an override on a run that inherits its typeface.
        out = self._edited(
            adversarial_deck, tmp_path,
            Change(id="c1", op=Op.SET_FONT_SIZE, slide=number,
                   target=f"{shape.id}/run/1", before=shape.runs[1].size_pt,
                   after=9),
            "restyled.pptx",
        )
        report = session.verify(
            adversarial_deck, out, self._locked(adversarial_deck, "formatting")
        )
        assert not report.deliverable, "a broken guarantee has to stop the deck"
        assert any("formatting lock" in reason for reason in report.blocking_reasons)

    def test_a_deck_whose_bullets_went_blocks_under_a_formatting_lock(
        self, session, adversarial_deck, tmp_path
    ):
        """*Leave my styling exactly as it is* has to cover the bullet.

        Nothing in the engine takes a bullet off, which is why this output is
        built by hand: a second line of defence never fires on anything the
        first line admits. What it proves is that the sentence the lock prints
        is now a property of the file, and the diff that answers it can see the
        paragraph a run sits in -- which until today it could not.
        """
        import re
        import zipfile

        from lxml import etree

        A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
        part = next(
            name for name in sorted(zipfile.ZipFile(adversarial_deck).namelist())
            if re.match(r"^ppt/slides/slide\d+\.xml$", name)
            and b"Margin held" in zipfile.ZipFile(adversarial_deck).read(name)
        )

        out = tmp_path / "unbulleted.pptx"
        with zipfile.ZipFile(adversarial_deck) as src, \
                zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename == part:
                    root = etree.fromstring(data)
                    for para in root.iter(f"{A}p"):
                        if not para.findall(f".//{A}t"):
                            continue
                        pPr = para.find(f"{A}pPr")
                        if pPr is None:
                            pPr = etree.Element(f"{A}pPr")
                            para.insert(0, pPr)
                        etree.SubElement(pPr, f"{A}buNone")
                    data = etree.tostring(root, xml_declaration=True,
                                          encoding="UTF-8", standalone=True)
                dst.writestr(item, data)

        report = session.verify(
            adversarial_deck, out, self._locked(adversarial_deck, "formatting")
        )
        assert not report.deliverable, "a deck that lost its bullets is not deliverable"
        assert any("formatting lock" in reason for reason in report.blocking_reasons)

    def test_a_rewritten_script_blocks_delivery_under_a_wording_lock(
        self, session, adversarial_deck, tmp_path
    ):
        """The lock's own sentence is "leave my words exactly as written".

        Speaker notes are words somebody wrote -- 73% of them, on one real deck
        -- and until the diff could see them this lock was silent about every
        one. Constructed by hand because nothing in this engine writes a notes
        part, which is the same reason the gap survived: there was no operation
        whose output would have exposed it.
        """
        from test_diff import rewrite_notes

        out = rewrite_notes(
            adversarial_deck, tmp_path / "rescripted.pptx", b"presenter", b"narrator"
        )
        report = session.verify(
            adversarial_deck, out, self._locked(adversarial_deck, "wording")
        )
        assert not report.deliverable, "a deck whose script was rewritten is not deliverable"
        assert any("wording lock" in reason for reason in report.blocking_reasons)

    def test_a_moved_shape_blocks_delivery_under_a_layout_lock(
        self, session, adversarial_deck, tmp_path
    ):
        number, table = next(
            (sl.number, s)
            for sl in inspect(adversarial_deck).slides
            for s in sl.shapes
            if s.kind == "table"
        )
        out = self._edited(
            adversarial_deck, tmp_path,
            Change(id="c1", op=Op.MOVE, slide=number, target=table.id,
                   before=[table.x, table.y], after=[table.x + 400000, table.y]),
            "moved.pptx",
        )
        report = session.verify(
            adversarial_deck, out, self._locked(adversarial_deck, "layout")
        )
        assert not report.deliverable
        assert any("layout lock" in reason for reason in report.blocking_reasons)

    def test_a_changed_figure_blocks_delivery_under_a_numbers_lock(
        self, session, adversarial_deck, tmp_path
    ):
        number, table = next(
            (sl.number, s)
            for sl in inspect(adversarial_deck).slides
            for s in sl.shapes
            if s.kind == "table" and s.cell(1, 1)
        )
        out = self._edited(
            adversarial_deck, tmp_path,
            Change(id="c1", op=Op.SET_TABLE_CELL, slide=number,
                   target=f"{table.id}/r1/c1", before=table.cell(1, 1), after="99.9x"),
            "refigured.pptx",
        )
        report = session.verify(
            adversarial_deck, out, self._locked(adversarial_deck, "numbers")
        )
        assert not report.deliverable
        assert any("numbers lock" in reason for reason in report.blocking_reasons)

    def test_a_locked_slide_that_changed_blocks_delivery(
        self, session, adversarial_deck, tmp_path
    ):
        number, shape = self._styled_shape(adversarial_deck)
        out = self._edited(
            adversarial_deck, tmp_path,
            Change(id="c1", op=Op.SET_TEXT, slide=number, target=shape.id,
                   before=shape.runs[0].text, after="CHANGED"),
            "slide-changed.pptx",
        )
        report = session.verify(
            adversarial_deck, out, self._locked(adversarial_deck, "slide", str(number))
        )
        assert not report.deliverable
        assert any(f"slide:{number} lock" in r for r in report.blocking_reasons)

    def test_a_lock_over_something_that_did_not_change_does_not_block(
        self, session, adversarial_deck, tmp_path
    ):
        """The other half. A second line of defence that fires on clean output
        is not a guarantee, it is a broken product."""
        number, shape = self._styled_shape(adversarial_deck)
        out = self._edited(
            adversarial_deck, tmp_path,
            Change(id="c1", op=Op.SET_TEXT, slide=number, target=shape.id,
                   before=shape.runs[0].text, after="Reworded"),
            "worded.pptx",
        )
        # Text moved; layout and figures did not.
        for scope in ("layout", "numbers", "charts", "media"):
            report = session.verify(
                adversarial_deck, out, self._locked(adversarial_deck, scope)
            )
            assert not report.lock_breaks, f"{scope} fired on an output it does not cover"

    def test_no_locks_means_no_extra_work_and_no_extra_reasons(
        self, session, adversarial_deck, tmp_path
    ):
        number, shape = self._styled_shape(adversarial_deck)
        out = self._edited(
            adversarial_deck, tmp_path,
            Change(id="c1", op=Op.SET_TEXT, slide=number, target=shape.id,
                   before=shape.runs[0].text, after="Reworded"),
            "plain.pptx",
        )
        assert session.verify(adversarial_deck, out).lock_breaks == []
