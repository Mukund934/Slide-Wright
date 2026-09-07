"""Structural diff — what changed, in a reviewer's terms.

The case that motivates this module: an edit produces a change nobody asked
for, the deck is blocked, and the reviewer is handed a part name. Everything
here exists so that moment produces a sentence instead of a filename.
"""

from __future__ import annotations

import shutil

import pytest

from slide_wright.apply import apply_changes
from slide_wright.changeset import Change, ChangeSet, Op
from slide_wright.diff import VISIBLE_MOVE_EMU, ShapeDelta, _describe_text_change, diff
from slide_wright.inspect import inspect


def approved(deck, *changes: Change) -> ChangeSet:
    cs = ChangeSet(deck=str(deck))
    for c in changes:
        cs.add(c)
    cs.approve_all()
    return cs


@pytest.fixture
def table(adversarial_deck):
    return next(s for s in inspect(adversarial_deck).all_shapes() if s.kind == "table")


@pytest.fixture
def positioned(adversarial_deck):
    """A plain shape the applier will actually move.

    Not a placeholder -- those are positioned by the layout and moving one is
    refused. Not a chart -- read-only under ADR-0009. Either would make this
    fixture yield a shape whose move silently does not happen, and the diff
    assertions would then be testing the refusal rather than the diff.
    """
    return next(
        s for s in inspect(adversarial_deck).slides[1].shapes
        if s.kind == "shape" and s.x is not None and not s.geometry_inherited
    )


class TestNoDifference:
    def test_a_copy_has_no_structural_differences(self, adversarial_deck, tmp_path):
        copy = tmp_path / "copy.pptx"
        shutil.copy(adversarial_deck, copy)
        result = diff(adversarial_deck, copy)
        assert not result.changed
        assert "No structural differences" in result.render()

    def test_a_deck_does_not_differ_from_itself(self, adversarial_deck):
        assert not diff(adversarial_deck, adversarial_deck).changed


class TestTextChanges:
    def test_a_cell_edit_is_reported_as_a_text_change(
        self, adversarial_deck, table, tmp_path
    ):
        out = tmp_path / "out.pptx"
        apply_changes(adversarial_deck, approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table.id}/r1/c1", before="9.4x", after="11.8x")), out)

        deltas = diff(adversarial_deck, out).deltas
        assert len(deltas) == 1
        assert deltas[0].kind == "text"
        assert deltas[0].slide == 3
        assert deltas[0].is_content

    def test_the_description_shows_the_changed_region_not_the_string_head(
        self, adversarial_deck, table, tmp_path
    ):
        """A table's text is long; truncating both sides shows the same prefix twice."""
        out = tmp_path / "out.pptx"
        apply_changes(adversarial_deck, approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table.id}/r1/c1", before="9.4x", after="11.8x")), out)

        description = diff(adversarial_deck, out).deltas[0].description
        assert "9.4" in description and "11.8" in description, description


class TestDescribeTextChange:
    """The helper directly, because its whole value is in the wording."""

    def test_isolates_a_change_inside_a_long_string(self):
        before = "CompanyEV/EBITDAMarginGrowthAlpha Corp9.4x22.1%18%Beta Industries"
        after = "CompanyEV/EBITDAMarginGrowthAlpha Corp11.8x22.1%18%Beta Industries"
        out = _describe_text_change(before, after)
        assert "9.4 -> 11.8" in out
        assert len(out) < len(before), "the point is to be shorter than the string"

    def test_a_short_change_needs_no_context(self):
        assert _describe_text_change("Q3 results", "Q4 results") == "Q[3 -> 4] results"

    def test_a_wholly_different_string_is_shown_whole(self):
        assert _describe_text_change("short", "other") == "[short -> other]"

    def test_deletion_and_insertion_are_named(self):
        assert "(nothing)" in _describe_text_change("text", "")
        assert "(nothing)" in _describe_text_change("", "text")

    def test_newlines_do_not_break_the_line(self):
        assert "\n" not in _describe_text_change("a\nb", "a\nc")


