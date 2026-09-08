"""The change set: the contract that makes the promise checkable.

The most important behaviour here is that locks are honoured at construction
*and* survive round-tripping. A guarantee the user set that quietly disappears
when the change set is saved and reloaded is worse than no guarantee.
"""

from __future__ import annotations

import pytest

from slide_wright.changeset import Change, ChangeSet, Lock, Op, Origin, Status


def cs(**kw) -> ChangeSet:
    return ChangeSet(deck="deck.pptx", **kw)


def change(cid="c1", op=Op.SET_TEXT, slide=1, target="7", before="old", after="new",
           **kw) -> Change:
    return Change(id=cid, op=op, slide=slide, target=target, before=before, after=after, **kw)


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


class TestProvenance:
    """A reviewer must be able to tell where a change came from."""

    def test_a_user_change_is_grounded(self):
        assert change().is_grounded
        assert not change().needs_review

    def test_a_model_change_without_a_citation_needs_review(self):
        c = Change(id="c1", op=Op.SET_TEXT, slide=1, target="7",
                   before="a", after="b", origin=Origin.MODEL)
        assert not c.is_grounded
        assert c.needs_review

    def test_a_model_change_with_a_citation_is_grounded(self):
        c = Change(id="c1", op=Op.SET_TABLE_CELL, slide=1, target="7/r1/c1",
                   before="9.4x", after="11.8x", origin=Origin.MODEL,
                   citation="comps.csv!B2")
        assert c.is_grounded
        assert not c.needs_review

    def test_source_changes_are_grounded_without_review(self):
        c = Change(id="r1", op=Op.SET_TABLE_CELL, slide=1, target="7/r1/c1",
                   before="9.4x", after="11.8x", origin=Origin.SOURCE,
                   citation="comps.csv!B2")
        assert c.is_grounded and not c.needs_review

    def test_approve_all_can_hold_back_unreviewed_model_changes(self):
        s = cs()
        s.add(Change(id="grounded", op=Op.SET_TEXT, slide=1, target="7",
                     before="a", after="b", origin=Origin.SOURCE, citation="x!A1"))
        s.add(Change(id="invented", op=Op.SET_TEXT, slide=1, target="7",
                     before="a", after="b", origin=Origin.MODEL))
        s.approve_all(include_unreviewed=False)
        assert [c.id for c in s.approved] == ["grounded"]
        assert [c.id for c in s.needing_review] == ["invented"]

    def test_render_flags_changes_needing_review(self):
        s = cs()
        s.add(Change(id="c1", op=Op.SET_TEXT, slide=1, target="7",
                     before="a", after="b", origin=Origin.MODEL, confidence=0.6))
        out = s.render()
        assert "NEEDS REVIEW" in out
        assert "confidence 60%" in out

    def test_render_shows_a_citation_instead_of_origin(self):
        s = cs()
        s.add(Change(id="r1", op=Op.SET_TABLE_CELL, slide=1, target="7/r1/c1",
                     before="9.4x", after="11.8x", origin=Origin.SOURCE,
                     citation="comps.csv!B2"))
        assert "source comps.csv!B2" in s.render()

    def test_impact_is_surfaced(self):
        s = cs()
        s.add(Change(id="c1", op=Op.RESIZE, slide=1, target="7",
                     before=(100, 100), after=(50, 100),
                     impact="text in this box may reflow"))
        assert "may also affect: text in this box may reflow" in s.render()

    def test_provenance_survives_a_round_trip(self):
        s = cs()
        s.add(Change(id="r1", op=Op.SET_TABLE_CELL, slide=1, target="7/r1/c1",
                     before="9.4x", after="11.8x", origin=Origin.SOURCE,
                     citation="comps.csv!B2", confidence=0.9,
                     impact="totals row", object_kind="table"))
        back = ChangeSet.from_json(s.to_json()).changes[0]
        assert back.origin is Origin.SOURCE
        assert back.citation == "comps.csv!B2"
        assert back.confidence == 0.9
        assert back.impact == "totals row"
        assert back.object_kind == "table"


