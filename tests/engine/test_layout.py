"""Near-miss alignment.

Three rules carry the whole safety argument, and each was found by measuring
rather than by reasoning:

  1. Nothing moves further than the tolerance.
  2. An alignment that is already exact is never broken to fix a near one.
  3. A stray only snaps onto a line at least two shapes already share.

Rules 2 and 3 were added after the pass was observed cycling forever on real
decks. They are the difference between a tool that converges and one that
churns a file every time it runs.
"""

from __future__ import annotations

import pytest

from slide_wright.changeset import Op, Origin
from slide_wright.inspect import EMU_PER_INCH, DeckInfo, ShapeInfo, SlideInfo
from slide_wright.layout import DEFAULT_TOLERANCE_EMU, plan_alignment

IN = EMU_PER_INCH


def shape(sid: str, x, y, cx=IN, cy=IN, kind="shape") -> ShapeInfo:
    return ShapeInfo(id=sid, name=f"Box {sid}", kind=kind, x=x, y=y, cx=cx, cy=cy)


def deck(*shapes: ShapeInfo) -> DeckInfo:
    return DeckInfo(slides=[SlideInfo(number=1, part_name="ppt/slides/slide1.xml",
                                      shapes=list(shapes))])


class TestNearMissesAreCorrected:
    def test_a_stray_snaps_onto_an_established_line(self):
        """Two shapes agree on a left edge; a third is a hair off."""
        plan = plan_alignment(deck(
            shape("1", x=IN, y=IN),
            shape("2", x=IN, y=3 * IN),
            shape("3", x=IN + 9000, y=5 * IN),   # 0.0098in off
        ))
        assert len(plan.changes) == 1
        change = plan.changes[0]
        assert change.target == "3"
        assert change.op is Op.MOVE
        assert change.after == (IN, 5 * IN)
        assert change.origin is Origin.RULE

    def test_an_already_flush_deck_needs_nothing(self):
        plan = plan_alignment(deck(
            shape("1", x=IN, y=IN),
            shape("2", x=IN, y=3 * IN),
        ))
        assert plan.empty

    def test_a_deliberate_offset_is_never_touched(self):
        """Two inches out of line is a decision, not a slip."""
        plan = plan_alignment(deck(
            shape("1", x=IN, y=IN),
            shape("2", x=IN, y=3 * IN),
            shape("3", x=3 * IN, y=5 * IN),
        ))
        assert plan.empty


class TestMovementIsBounded:
    """The safety property. Nothing may move further than the tolerance.

    A first implementation clustered by gap-between-neighbours rather than by
    total span, so values at 0, 15 and 30 EMU chained into one group with a
    tolerance of 18. Measured on a real deck: a claimed 0.02in bound produced a
    0.031in move.
    """

    def test_no_change_exceeds_the_tolerance(self):
        plan = plan_alignment(deck(
            shape("1", x=IN, y=IN),
            shape("2", x=IN, y=3 * IN),
            shape("3", x=IN + 9000, y=5 * IN),
        ))
        for change in plan.changes:
            dx = abs(change.after[0] - change.before[0])
            dy = abs(change.after[1] - change.before[1])
            assert max(dx, dy) <= DEFAULT_TOLERANCE_EMU

    def test_a_chain_of_near_misses_cannot_compound(self):
        """Each neighbour within tolerance, the span far outside it.

        Two shapes establish the line so a correction actually happens; the
        third and fourth chain away from it. Under gap-based clustering the
        farthest would be dragged the whole span.
        """
        step = DEFAULT_TOLERANCE_EMU - 100
        plan = plan_alignment(deck(
            shape("1", x=IN, y=IN),
            shape("2", x=IN, y=3 * IN),
            shape("3", x=IN + step, y=5 * IN),
            shape("4", x=IN + 2 * step, y=7 * IN),
        ))
        assert plan.changes, "this must exercise the bound, not skip it"
        for change in plan.changes:
            dx = abs(change.after[0] - change.before[0])
            assert dx <= DEFAULT_TOLERANCE_EMU, "a chain moved a shape too far"
        moved = {c.target for c in plan.changes}
        assert "4" not in moved, (
            "the far end of a chain is outside the tolerance and must be left alone"
        )

    def test_a_wider_tolerance_is_honoured(self):
        wide = int(0.5 * IN)
        plan = plan_alignment(deck(
            shape("1", x=IN, y=IN),
            shape("2", x=IN, y=3 * IN),
            shape("3", x=IN + int(0.2 * IN), y=5 * IN),
        ), tolerance_emu=wide)
        assert len(plan.changes) == 1
        assert plan.worst_shift_emu <= wide


