"""Refreshing a deck's figures from a source.

The dangerous property of this feature is that a wrong number looks exactly
like a right one. So the tests care most about what it *refuses* to do: match
by position, match on thin evidence, or change a figure it cannot cite.
"""

from __future__ import annotations

import pytest

from slide_wright.changeset import Op
from slide_wright.inspect import EMU_PER_INCH, DeckInfo, ShapeInfo, SlideInfo, TextRun
from slide_wright.refresh import plan_refresh
from slide_wright.sources import SourceSet, read_csv

W, H = int(13.333 * EMU_PER_INCH), int(7.5 * EMU_PER_INCH)

DECK_TABLE = [
    ["Company", "EV/EBITDA", "Margin"],
    ["Alpha Corp", "9.4x", "22.1%"],
    ["Beta Industries", "11.2x", "19.8%"],
]


def table_shape(grid, sid="7") -> ShapeInfo:
    cells = [c for row in grid for c in row]
    return ShapeInfo(
        id=sid, name="Table", kind="table",
        x=0, y=0, cx=EMU_PER_INCH, cy=EMU_PER_INCH,
        table_rows=len(grid), table_cols=len(grid[0]),
        runs=[TextRun(text=c) for c in cells],
    )


def deck_with_table(grid=DECK_TABLE, slide=3) -> DeckInfo:
    return DeckInfo(
        slide_width=W, slide_height=H,
        slides=[SlideInfo(number=slide, part_name=f"s{slide}", shapes=[table_shape(grid)])],
    )


@pytest.fixture
def source(tmp_path):
    """Alpha's multiple moved; Beta's margin moved; everything else is the same."""
    path = tmp_path / "comps.csv"
    path.write_text(
        "Company,EV/EBITDA,Margin\n"
        "Alpha Corp,11.8x,22.1%\n"
        "Beta Industries,11.2x,21.4%\n",
        encoding="utf-8",
    )
    return SourceSet([read_csv(path)])


class TestMatching:
    def test_finds_the_changed_figures(self, source):
        plan = plan_refresh(deck_with_table(), source)
        assert len(plan.updates) == 2
        assert {(m.current, m.citation.value) for m in plan.updates} == {
            ("9.4x", "11.8x"), ("19.8%", "21.4%"),
        }

    def test_confirms_the_unchanged_figures(self, source):
        """Knowing a figure is still right is as useful as knowing it moved."""
        plan = plan_refresh(deck_with_table(), source)
        assert len(plan.confirmed) == 2
        assert all(not m.changed for m in plan.confirmed)

    def test_every_match_carries_a_citation(self, source):
        plan = plan_refresh(deck_with_table(), source)
        for match in plan.matches:
            assert match.citation.reference.startswith("comps.csv!")

    def test_citation_points_at_the_right_cell(self, source):
        plan = plan_refresh(deck_with_table(), source)
        alpha = next(m for m in plan.updates if m.current == "9.4x")
        assert alpha.citation.reference == "comps.csv!B2"

    def test_matching_is_by_label_not_position(self, tmp_path):
        """Reordered source rows and columns must still match correctly."""
        path = tmp_path / "shuffled.csv"
        path.write_text(
            "Company,Margin,EV/EBITDA\n"
            "Beta Industries,21.4%,11.2x\n"
            "Alpha Corp,22.1%,11.8x\n",
            encoding="utf-8",
        )
        plan = plan_refresh(deck_with_table(), SourceSet([read_csv(path)]))
        alpha = next(m for m in plan.updates if m.current == "9.4x")
        assert alpha.citation.value == "11.8x"