class TestProtectionScopes:
    """The conditional requests people actually make, as engine constraints."""

    def test_wording_lock_protects_text_but_permits_layout(self):
        s = cs()
        s.lock("wording", reason="legal signed off on this copy")
        assert s.add(change(cid="text", op=Op.SET_TEXT)).status is Status.REJECTED
        assert s.add(Change(id="move", op=Op.MOVE, slide=1, target="7",
                            before=(0, 0), after=(10, 0))).status is Status.PROPOSED

    def test_layout_lock_protects_geometry_but_permits_text(self):
        s = cs()
        s.lock("layout", reason="template-controlled positions")
        assert s.add(Change(id="move", op=Op.MOVE, slide=1, target="7",
                            before=(0, 0), after=(10, 0))).status is Status.REJECTED
        assert s.add(change(cid="text")).status is Status.PROPOSED

    def test_tables_lock_protects_table_objects(self):
        s = cs()
        s.lock("tables", reason="exhibits are final")
        blocked = s.add(Change(id="t", op=Op.SET_TABLE_CELL, slide=1,
                               target="7/r1/c1", before="1", after="2",
                               object_kind="table"))
        assert blocked.status is Status.REJECTED

    def test_tables_lock_leaves_ordinary_text_alone(self):
        s = cs()
        s.lock("tables")
        assert s.add(change(cid="c1")).status is Status.PROPOSED

    def test_charts_lock_protects_chart_objects(self):
        s = cs()
        s.lock("charts")
        blocked = s.add(Change(id="c", op=Op.SET_TEXT, slide=1, target="9",
                               before="a", after="b", object_kind="chart"))
        assert blocked.status is Status.REJECTED

    def test_media_lock_protects_pictures(self):
        s = cs()
        s.lock("media")
        blocked = s.add(Change(id="p", op=Op.MOVE, slide=1, target="9",
                               before=(0, 0), after=(1, 1), object_kind="picture"))
        assert blocked.status is Status.REJECTED

    def test_an_unknown_scope_is_refused_at_construction(self):
        """A silently-ignored lock is worse than no lock."""
        with pytest.raises(ValueError, match="unknown protection scope"):
            Lock(scope="vibes")

    def test_combining_locks_narrows_further(self):
        s = cs()
        s.lock("numbers")
        s.lock("layout")
        assert s.add(change(before="Revenue 4.2M")).status is Status.REJECTED
        assert s.add(Change(id="m", op=Op.MOVE, slide=1, target="7",
                            before=(0, 0), after=(9, 9))).status is Status.REJECTED
        assert s.add(change(cid="ok", before="Strategy", after="Our strategy")).status is Status.PROPOSED


class TestDescribeCoversEveryOp:
    """A missing entry raises KeyError at report time — after the edit is written.

    That is exactly what happened when `set_font` was added: the change applied
    cleanly, then the run crashed while describing what it had just done.
    """

    def test_every_op_can_be_described(self):
        for op in Op:
            c = Change(id="c1", op=op, slide=1, target="7", before="a", after="b")
            assert c.describe(), f"{op.value} has no description"


