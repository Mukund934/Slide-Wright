"""Source documents and citations.

Extraction must be exact. A model may choose which cell answers a question; it
may never decide what the cell contains, because that is how a slide ends up
claiming 43% when the workbook says 12%.
"""

from __future__ import annotations

import pytest

from slide_wright.sources import (
    Citation,
    SourceError,
    SourceSet,
    SourceTable,
    _a1,
    load,
    read_csv,
)

COMPS = (
    "Company,EV/EBITDA,Margin,Growth\n"
    "Alpha Corp,11.8x,22.1%,18%\n"
    "Beta Industries,11.2x,19.8%,12%\n"
    "Gamma Holdings,8.7x,24.5%,21%\n"
)


@pytest.fixture
def comps(tmp_path):
    path = tmp_path / "comps.csv"
    path.write_text(COMPS, encoding="utf-8")
    return read_csv(path)


class TestReading:
    def test_reads_rows_and_header(self, comps):
        assert comps.header == ["Company", "EV/EBITDA", "Margin", "Growth"]
        assert len(comps.rows) == 4
        assert len(comps.body) == 3

    def test_skips_blank_lines(self, tmp_path):
        path = tmp_path / "gappy.csv"
        path.write_text("A,B\n\n1,2\n\n", encoding="utf-8")
        assert len(read_csv(path).rows) == 2

    def test_strips_whitespace(self, tmp_path):
        path = tmp_path / "spacey.csv"
        path.write_text("A , B\n 1 , 2 \n", encoding="utf-8")
        assert read_csv(path).rows[1] == ["1", "2"]

    def test_handles_a_utf8_bom(self, tmp_path):
        path = tmp_path / "bom.csv"
        path.write_bytes(b"\xef\xbb\xbfCompany,Value\nAlpha,1\n")
        assert read_csv(path).header[0] == "Company"

    def test_missing_file_is_refused(self, tmp_path):
        with pytest.raises(SourceError, match="not a file"):
            read_csv(tmp_path / "nope.csv")

    def test_unsupported_type_is_refused(self, tmp_path):
        path = tmp_path / "notes.txt"
        path.write_text("hello")
        with pytest.raises(SourceError, match="unsupported source type"):
            load(path)


class TestCitations:
    def test_cell_cites_value_locator_and_label(self, comps):
        citation = comps.cell(1, 1)
        assert citation.value == "11.8x"
        assert citation.label == "EV/EBITDA"
        assert citation.locator.endswith("B2")

    def test_reference_does_not_repeat_the_filename(self, comps):
        """A CSV's sheet name is its own stem; citing it twice is noise."""
        assert comps.cell(1, 1).reference == "comps.csv!B2"

    def test_render_is_readable(self, comps):
        assert comps.cell(1, 1).render() == "11.8x (EV/EBITDA) — comps.csv!B2"

    def test_header_cells_have_no_label(self, comps):
        assert comps.cell(0, 0).label == ""

    def test_out_of_range_returns_none(self, comps):
        assert comps.cell(99, 0) is None
        assert comps.cell(0, 99) is None
        assert comps.cell(-1, 0) is None


class TestLookup:
    def test_finds_a_value_by_row_and_column_label(self, comps):
        citation = comps.lookup("Alpha Corp", "EV/EBITDA")
        assert citation is not None
        assert citation.value == "11.8x"

    def test_lookup_ignores_case_and_spacing(self, comps):
        assert comps.lookup("alpha corp", "ev/ebitda").value == "11.8x"

    def test_unknown_row_returns_none(self, comps):
        assert comps.lookup("Nonexistent Ltd", "Margin") is None

    def test_unknown_column_returns_none(self, comps):
        assert comps.lookup("Alpha Corp", "Nonexistent") is None


class TestFind:
    def test_finds_every_occurrence(self, comps):
        assert len(comps.find("11.8x")) == 1

    def test_matching_ignores_commas_and_case(self, tmp_path):
        path = tmp_path / "big.csv"
        path.write_text("Metric,Value\nRevenue,\"1,250,000\"\n", encoding="utf-8")
        assert read_csv(path).find("1250000")

    def test_no_match_returns_empty(self, comps):
        assert comps.find("does not appear") == []


class TestSourceSet:
    def test_searches_across_tables(self, comps, tmp_path):
        other = tmp_path / "other.csv"
        other.write_text("Metric,Value\nARR,11.8x\n", encoding="utf-8")
        sources = SourceSet([comps, read_csv(other)])
        assert len(sources.find("11.8x")) == 2

    def test_summary_is_compact_and_contains_values(self, comps):
        text = SourceSet([comps]).summarise()
        assert "comps" in text and "Alpha Corp" in text

    def test_summary_truncates_long_tables(self, tmp_path):
        path = tmp_path / "long.csv"
        path.write_text("A\n" + "\n".join(str(i) for i in range(50)), encoding="utf-8")
        assert "more rows" in SourceSet([read_csv(path)]).summarise(max_rows=5)


class TestA1Notation:
    @pytest.mark.parametrize(
        "row,col,expected",
        [(0, 0, "A1"), (0, 1, "B1"), (1, 0, "A2"), (0, 25, "Z1"),
         (0, 26, "AA1"), (0, 27, "AB1"), (9, 51, "AZ10")],
    )
    def test_matches_spreadsheet_convention(self, row, col, expected):
        assert _a1(row, col) == expected


class TestXlsx:
    def test_reads_a_real_workbook(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        path = tmp_path / "book.xlsx"
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = "Comps"
        for row in [["Company", "Multiple"], ["Alpha Corp", "11.8x"]]:
            sheet.append(row)
        book.save(str(path))

        tables = load(path)
        assert len(tables) == 1
        assert tables[0].name == "Comps"
        citation = tables[0].lookup("Alpha Corp", "Multiple")
        assert citation.value == "11.8x"
        assert citation.reference == "book.xlsx!Comps!B2"

    def test_empty_workbook_is_reported_not_silently_empty(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        path = tmp_path / "blank.xlsx"
        openpyxl.Workbook().save(str(path))
        with pytest.raises(SourceError, match="no readable cell values"):
            load(path)


class TestCitationValue:
    def test_citation_is_immutable(self):
        citation = Citation(document="a.csv", locator="A1", value="1")
        with pytest.raises(Exception):
            citation.value = "2"  # type: ignore[misc]