class TestExactAlignmentsAreNeverBroken:
    """Rule 2. Trading a real alignment for a smaller one makes the deck worse."""

    def test_a_shape_flush_with_another_is_held_still(self):
        # 1 and 2 share a left edge exactly. 3 and 4 establish a right-edge
        # line that 2 is fractionally off — but moving 2 would break its exact
        # left alignment with 1.
        plan = plan_alignment(deck(
            shape("1", x=IN, y=IN),
            shape("2", x=IN, y=3 * IN, cx=2 * IN),
            shape("3", x=5 * IN, y=5 * IN, cx=IN),
            shape("4", x=5 * IN, y=7 * IN, cx=IN),
        ))
        moved = {c.target for c in plan.changes}
        assert "2" not in moved, "an exact alignment was broken to fix a near one"

    def test_holding_a_shape_still_is_reported(self):
        plan = plan_alignment(deck(
            shape("1", x=IN, y=IN),
            shape("2", x=IN, y=3 * IN),
        ))
        assert any("flush" in note for note in plan.skipped)


class TestOnlyEstablishedLines:
    """Rule 3, and the one that makes the pass terminate.

    Two shapes near each other on two different edges used to produce two
    clusters that disagreed about which was the reference. The top cluster
    pulled one onto the other; the centre cluster pulled the other back. On a
    real deck they chased each other down the slide forever.
    """

    def test_two_shapes_with_no_shared_line_are_left_alone(self):
        plan = plan_alignment(deck(
            shape("1", x=IN, y=IN),
            shape("2", x=IN + 9000, y=3 * IN),
        ))
        assert plan.empty, "with no majority there is no line to snap to"

    def test_a_mutual_chase_does_not_arise(self):
        """The exact geometry of the observed cycle: two boxes, two edges.

        Their top edges are a near-miss and so are their centres, but the two
        clusters disagree about which shape is the reference. With no majority
        neither cluster acts, so there is nothing to chase.
        """
        plan = plan_alignment(deck(
            shape("1", x=IN, y=IN, cy=IN),
            shape("2", x=IN, y=IN + 6243, cy=IN + 14764),
        ))
        assert plan.empty, "two disagreeing shapes must not move each other"

    def test_three_shapes_two_agreeing_do_correct_the_third(self):
        plan = plan_alignment(deck(
            shape("1", x=IN, y=IN),
            shape("2", x=IN, y=3 * IN),
            shape("3", x=IN + 5000, y=5 * IN),
        ))
        assert [c.target for c in plan.changes] == ["3"]


class TestLayoutPlacedShapes:
    def test_shapes_without_geometry_are_never_moved(self):
        """A shape positioned by the layout must not be overridden."""
        plan = plan_alignment(deck(
            shape("1", x=None, y=None),
            shape("2", x=IN, y=IN),
            shape("3", x=IN, y=3 * IN),
        ))
        assert "1" not in {c.target for c in plan.changes}
        assert any("layout" in note for note in plan.skipped)

    def test_a_slide_with_one_shape_is_skipped(self):
        assert plan_alignment(deck(shape("1", x=IN, y=IN))).empty


class TestRendering:
    def test_reports_the_bound_it_respected(self):
        out = plan_alignment(deck(
            shape("1", x=IN, y=IN),
            shape("2", x=IN, y=3 * IN),
            shape("3", x=IN + 9000, y=5 * IN),
        )).render()
        assert "tolerance" in out
        assert "no text changes" in out

    def test_a_clean_deck_says_so(self):
        out = plan_alignment(deck(
            shape("1", x=IN, y=IN),
            shape("2", x=IN, y=3 * IN),
        )).render()
        assert "out of line" in out


