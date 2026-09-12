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

import re
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
from slide_wright.package import Package


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


class TestTheCommentsRowIsEarned:
    """`Comments` read UNKNOWN for the life of the table. Now it is measured.

    "Adding a deck that contains one is the only way to change it" is the
    module's own rule, and no deck in the corpus had a comment: three of the
    real fixtures carry `ppt/commentAuthors.xml` with nothing beside it, which
    is what usually happens to comments before a deck is published.

    python-pptx cannot write one, so the part is assembled by hand after the
    save -- the only fixture here that is. These tests check the two things that
    makes it worth having: that the package still holds together, and that the
    part survives an edit rather than being quietly dropped.
    """

    def test_the_corpus_carries_a_comment(self, adversarial_deck):
        pkg = Package.open(adversarial_deck)
        assert "ppt/comments/comment1.xml" in pkg.parts
        assert "ppt/commentAuthors.xml" in pkg.parts

    def test_every_relationship_it_adds_resolves(self, adversarial_deck):
        """A part nothing points at is not in the deck, whatever the zip holds."""
        import posixpath
        import zipfile

        from lxml import etree

        with zipfile.ZipFile(adversarial_deck) as z:
            names = set(z.namelist())
            dangling = []
            for entry in names:
                if not entry.endswith(".rels"):
                    continue
                base = entry.rsplit("_rels/", 1)[0].rstrip("/")
                for rel in etree.fromstring(z.read(entry)).iter():
                    target = rel.get("Target")
                    if not target or rel.get("TargetMode") == "External":
                        continue
                    resolved = (
                        posixpath.normpath(posixpath.join(base, target)) if base else target
                    )
                    if resolved not in names:
                        dangling.append((entry, target))
        assert dangling == []

    def test_both_parts_are_declared_in_the_content_types(self, adversarial_deck):
        import zipfile

        with zipfile.ZipFile(adversarial_deck) as z:
            declared = z.read("[Content_Types].xml").decode()
        assert "/ppt/comments/comment1.xml" in declared
        assert "/ppt/commentAuthors.xml" in declared

    def test_a_comment_survives_an_edit_byte_for_byte(self, adversarial_deck, tmp_path):
        """The measurement the compatibility row is actually making."""
        from slide_wright.apply import apply_changes
        from slide_wright.changeset import Change, ChangeSet, Op
        from slide_wright.fidelity import compare
        from slide_wright.inspect import inspect

        deck = inspect(adversarial_deck)
        slide, shape = next(
            (sl, sh) for sl in deck.slides for sh in sl.shapes if sh.runs
        )
        changes = ChangeSet(deck=str(adversarial_deck))
        changes.add(Change(
            id="c", op=Op.SET_TEXT, slide=slide.number, target=shape.id,
            before=shape.runs[0].text, after=shape.runs[0].text + ".",
        ))
        changes.approve_all()
        out = tmp_path / "edited.pptx"
        apply_changes(adversarial_deck, changes, out)

        moved = [
            d for d in compare(adversarial_deck, out).deltas
            if "comment" in d.name.lower() and d.status != "identical"
        ]
        assert moved == [], "an edit must not touch a comment it was never asked about"


class TestModernCommentsStayUnknown:
    """Listed, not measured, and that is the honest state.

    The 2021 comment schema is a different part from the legacy one, and no deck
    on this machine has either the part or a sample to copy -- two decks carry
    `ppt/authors.xml`, its author list, and none carries a comment written
    against it. The legacy fixture here was defensible because a real
    PowerPoint-written sibling existed in the same namespace to copy the
    declarations from; there is no equivalent for this, so inventing one would
    put a row in the table that rests on a guess.

    Listed rather than omitted, because the module says why: a construct missing
    from a compatibility table reads as one that was considered and found fine.
    """

    def test_it_is_a_construct_the_table_knows_about(self):
        assert any(c.name == "Modern comments" for c in CONSTRUCTS)

    def test_it_has_no_verdict_because_nothing_was_measured(self):
        construct = next(c for c in CONSTRUCTS if c.name == "Modern comments")
        assert Row(construct=construct).verdict == UNKNOWN
        assert Row(construct=construct).editing == UNKNOWN

    def test_the_legacy_row_does_not_answer_for_it(self):
        """Two constructs, two parts, two questions."""
        legacy = next(c for c in CONSTRUCTS if c.name == "Comments")
        modern = next(c for c in CONSTRUCTS if c.name == "Modern comments")
        assert legacy.part != modern.part
        assert not re.search(legacy.part, "ppt/comments/modernComment_abc.xml")
        assert re.search(modern.part, "ppt/comments/modernComment_abc.xml")
