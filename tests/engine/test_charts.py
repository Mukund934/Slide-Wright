"""Native charts: read them, refuse to edit them, prove they survived.

A chart keeps its numbers twice — the cached series that is drawn, and the
embedded workbook that "Edit Data" opens. The failure this module exists to
prevent is a deck where those two disagree, because it looks perfectly correct
until someone opens the data in front of an audience.

Tests marked `fixtures` need the third-party corpus:
    python scripts/fetch_fixtures.py
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest

from slide_wright.apply import ApplyError, apply_changes
from slide_wright.changeset import Change, ChangeSet, Op
from slide_wright.charts import (
    ChartUnsupported,
    assert_preserved,
    census,
    find_all,
    guard_edit,
    values,
)
from slide_wright.inspect import inspect
from slide_wright.package import Package

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "third-party"


def fixture(name: str) -> Path:
    path = FIXTURES / name
    if not path.is_file():
        pytest.skip(f"{name} not present; run scripts/fetch_fixtures.py")
    return path


@pytest.fixture
def eia():
    """A real 350-part government deck: 29 charts, 29 linked workbooks."""
    return fixture("eia-aeo2023-release.pptx")


@pytest.fixture
def many_charts():
    return fixture("pypptx-chart-types.pptx")


def approved(deck, *changes: Change) -> ChangeSet:
    cs = ChangeSet(deck=str(deck))
    for c in changes:
        cs.add(c)
    cs.approve_all()
    return cs


class TestReading:
    def test_finds_a_chart_and_its_workbook(self, adversarial_deck):
        charts = find_all(adversarial_deck)
        assert len(charts) == 1
        chart = charts[0]
        assert chart.slide == 2
        assert chart.series_count == 2
        assert chart.has_data_link
        assert chart.workbook.endswith(".xlsx")

    def test_reads_the_cached_values(self, adversarial_deck):
        chart = find_all(adversarial_deck)[0]
        cached = values(adversarial_deck, chart.part)
        assert "Q1" in cached and "3.1" in cached

    def test_census_counts_what_must_not_be_lost(self, adversarial_deck):
        c = census(adversarial_deck)
        assert c["charts"] == 1
        assert c["workbooks"] == 1
        assert c["series"] == 2
        assert c["values"] > 0
        assert not c["malformed"]

    def test_a_deck_with_no_chart_censuses_to_zero(self, minimal_deck):
        assert census(minimal_deck)["charts"] == 0
        assert find_all(minimal_deck) == []


class TestRefusal:
    """Editing chart data must fail closed, and say something true."""

    def test_targeting_a_chart_is_refused(self, adversarial_deck):
        chart = find_all(adversarial_deck)[0]
        with pytest.raises(ChartUnsupported, match="native chart"):
            guard_edit(adversarial_deck, chart.slide, chart.shape_id)

    def test_the_refusal_names_the_workbook(self, adversarial_deck):
        chart = find_all(adversarial_deck)[0]
        with pytest.raises(ChartUnsupported, match="xlsx"):
            guard_edit(adversarial_deck, chart.slide, chart.shape_id)

    def test_other_shapes_on_the_same_slide_are_untouched(self, adversarial_deck):
        chart = find_all(adversarial_deck)[0]
        guard_edit(adversarial_deck, chart.slide, "99999")  # must not raise

    def test_the_applier_refuses_a_chart_edit_before_writing(self, adversarial_deck, tmp_path):
        chart = find_all(adversarial_deck)[0]
        out = tmp_path / "out.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TEXT, slide=chart.slide, target=chart.shape_id,
            before="Q4", after="Q1"))
        with pytest.raises(ApplyError, match="native chart"):
            apply_changes(adversarial_deck, cs, out)
        assert not out.exists(), "nothing may be written when a change is refused"

    def test_the_message_does_not_claim_the_value_is_absent(self, adversarial_deck, tmp_path):
        """The old refusal said the shape did not contain the text.

        It does contain it — in the chart part rather than the slide — so that
        message sent a reviewer looking for something demonstrably there.
        """
        chart = find_all(adversarial_deck)[0]
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TEXT, slide=chart.slide, target=chart.shape_id,
            before="Q4", after="Q1"))
        with pytest.raises(ApplyError) as exc:
            apply_changes(adversarial_deck, cs, tmp_path / "out.pptx")
        assert "does not contain" not in str(exc.value)


class TestPreservation:
    """The half of the question that is answerable without editing charts."""

    def test_editing_elsewhere_leaves_the_chart_byte_identical(
        self, adversarial_deck, tmp_path
    ):
        table = next(s for s in inspect(adversarial_deck).all_shapes() if s.kind == "table")
        out = tmp_path / "out.pptx"
        apply_changes(adversarial_deck, approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table.id}/r1/c1", before="9.4x", after="11.8x")), out)

        before, after = Package.open(adversarial_deck), Package.open(out)
        chart = find_all(adversarial_deck)[0]
        assert after.read(chart.part) == before.read(chart.part)
        assert after.read(chart.workbook) == before.read(chart.workbook)

    def test_the_data_link_itself_survives(self, adversarial_deck, tmp_path):
        """A chart that keeps its picture but loses its workbook looks fine."""
        table = next(s for s in inspect(adversarial_deck).all_shapes() if s.kind == "table")
        out = tmp_path / "out.pptx"
        apply_changes(adversarial_deck, approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table.id}/r1/c1", before="9.4x", after="11.8x")), out)

        assert find_all(out)[0].has_data_link

    def test_assert_preserved_accepts_an_untouched_copy(self, adversarial_deck, tmp_path):
        copy = tmp_path / "copy.pptx"
        shutil.copy(adversarial_deck, copy)
        assert_preserved(adversarial_deck, copy)  # must not raise

    def test_assert_preserved_catches_a_stripped_workbook(self, adversarial_deck, tmp_path):
        """The silent loss: picture intact, "Edit Data" broken."""
        stripped = tmp_path / "stripped.pptx"
        with zipfile.ZipFile(adversarial_deck) as zin, \
             zipfile.ZipFile(stripped, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                if "embeddings" in info.filename:
                    continue
                zout.writestr(info, zin.read(info.filename))

        with pytest.raises(ChartUnsupported, match="workbooks"):
            assert_preserved(adversarial_deck, stripped)


class TestDamageIsReportedNotRaised:
    def test_a_corrupt_chart_part_is_reported_as_malformed(self, adversarial_deck, tmp_path):
        broken = tmp_path / "broken.pptx"
        with zipfile.ZipFile(adversarial_deck) as zin, \
             zipfile.ZipFile(broken, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename.startswith("ppt/charts/chart"):
                    data = b"<c:chartSpace>truncated"
                zout.writestr(info, data)

        charts = find_all(broken)
        assert charts and charts[0].malformed, "damage must be reported, not raised"
        assert census(broken)["malformed"]


@pytest.mark.fixtures
class TestRealDecks:
    """The case the kill-signal question was actually about."""

    def test_a_real_deck_with_29_linked_charts_is_read_completely(self, eia):
        c = census(eia)
        assert c["charts"] == 29
        assert c["workbooks"] == 29, "every chart must keep its data link"
        assert c["values"] > 20_000

    def test_editing_text_preserves_all_29_charts_and_links(self, eia, tmp_path):
        deck = inspect(eia)
        target = next(s for s in deck.all_shapes()
                      if s.has_text and len(s.text.strip()) > 4)
        slide = next(sl.number for sl in deck.slides
                     if any(s.id == target.id for s in sl.shapes))

        out = tmp_path / "out.pptx"
        apply_changes(eia, approved(eia, Change(
            id="c1", op=Op.SET_TEXT, slide=slide, target=target.id,
            before=target.text, after=target.text + " (rev)")), out)

        before, after = Package.open(eia), Package.open(out)
        for chart in find_all(eia):
            assert after.read(chart.part) == before.read(chart.part)
            assert after.read(chart.workbook) == before.read(chart.workbook)
        assert census(out)["workbooks"] == 29

    def test_many_charts_on_one_slide_are_all_found(self, many_charts):
        assert census(many_charts)["charts"] == 31