class TestALockIsNotAMatterOfOrder:
    """`add` consulted the locks that existed when a change arrived.

    A lock declared afterwards protected nothing at all — silently, while the
    interface showed it held. Every production path happened to declare locks
    first, so the trap was live and unhit: the guarantee depended on the order
    two public methods were called in, and nothing said so except a comment.
    """

    def _change(self, **over):
        base = dict(id="c1", op=Op.SET_TEXT, slide=1, target="5",
                    before="Revenue grew", after="Revenue fell")
        base.update(over)
        return Change(**base)

    def test_a_lock_added_after_the_change_still_blocks_it(self):
        changeset = ChangeSet(deck="d.pptx")
        change = changeset.add(self._change())
        assert change.status is Status.PROPOSED
        changeset.lock("wording")
        assert change.status is Status.REJECTED

    def test_it_says_which_lock_stopped_it(self):
        changeset = ChangeSet(deck="d.pptx")
        change = changeset.add(self._change())
        changeset.lock("wording", reason="the partner signed this off")
        assert "wording lock" in change.rationale
        assert "the partner signed this off" in change.rationale

    def test_a_lock_added_first_still_blocks(self):
        changeset = ChangeSet(deck="d.pptx")
        changeset.lock("wording")
        assert changeset.add(self._change()).status is Status.REJECTED

    def test_an_applied_change_is_not_retroactively_rejected(self):
        """It is already in the file. Marking it rejected would make the record
        of what happened disagree with what happened."""
        changeset = ChangeSet(deck="d.pptx")
        change = changeset.add(self._change())
        change.status = Status.APPLIED
        changeset.lock("wording")
        assert change.status is Status.APPLIED

    def test_a_lock_that_matches_nothing_leaves_the_set_alone(self):
        changeset = ChangeSet(deck="d.pptx")
        change = changeset.add(self._change(op=Op.MOVE, before=(0, 0), after=(1, 1)))
        changeset.lock("wording")
        assert change.status is Status.PROPOSED


class TestNotKnowingIsNotTheSameAsKnowingThereIsNoNumber:
    """`numbers` means "do not touch a single figure".

    It asked `_contains_number` about the text being replaced, and given `None`
    that answered no — so a change that could not say what it was replacing
    walked through a lock whose whole purpose is that no figure moves. A
    guarantee has to fail in the direction of refusing.
    """

    def _with_numbers_lock(self, **over):
        changeset = ChangeSet(deck="d.pptx")
        changeset.lock("numbers")
        base = dict(id="c1", op=Op.SET_TEXT, slide=1, target="5",
                    before="9.4x", after="11.8x")
        base.update(over)
        return changeset.add(Change(**base))

    def test_a_figure_is_blocked(self):
        assert self._with_numbers_lock().status is Status.REJECTED

    def test_prose_is_not(self):
        assert self._with_numbers_lock(
            before="Revenue grew", after="Revenue rose"
        ).status is Status.PROPOSED

    def test_an_unknown_before_is_blocked(self):
        assert self._with_numbers_lock(before=None).status is Status.REJECTED

    def test_an_empty_before_is_blocked(self):
        """An empty cell being filled in could be filled with a figure."""
        assert self._with_numbers_lock(before="").status is Status.REJECTED

    def test_it_still_only_applies_to_content_operations(self):
        """A move is not a figure changing, whatever its `before` says."""
        assert self._with_numbers_lock(
            op=Op.MOVE, before=None, after=(1, 1)
        ).status is Status.PROPOSED


class TestAnExhibitLockDoesNotDependOnBeingToldTheKind:
    """A cell edit is a table edit however the change describes itself.

    `tables`, `charts` and `media` match on `Change.object_kind`. That is a
    description the builder supplies, so a builder that omits it produces a
    change the lock cannot see — which is a guarantee resting on bookkeeping.
    For a cell edit the op alone settles it, so that case no longer asks.
    """

    def test_a_cell_edit_is_blocked_with_no_object_kind_declared(self):
        cs = ChangeSet(deck="d.pptx")
        cs.lock("tables", reason="leave the exhibits alone")
        cs.add(Change(id="c1", op=Op.SET_TABLE_CELL, slide=1, target="5/r1/c1",
                      before="9.4x", after="11.8x"))
        assert cs.changes[0].status is Status.REJECTED
        assert "tables lock" in cs.changes[0].rationale

    def test_an_ordinary_text_edit_is_not_caught_by_it(self):
        cs = ChangeSet(deck="d.pptx")
        cs.lock("tables")
        cs.add(Change(id="c1", op=Op.SET_TEXT, slide=1, target="5",
                      before="Draft", after="Final"))
        assert cs.changes[0].status is Status.PROPOSED
