"""The compatibility matrix, and the one rule that makes it worth reading.

A table of "which constructs does it handle" drifts the same way every time. A
row nobody has evidence for gets filled in with what the author believes,
because a blank looks like an oversight and a tick looks finished. Once that has
happened twice, a tick meaning "we measured this" is indistinguishable from one
meaning "we think so", and every other row is worth less.

So the verdict is not a field. It is derived from counts that only an actual
measurement can raise, and these tests are what stop someone reintroducing a way
to write one by hand.
"""

from __future__ import annotations

import shutil
import zipfile

import pytest

from slide_wright.apply import apply_changes
from slide_wright.changeset import Change, ChangeSet, Op
from slide_wright.compatibility import (
    CONSTRUCTS,
    DAMAGED,
    PRESERVED,
    REFUSED,
    UNKNOWN,
    Construct,
    Matrix,
    Row,
    measure,
)
from slide_wright.inspect import inspect


class TestUnknownStaysUnknown:
    def test_a_construct_no_deck_contains_is_unknown(self):
        assert Row(construct=Construct("Ink")).verdict == UNKNOWN

    def test_it_stays_unknown_however_the_construct_is_described(self):
        """A note, a refusal, a rationale — none of them are evidence.

        This is the exact shape of the drift: someone writes a thoughtful
        sentence about a construct, and the sentence starts reading as a
        finding.
        """
        described = Construct(
            "Ink", marker="<p14:ink",
            refused_because="not implemented",
            note="we are fairly confident this is fine",
        )
        assert Row(construct=described).verdict == UNKNOWN

    def test_there_is_no_field_to_set_it_by_hand(self):
        """`verdict` is derived. If it ever becomes assignable, this fails."""
        row = Row(construct=Construct("Ink"))
        with pytest.raises(AttributeError):
            row.verdict = PRESERVED

    def test_one_measured_deck_is_enough_to_stop_being_unknown(self):
        row = Row(construct=Construct("Ink"), decks_containing=1, decks_preserved=1)
        assert row.verdict == PRESERVED

    def test_a_construct_that_did_not_survive_is_not_preserved(self):
        row = Row(construct=Construct("Ink"), decks_containing=2, decks_preserved=1)
        assert row.verdict == DAMAGED

    def test_an_unmeasured_construct_says_unknown_about_editing_too(self):
        """Not "allowed". Nobody has tried."""
        assert Row(construct=Construct("Ink")).editing == UNKNOWN

    def test_a_refusal_is_reported_without_a_measurement(self):
        """It is a decision in the code, not a finding about a deck."""
        row = Row(construct=Construct("Macros", refused_because="package.py"))
        assert row.editing == REFUSED
        assert row.verdict == UNKNOWN, "still nothing measured about preservation"

    def test_the_word_allowed_is_never_used(self):
        """There is no guard against editing an OLE object and also no way to
        edit one. "Allowed" would read as support for something nobody built —
        the same overclaim as a ticked row nobody measured."""
        row = Row(construct=Construct("OLE"), decks_containing=1, decks_preserved=1)
        assert row.editing == "not refused"


class TestTheRenderedTableSaysWhatItMeans:
    def _matrix(self, *rows: Row) -> Matrix:
        return Matrix(rows=list(rows), decks=["a.pptx", "b.pptx"])

    def test_an_unknown_row_is_shown_rather_than_omitted(self):
        """A construct missing from a compatibility table reads as one that was
        considered and found fine."""
        table = self._matrix(Row(construct=Construct("Ink annotations"))).render()
        assert "Ink annotations" in table
        assert "UNKNOWN" in table

    def test_it_says_why_it_is_unknown(self):
        table = self._matrix(Row(construct=Construct("Ink"))).render()
        assert "nothing to measure" in table

    def test_a_measured_row_says_how_many_decks(self):
        table = self._matrix(
            Row(construct=Construct("Tables"), decks_containing=1, decks_preserved=1)
        ).render()
        assert "1 of 2 decks" in table


class TestMeasuringAgainstRealPackages:
    """The counts have to come from packages, not from the construct list."""

    def _edited(self, deck, tmp_path):
        info = inspect(deck)
        for slide in info.slides:
            for shape in slide.shapes:
                if shape.kind in ("table", "chart") or not shape.runs:
                    continue
                run = next((r for r in shape.runs if r.text.strip()), None)
                if run is None:
                    continue
                changeset = ChangeSet(deck=str(deck))
                changeset.add(Change(id="m1", op=Op.SET_TEXT, slide=slide.number,
                                     target=shape.id, before=run.text,
                                     after=run.text + "."))
                changeset.approve_all()
                out = tmp_path / "edited.pptx"
                result = apply_changes(deck, changeset, out)
                if result.ok:
                    return out
        pytest.skip("fixture has nothing editable")

    def test_a_construct_the_deck_has_is_counted(self, adversarial_deck, tmp_path):
        matrix = measure([(adversarial_deck, self._edited(adversarial_deck, tmp_path))])
        tables = next(r for r in matrix.rows if r.construct.name == "Native tables")
        assert tables.decks_containing == 1
        assert tables.verdict == PRESERVED

    def test_a_construct_the_deck_lacks_stays_unknown(self, adversarial_deck, tmp_path):
        matrix = measure([(adversarial_deck, self._edited(adversarial_deck, tmp_path))])
        ink = next(r for r in matrix.rows if r.construct.name == "Ink annotations")
        assert ink.decks_containing == 0
        assert ink.verdict == UNKNOWN

    def test_a_construct_whose_parts_were_dropped_reads_as_damaged(
        self, adversarial_deck, tmp_path
    ):
        """The measurement has to be capable of saying no."""
        stripped = tmp_path / "no_charts.pptx"
        with zipfile.ZipFile(adversarial_deck) as zin, \
                zipfile.ZipFile(stripped, "w") as zout:
            for info in zin.infolist():
                if info.filename.startswith("ppt/charts/"):
                    continue
                zout.writestr(info.filename, zin.read(info.filename))
        matrix = measure([(adversarial_deck, stripped)])
        charts = next(r for r in matrix.rows if r.construct.name == "Charts")
        assert charts.verdict == DAMAGED

    def test_an_untouched_copy_preserves_everything_it_contains(
        self, adversarial_deck, tmp_path
    ):
        copy = tmp_path / "same.pptx"
        shutil.copy(adversarial_deck, copy)
        matrix = measure([(adversarial_deck, copy)])
        assert all(r.verdict == PRESERVED for r in matrix.measured)


class TestTheConstructListItself:
    def test_every_construct_can_be_detected_somehow(self):
        """A row with neither a marker nor a part could never be measured, so it
        would be permanently UNKNOWN for a reason that is a bug rather than a
        fact about the decks."""
        for construct in CONSTRUCTS:
            assert construct.marker or construct.part, construct.name

    def test_every_refusal_names_where_it_is_enforced(self):
        """"REFUSED" with no citation is the same unfalsifiable claim as a tick."""
        for construct in CONSTRUCTS:
            if construct.refused_because:
                assert any(
                    token in construct.refused_because
                    for token in (".py", "ADR-")
                ), construct.name

    def test_names_are_unique(self):
        names = [c.name for c in CONSTRUCTS]
        assert len(names) == len(set(names))