@pytest.mark.fixtures
class TestRealDecks:
    def _fixture(self, name):
        from pathlib import Path

        path = (Path(__file__).resolve().parents[2] / "tests" / "fixtures"
                / "third-party" / name)
        if not path.is_file():
            pytest.skip("run scripts/fetch_fixtures.py")
        return path

    def test_a_real_deck_converges_in_one_pass(self, tmp_path):
        """The property that took three attempts to get right."""
        from slide_wright.apply import apply_changes
        from slide_wright.inspect import inspect

        deck_path = self._fixture("eia-aeo2023-release.pptx")
        plan = plan_alignment(inspect(deck_path))
        if plan.empty:
            pytest.skip("nothing to align in this fixture")

        cs = plan.to_changeset(str(deck_path))
        cs.approve_all()
        out = tmp_path / "aligned.pptx"
        apply_changes(deck_path, cs, out)

        assert plan_alignment(inspect(out)).empty, "a second pass still wants to move things"

    def test_aligning_a_real_deck_changes_no_content(self, tmp_path):
        from slide_wright.apply import apply_changes
        from slide_wright.diff import diff
        from slide_wright.inspect import inspect

        deck_path = self._fixture("eia-aeo2023-release.pptx")
        plan = plan_alignment(inspect(deck_path))
        if plan.empty:
            pytest.skip("nothing to align in this fixture")

        cs = plan.to_changeset(str(deck_path))
        cs.approve_all()
        out = tmp_path / "aligned.pptx"
        apply_changes(deck_path, cs, out)

        result = diff(deck_path, out)
        assert result.deltas
        assert not result.content_deltas
        assert {d.kind for d in result.deltas} == {"geometry"}


class TestOneRunReachesAFixpoint:
    """Snapping changes the geometry, so one round is not the end of it.

    Making a shape flush with a line adds a member to that line, which can turn
    a value two shapes shared into one three do — and pull in a fourth that was
    a lone stray before. Measured on a 41-slide deck: 66 corrections, then 11
    more on the pass after, then none.

    The planning is therefore iterated internally. Telling a user to run it
    repeatedly until it stops changing things is not something to ask of anyone
    pointing a tool at a deck that matters.
    """

    def _cascading_deck(self):
        """A stray whose correction creates the line that catches the next one."""
        step = 9000
        return deck(
            shape("1", x=IN, y=IN, cx=2 * IN),
            shape("2", x=IN, y=3 * IN, cx=2 * IN),
            # Left edge is a hair off the 1/2 line; its right edge then lands
            # where 4 and 5 nearly are.
            shape("3", x=IN + step, y=5 * IN, cx=2 * IN),
            shape("4", x=4 * IN, y=7 * IN, cx=IN),
            shape("5", x=4 * IN, y=9 * IN, cx=IN),
        )

    def test_the_plan_is_its_own_fixpoint(self):
        """Applying it and re-planning must find nothing."""
        plan = plan_alignment(self._cascading_deck())
        if plan.empty:
            pytest.skip("this arrangement needs no correction")

        moved = {c.target: c.after for c in plan.changes}
        after = deck(*[
            shape(s.id, x=moved.get(s.id, (s.x, s.y))[0],
                  y=moved.get(s.id, (s.x, s.y))[1], cx=s.cx, cy=s.cy)
            for s in self._cascading_deck().slides[0].shapes
        ])
        assert plan_alignment(after).empty, "a second run still wants to move things"

    def test_the_bound_holds_across_rounds(self):
        """A shape becomes anchored once flush, so it moves at most once."""
        plan = plan_alignment(self._cascading_deck())
        for change in plan.changes:
            dx = abs(change.after[0] - change.before[0])
            dy = abs(change.after[1] - change.before[1])
            assert max(dx, dy) <= DEFAULT_TOLERANCE_EMU

    def test_each_shape_is_moved_at_most_once(self):
        """One change per shape, holding its final position, not a move per round."""
        plan = plan_alignment(self._cascading_deck())
        targets = [c.target for c in plan.changes]
        assert len(targets) == len(set(targets))

    def test_iteration_is_bounded(self):
        """A pathological deck must not spin; MAX_ROUNDS caps it."""
        from slide_wright.layout import MAX_ROUNDS

        assert MAX_ROUNDS > 0
        many = deck(*[shape(str(i), x=IN + i * 3, y=IN + i * IN) for i in range(30)])
        plan_alignment(many)  # must return, not hang
