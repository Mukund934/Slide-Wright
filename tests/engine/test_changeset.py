"""The change set: the contract that makes the promise checkable.

The most important behaviour here is that locks are honoured at construction
*and* survive round-tripping. A guarantee the user set that quietly disappears
when the change set is saved and reloaded is worse than no guarantee.
"""

from __future__ import annotations

from slide_wright.changeset import Change, ChangeSet, Lock, Op, Status


def cs(**kw) -> ChangeSet:
    return ChangeSet(deck="deck.pptx", **kw)


def change(cid="c1", op=Op.SET_TEXT, slide=1, target="7", before="old", after="new") -> Change:
    return Change(id=cid, op=op, slide=slide, target=target, before=before, after=after)


class TestReviewLifecycle:
    def test_changes_start_proposed_and_are_not_actionable(self):
        c = cs().add(change())
        assert c.status is Status.PROPOSED
        assert not c.is_actionable

    def test_approve_all_promotes_only_proposed(self):
        s = cs()
        s.add(change("c1"))
        rejected = s.add(change("c2"))
        s.reject("c2")
        s.approve_all()
        assert s.changes[0].status is Status.APPROVED
        assert rejected.status is Status.REJECTED

    def test_selective_approval(self):
        s = cs()
        s.add(change("c1")); s.add(change("c2")); s.add(change("c3"))
        s.approve("c1", "c3")
        assert [c.id for c in s.approved] == ["c1", "c3"]
        assert [c.id for c in s.proposed] == ["c2"]

    def test_rejecting_an_applied_change_is_refused(self):
        s = cs()
        c = s.add(change("c1"))
        c.status = Status.APPLIED
        s.reject("c1")
        assert c.status is Status.APPLIED, "an applied change cannot be un-proposed"

    def test_only_approved_slides_are_targets(self):
        s = cs()
        s.add(change("c1", slide=3)); s.add(change("c2", slide=9))
        s.approve("c1")
        assert s.target_slides == {3}


class TestLocks:
    def test_slide_lock_blocks_a_change_on_that_slide(self):
        s = cs()
        s.lock("slide", "4", reason="signed off by the partner")
        blocked = s.add(change(slide=4))
        assert blocked.status is Status.REJECTED
        assert "signed off by the partner" in blocked.rationale

    def test_slide_lock_permits_other_slides(self):
        s = cs()
        s.lock("slide", "4")
        assert s.add(change(slide=5)).status is Status.PROPOSED

    def test_shape_lock_blocks_that_shape_only(self):
        s = cs()
        s.lock("shape", "7")
        assert s.add(change(target="7")).status is Status.REJECTED
        assert s.add(change(cid="c2", target="8")).status is Status.PROPOSED

    def test_shape_lock_also_covers_that_shapes_cells(self):
        s = cs()
        s.lock("shape", "7")
        cell = s.add(change(op=Op.SET_TABLE_CELL, target="7/r2/c3", before="4.2M"))
        assert cell.status is Status.REJECTED

    def test_numbers_lock_blocks_edits_to_figures(self):
        """'Polish the deck but do not touch a single number.'"""
        s = cs()
        s.lock("numbers", reason="figures are audited")
        numeric = s.add(change(before="Revenue 4.2M", after="Revenue 5.8M"))
        assert numeric.status is Status.REJECTED

    def test_numbers_lock_permits_pure_text_edits(self):
        s = cs()
        s.lock("numbers")
        textual = s.add(change(before="Our strategy", after="Our 2026 strategy"))
        # The replacement has a digit but the original does not; the lock
        # protects existing figures from being rewritten.
        assert textual.status is Status.PROPOSED

    def test_deck_wide_slide_lock_blocks_everything(self):
        s = cs()
        s.lock("slide")
        assert s.add(change(slide=1)).status is Status.REJECTED
        assert s.add(change(cid="c2", slide=99)).status is Status.REJECTED


class TestSerialisation:
    def test_round_trip_preserves_changes(self):
        s = cs(instruction="make it board-ready")
        s.add(change("c1", slide=2))
        s.add(change("c2", op=Op.MOVE, slide=3, before=100, after=200))
        s.approve("c1")

        back = ChangeSet.from_json(s.to_json())
        assert back.deck == s.deck
        assert back.instruction == "make it board-ready"
        assert [c.id for c in back.changes] == ["c1", "c2"]
        assert back.changes[0].status is Status.APPROVED
        assert back.changes[1].op is Op.MOVE

    def test_round_trip_preserves_locks(self):
        s = cs()
        s.lock("numbers", reason="audited")
        back = ChangeSet.from_json(s.to_json())
        assert len(back.locks) == 1
        assert back.locks[0].scope == "numbers"
        assert back.locks[0].reason == "audited"

    def test_reloaded_locks_still_block(self):
        """A guarantee must survive persistence, or it was never a guarantee."""
        s = cs()
        s.lock("shape", "7")
        back = ChangeSet.from_json(s.to_json())
        assert back.add(change(target="7")).status is Status.REJECTED

    def test_saves_to_disk(self, tmp_path):
        s = cs()
        s.add(change())
        path = s.save(tmp_path / "nested" / "cs.json")
        assert path.is_file()
        assert ChangeSet.from_json(path.read_text(encoding="utf-8")).changes


class TestRendering:
    def test_render_shows_status_and_counts(self):
        s = cs(instruction="tighten slide 2")
        s.add(change("c1")); s.add(change("c2"))
        s.approve("c1")
        out = s.render()
        assert "tighten slide 2" in out
        assert "1 proposed" in out and "1 approved" in out

    def test_render_explains_a_lock_rejection(self):
        s = cs()
        s.lock("numbers", reason="audited figures")
        s.add(change(before="4.2M", after="5.8M"))
        assert "blocked by numbers lock" in s.render()

    def test_empty_change_set_renders_cleanly(self):
        assert "no changes proposed" in cs().render()

    def test_describe_is_readable_per_op(self):
        assert "set text" in change().describe()
        assert "move" in change(op=Op.MOVE, before=1, after=2).describe()
        assert change(op=Op.DELETE_SHAPE).describe() == "delete"