class TestRefusals:
    def test_unrelated_source_is_not_used(self, tmp_path):
        path = tmp_path / "weather.csv"
        path.write_text("City,Rainfall\nMumbai,2200mm\n", encoding="utf-8")
        plan = plan_refresh(deck_with_table(), SourceSet([read_csv(path)]))
        assert plan.matches == []
        assert plan.unmatched, "an unrelated source must be reported, not silently ignored"

    def test_a_single_coincidental_label_is_not_enough(self, tmp_path):
        """One shared word must never drive a numeric update."""
        path = tmp_path / "thin.csv"
        path.write_text("Company,Unrelated\nSomething Else,1\n", encoding="utf-8")
        plan = plan_refresh(deck_with_table(), SourceSet([read_csv(path)]))
        assert plan.updates == []

    def test_missing_row_is_reported_not_guessed(self, tmp_path):
        path = tmp_path / "partial.csv"
        path.write_text(
            "Company,EV/EBITDA,Margin\nAlpha Corp,11.8x,22.1%\n", encoding="utf-8"
        )
        plan = plan_refresh(deck_with_table(), SourceSet([read_csv(path)]))
        labels = " ".join(label for _, label, _ in plan.unmatched)
        assert "Beta Industries" in labels
        assert all(m.citation.label != "" or m.col == 0 for m in plan.matches)

    def test_no_tables_means_no_matches(self, source):
        empty = DeckInfo(slide_width=W, slide_height=H,
                         slides=[SlideInfo(number=1, part_name="s1")])
        assert plan_refresh(empty, source).matches == []

    def test_a_malformed_grid_is_skipped_not_guessed(self, source):
        """A run count that does not fit the grid must not be reshaped."""
        broken = table_shape(DECK_TABLE)
        broken.runs = broken.runs[:-1]  # one cell short
        deck = DeckInfo(slide_width=W, slide_height=H,
                        slides=[SlideInfo(number=1, part_name="s1", shapes=[broken])])
        assert plan_refresh(deck, source).matches == []


class TestChangeSet:
    def test_produces_only_the_changed_cells(self, source):
        changeset = plan_refresh(deck_with_table(), source).to_changeset("d.pptx")
        assert len(changeset.changes) == 2
        assert all(c.op is Op.SET_TABLE_CELL for c in changeset.changes)

    def test_targets_address_the_right_cells(self, source):
        changeset = plan_refresh(deck_with_table(), source).to_changeset("d.pptx")
        assert {c.target for c in changeset.changes} == {"7/r1/c1", "7/r2/c2"}

    def test_every_change_records_its_source(self, source):
        changeset = plan_refresh(deck_with_table(), source).to_changeset("d.pptx")
        assert all(c.rationale.startswith("source: comps.csv!") for c in changeset.changes)

    def test_numbers_lock_blocks_a_refresh(self, source):
        """The lock must beat an automated refresh, not just a manual edit."""
        plan = plan_refresh(deck_with_table(), source)
        changeset = plan.to_changeset("d.pptx")
        changeset.locks.clear()
        protected = plan.to_changeset("d.pptx")
        protected.changes.clear()
        protected.lock("numbers", reason="audited")
        for match in plan.updates:
            from slide_wright.changeset import Change

            protected.add(Change(
                id=match.target, op=Op.SET_TABLE_CELL, slide=match.slide,
                target=match.target, before=match.current, after=match.citation.value,
            ))
        assert protected.approved == []
        assert len(protected.rejected) == 2


class TestReporting:
    def test_report_states_all_three_counts(self, source):
        text = plan_refresh(deck_with_table(), source).render()
        assert "2 figure(s) to update" in text
        assert "2 already correct" in text
        assert "0 not found" in text

    def test_report_shows_the_citation_for_each_update(self, source):
        text = plan_refresh(deck_with_table(), source).render()
        assert "comps.csv!B2" in text

    def test_report_lists_unmatched_labels(self, tmp_path):
        path = tmp_path / "partial.csv"
        path.write_text("Company,EV/EBITDA,Margin\nAlpha Corp,11.8x,22.1%\n",
                        encoding="utf-8")
        text = plan_refresh(deck_with_table(), SourceSet([read_csv(path)])).render()
        assert "Not found in the source" in text


class TestEndToEndOnARealPackage:
    def test_refresh_applies_and_verifies(self, adversarial_deck, tmp_path):
        from slide_wright.inspect import inspect
        from slide_wright.session import Session

        path = tmp_path / "comps.csv"
        path.write_text(
            "Company,EV/EBITDA,Margin,Growth\n"
            "Alpha Corp,11.8x,22.1%,18%\n"
            "Beta Industries,11.2x,19.8%,12%\n"
            "Gamma Holdings,8.7x,24.5%,21%\n",
            encoding="utf-8",
        )
        session = Session.open(adversarial_deck, workspace=tmp_path / "ws")
        plan = plan_refresh(session.deck(), SourceSet([read_csv(path)]))
        assert plan.updates, "the corpus deck's 9.4x should be refreshed to 11.8x"

        changeset = session.propose("refresh from workbook")
        for change in plan.to_changeset(str(session.current.path)).changes:
            changeset.add(change)
        changeset.approve_all()
        report = session.apply()

        assert report.deliverable
        assert "11.8x" in inspect(session.current.path).slide(3).text
        assert not report.fidelity.native_losses
