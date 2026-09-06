"""In-place application of approved changes.

The property under test is narrowness: an edit must touch the target and
nothing else. Slide-level preservation is table stakes here — these tests
assert preservation *within* the edited slide, down to the character.
"""

from __future__ import annotations

import difflib
import zipfile

import pytest

from slide_wright.apply import ApplyError, apply_changes
from slide_wright.changeset import Change, ChangeSet, Op
from slide_wright.fidelity import compare
from slide_wright.inspect import inspect
from slide_wright.package import Package


def slide_body(path, part: str) -> str:
    raw = zipfile.ZipFile(path).read(part).decode("utf-8")
    return raw[raw.index("<p:sld"):]


def approved(deck, *changes: Change) -> ChangeSet:
    cs = ChangeSet(deck=str(deck))
    for c in changes:
        cs.add(c)
    cs.approve_all()
    return cs


@pytest.fixture
def table_shape(adversarial_deck):
    return next(s for s in inspect(adversarial_deck).all_shapes() if s.kind == "table")


class TestNarrowness:
    def test_a_cell_edit_changes_two_characters(self, adversarial_deck, table_shape, tmp_path):
        """The strongest form of the promise, measured rather than asserted."""
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table_shape.id}/r1/c1", before="9.4x", after="11.8x",
        ))
        apply_changes(adversarial_deck, cs, out)

        before = slide_body(adversarial_deck, "ppt/slides/slide3.xml")
        after = slide_body(out, "ppt/slides/slide3.xml")
        matcher = difflib.SequenceMatcher(None, before, after)
        edits = [op for op in matcher.get_opcodes() if op[0] != "equal"]
        preserved = sum(b.size for b in matcher.get_matching_blocks()) / len(before)

        assert len(edits) <= 3, f"expected a surgical edit, got {edits}"
        assert preserved > 0.99, f"only {preserved:.2%} of the edited slide preserved"

    def test_only_the_target_slide_part_is_touched(self, adversarial_deck, table_shape, tmp_path):
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table_shape.id}/r1/c1", before="9.4x", after="11.8x",
        ))
        result = apply_changes(adversarial_deck, cs, out)
        assert result.touched_parts == {"ppt/slides/slide3.xml"}
        assert [d.name for d in compare(adversarial_deck, out).changed] == [
            "ppt/slides/slide3.xml"
        ]

    def test_run_count_is_unchanged_by_a_text_edit(self, adversarial_deck, tmp_path):
        deck_info = inspect(adversarial_deck)
        title = deck_info.slide(1).shapes[0]
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TEXT, slide=1, target=title.id,
            before=title.text, after="Renamed Corpus",
        ))
        apply_changes(adversarial_deck, cs, out)
        rep = compare(adversarial_deck, out)
        assert rep.output_census.text_runs == rep.source_census.text_runs


class TestOperations:
    def test_set_text_writes_the_new_value(self, adversarial_deck, tmp_path):
        d = inspect(adversarial_deck)
        title = d.slide(1).shapes[0]
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TEXT, slide=1, target=title.id,
            before=title.text, after="Renamed Corpus",
        ))
        apply_changes(adversarial_deck, cs, out)
        assert inspect(out).slide(1).title == "Renamed Corpus"

    def test_set_table_cell_writes_the_new_value(self, adversarial_deck, table_shape, tmp_path):
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table_shape.id}/r1/c1", before="9.4x", after="11.8x",
        ))
        apply_changes(adversarial_deck, cs, out)
        assert "11.8x" in inspect(out).slide(3).text
        assert "9.4x" not in inspect(out).slide(3).text

    def test_move_updates_offset(self, adversarial_deck, tmp_path):
        d = inspect(adversarial_deck)
        shape = next(s for s in d.slide(6).shapes if s.x is not None)
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.MOVE, slide=6, target=shape.id,
            before=(shape.x, shape.y), after=(shape.x + 100000, shape.y),
        ))
        apply_changes(adversarial_deck, cs, out)
        moved = next(s for s in inspect(out).slide(6).shapes if s.id == shape.id)
        assert moved.x == shape.x + 100000

    def test_resize_updates_extent(self, adversarial_deck, tmp_path):
        d = inspect(adversarial_deck)
        shape = next(s for s in d.slide(6).shapes if s.cx)
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.RESIZE, slide=6, target=shape.id,
            before=(shape.cx, shape.cy), after=(shape.cx // 2, shape.cy),
        ))
        apply_changes(adversarial_deck, cs, out)
        resized = next(s for s in inspect(out).slide(6).shapes if s.id == shape.id)
        assert resized.cx == shape.cx // 2