class TestGeometryChanges:
    def test_a_move_is_reported_in_inches(self, adversarial_deck, positioned, tmp_path):
        out = tmp_path / "out.pptx"
        apply_changes(adversarial_deck, approved(adversarial_deck, Change(
            id="c1", op=Op.MOVE, slide=2, target=positioned.id,
            before=(positioned.x, positioned.y),
            after=(positioned.x + 457200, positioned.y))), out)

        deltas = diff(adversarial_deck, out).deltas
        assert [d.kind for d in deltas] == ["geometry"]
        assert "+0.50in" in deltas[0].description

    def test_a_move_is_not_a_content_change(self, adversarial_deck, positioned, tmp_path):
        """Moving a box changes how the deck looks, not what it says."""
        out = tmp_path / "out.pptx"
        apply_changes(adversarial_deck, approved(adversarial_deck, Change(
            id="c1", op=Op.MOVE, slide=2, target=positioned.id,
            before=(positioned.x, positioned.y),
            after=(positioned.x + 457200, positioned.y))), out)

        result = diff(adversarial_deck, out)
        assert result.deltas and not result.content_deltas

    def test_a_sub_threshold_move_is_flagged_as_probable_rounding(
        self, adversarial_deck, positioned, tmp_path
    ):
        """Reported, not hidden — but named so nobody chases a save artefact."""
        out = tmp_path / "out.pptx"
        apply_changes(adversarial_deck, approved(adversarial_deck, Change(
            id="c1", op=Op.MOVE, slide=2, target=positioned.id,
            before=(positioned.x, positioned.y),
            after=(positioned.x + VISIBLE_MOVE_EMU - 1, positioned.y))), out)

        deltas = diff(adversarial_deck, out).deltas
        assert deltas, "a sub-threshold move must still be reported"
        assert "rounding" in deltas[0].description

    def test_a_resize_is_reported_with_both_sizes(
        self, adversarial_deck, positioned, tmp_path
    ):
        out = tmp_path / "out.pptx"
        apply_changes(adversarial_deck, approved(adversarial_deck, Change(
            id="c1", op=Op.RESIZE, slide=2, target=positioned.id,
            before=(positioned.cx, positioned.cy),
            after=(positioned.cx // 2, positioned.cy))), out)

        deltas = [d for d in diff(adversarial_deck, out).deltas if d.kind == "size"]
        assert deltas and "->" in deltas[0].description


class TestRendering:
    def test_groups_differences_by_slide(self, adversarial_deck, table, positioned, tmp_path):
        out = tmp_path / "out.pptx"
        apply_changes(adversarial_deck, approved(
            adversarial_deck,
            Change(id="c1", op=Op.SET_TABLE_CELL, slide=3,
                   target=f"{table.id}/r1/c1", before="9.4x", after="11.8x"),
            Change(id="c2", op=Op.MOVE, slide=2, target=positioned.id,
                   before=(positioned.x, positioned.y),
                   after=(positioned.x + 457200, positioned.y)),
        ), out)

        out_text = diff(adversarial_deck, out).render()
        assert "slide 2" in out_text and "slide 3" in out_text

    def test_separates_what_it_says_from_how_it_looks(
        self, adversarial_deck, table, positioned, tmp_path
    ):
        out = tmp_path / "out.pptx"
        apply_changes(adversarial_deck, approved(
            adversarial_deck,
            Change(id="c1", op=Op.SET_TABLE_CELL, slide=3,
                   target=f"{table.id}/r1/c1", before="9.4x", after="11.8x"),
            Change(id="c2", op=Op.MOVE, slide=2, target=positioned.id,
                   before=(positioned.x, positioned.y),
                   after=(positioned.x + 457200, positioned.y)),
        ), out)

        text = diff(adversarial_deck, out).render()
        assert "1 change what the deck says" in text
        assert "1 change how it looks" in text

    def test_a_long_diff_is_truncated_with_a_count(self, adversarial_deck, tmp_path):
        copy = tmp_path / "copy.pptx"
        shutil.copy(adversarial_deck, copy)
        result = diff(adversarial_deck, copy)
        from slide_wright.diff import ShapeDelta
        result.deltas = [
            ShapeDelta(slide=1, shape_id=str(i), kind="text", description=f"change {i}")
            for i in range(60)
        ]
        text = result.render(limit=10)
        assert "50 more difference(s)" in text


class TestAcceptsInspectedDecks:
    def test_takes_deckinfo_so_a_caller_need_not_parse_twice(self, adversarial_deck, tmp_path):
        copy = tmp_path / "copy.pptx"
        shutil.copy(adversarial_deck, copy)
        assert not diff(inspect(adversarial_deck), inspect(copy)).changed


class TestFigureChanges:
    """The sharpest claim this product makes is not "content unchanged".

    It is *no figure changed*. Someone asking for a formatting pass on a
    pitchbook does not want reassurance about prose; they want to know the
    multiples are the ones they signed off.
    """

    def delta(self, kind, before, after):
        return ShapeDelta(slide=1, shape_id="1", kind=kind,
                          description="", before=before, after=after)

    def test_a_moved_number_is_a_figure_change(self):
        assert self.delta("text", "9.4x", "11.8x").changes_figures
        assert self.delta("table", "312", "340").changes_figures

    def test_presentation_is_never_a_figure_change(self):
        """A typeface carrying digits must not read as a moved number."""
        assert not self.delta("formatting", "Arial 10", "Arial 12").changes_figures

    def test_prose_edited_without_touching_a_number_is_not(self):
        assert not self.delta("text", "teh margin", "the margin").changes_figures
        assert not self.delta("text", "Revenue", "Revenue up").changes_figures

    def test_reordering_digits_counts(self):
        """Comparing sets would call this unchanged. It is a different deck."""
        assert self.delta("text", "9.4 and 4.9", "4.9 and 9.4").changes_figures

    def test_it_errs_toward_flagging(self):
        """This claim is a negative being proved.

        A false alarm costs a second look; a missed one costs the guarantee. So
        a label that merely contains a digit is flagged rather than parsed for
        whether it is really a quantity.
        """
        assert self.delta("text", "Q3 2026", "Q4 2026").changes_figures

    def test_the_deck_reports_them_separately(self, adversarial_deck, table, tmp_path):
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table.id}/r1/c1", before="9.4x", after="11.8x"))
        apply_changes(adversarial_deck, cs, out)

        result = diff(adversarial_deck, out)
        assert result.figure_deltas, "a changed multiple is a figure change"
        assert all(d.is_content for d in result.figure_deltas)