class TestRefusals:
    """Fail closed. Never approximate a change we cannot make precisely."""

    def test_unsupported_op_is_refused_not_approximated(self, adversarial_deck, tmp_path):
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.DELETE_SHAPE, slide=1, target="2",
        ))
        with pytest.raises(ApplyError, match="cannot perform"):
            apply_changes(adversarial_deck, cs, tmp_path / "o.pptx")

    def test_empty_change_set_is_refused(self, adversarial_deck, tmp_path):
        with pytest.raises(ApplyError, match="no approved changes"):
            apply_changes(adversarial_deck, ChangeSet(deck=str(adversarial_deck)),
                          tmp_path / "o.pptx")

    def test_rejected_changes_are_not_applied(self, adversarial_deck, tmp_path):
        d = inspect(adversarial_deck)
        title = d.slide(1).shapes[0]
        cs = ChangeSet(deck=str(adversarial_deck))
        cs.add(Change(id="c1", op=Op.SET_TEXT, slide=1, target=title.id,
                      before=title.text, after="Should Not Appear"))
        cs.add(Change(id="c2", op=Op.SET_TEXT, slide=1, target=title.id,
                      before=title.text, after="Should Appear"))
        cs.approve("c2")
        cs.reject("c1")
        out = tmp_path / "o.pptx"
        apply_changes(adversarial_deck, cs, out)
        assert inspect(out).slide(1).title == "Should Appear"

    def test_missing_slide_is_recorded_as_failure(self, adversarial_deck, tmp_path):
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TEXT, slide=999, target="1", before="x", after="y",
        ))
        result = apply_changes(adversarial_deck, cs, tmp_path / "o.pptx")
        assert not result.ok
        assert "not found" in result.failed[0][1]

    def test_missing_target_is_recorded_as_failure(self, adversarial_deck, tmp_path):
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TEXT, slide=1, target="99999", before="x", after="y",
        ))
        result = apply_changes(adversarial_deck, cs, tmp_path / "o.pptx")
        assert not result.ok


class TestSourceIsImmutable:
    def test_the_source_file_is_never_modified(self, adversarial_deck, table_shape, tmp_path):
        original = adversarial_deck.read_bytes()
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table_shape.id}/r1/c1", before="9.4x", after="11.8x",
        ))
        apply_changes(adversarial_deck, cs, tmp_path / "o.pptx")
        assert adversarial_deck.read_bytes() == original


class TestIntegrityPreserved:
    def test_native_objects_survive_an_edit(self, adversarial_deck, table_shape, tmp_path):
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table_shape.id}/r1/c1", before="9.4x", after="11.8x",
        ))
        apply_changes(adversarial_deck, cs, out)
        rep = compare(adversarial_deck, out)
        assert not rep.native_losses
        assert not rep.rasterisation_suspected
        assert rep.structurally_intact

    def test_output_is_a_valid_package(self, adversarial_deck, table_shape, tmp_path):
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table_shape.id}/r1/c1", before="9.4x", after="11.8x",
        ))
        apply_changes(adversarial_deck, cs, out)
        assert Package.open(out).slide_count == Package.open(adversarial_deck).slide_count


class TestFailuresExplainThemselves:
    """A refusal is only useful if it says what to do differently.

    `_apply_one` returned a single boolean, so "the shape is not on this slide"
    and "the shape is here but the edit does not apply to it" produced the same
    message: "target not found on slide". That sends a reader hunting for a
    shape id that is in fact present, and it fired on the common case of a run
    whose font size is inherited from the layout.
    """

    def _why(self, deck, change, tmp_path) -> str:
        cs = approved(deck, change)
        result = apply_changes(deck, cs, tmp_path / "out.pptx")
        assert result.failed, "expected this change to fail"
        return result.failed[0][1]

    def test_a_missing_shape_says_so_and_names_the_slide(self, adversarial_deck, tmp_path):
        why = self._why(adversarial_deck, Change(
            id="c1", op=Op.SET_TEXT, slide=1, target="999",
            before="x", after="y"), tmp_path)
        assert "no shape with id '999'" in why and "slide 1" in why

    def test_wrong_before_text_names_the_text_not_the_shape(self, adversarial_deck, tmp_path):
        shape = next(s for s in inspect(adversarial_deck).slides[0].shapes if s.has_text)
        why = self._why(adversarial_deck, Change(
            id="c1", op=Op.SET_TEXT, slide=1, target=shape.id,
            before="text that is not in this deck", after="y"), tmp_path)
        assert "does not contain the text" in why
        assert "not found" not in why, "the shape was found; saying otherwise misleads"

    def test_an_inherited_font_size_is_explained_not_denied(self, adversarial_deck, tmp_path):
        """The commonest real case: a run with no explicit formatting override."""
        shape = next(s for s in inspect(adversarial_deck).slides[1].shapes
                     if s.x is not None)
        why = self._why(adversarial_deck, Change(
            id="c1", op=Op.SET_FONT_SIZE, slide=2, target=shape.id,
            before=None, after=14), tmp_path)
        assert "inherited from the layout" in why
        assert "not found" not in why

    def test_a_layout_placed_shape_explains_why_it_cannot_move(
        self, adversarial_deck, tmp_path
    ):
        shape = next(s for s in inspect(adversarial_deck).slides[0].shapes
                     if s.x is None)
        why = self._why(adversarial_deck, Change(
            id="c1", op=Op.MOVE, slide=1, target=shape.id,
            before=(0, 0), after=(100, 100)), tmp_path)
        assert "placed by the layout" in why
        assert "not found" not in why
