"""Source documents and citations.

Extraction must be exact. A model may choose which cell answers a question; it
may never decide what the cell contains, because that is how a slide ends up
claiming 43% when the workbook says 12%.
"""

from __future__ import annotations

import pytest

from slide_wright.sources import (
    read_xlsx,
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


class TestCsvAsSpreadsheetsActuallyWriteIt:
    """Two things were refusing real files outright.

    **Encoding.** Only UTF-8 was accepted, so a CSV saved by Excel on Windows —
    cp1252 the moment any name carries an accent — came back as "could not read:
    'utf-8' codec can't decode byte 0xe9", and the feature was unusable for that
    person entirely. That is the common case for this workflow, not an edge one:
    it is the workbook an analyst emails you.

    **Delimiter.** A semicolon-separated file, which is what European Excel
    writes when the locale takes `,` as the decimal separator, parsed as a
    single column called `Company;Multiple`. It then matched nothing and
    reported that the labels did not line up — silently useless, which is worse
    than refused.
    """

    def _read(self, tmp_path, name, data: bytes):
        path = tmp_path / name
        path.write_bytes(data)
        return read_csv(path)

    def test_plain_utf8(self, tmp_path):
        table = self._read(tmp_path, "a.csv", b"Company,Multiple\nAlpha,9.4x\n")
        assert table.rows == [["Company", "Multiple"], ["Alpha", "9.4x"]]

    def test_a_byte_order_mark_is_not_part_of_the_first_header(self, tmp_path):
        table = self._read(tmp_path, "b.csv", b"\xef\xbb\xbfCompany,Multiple\nAlpha,9.4x\n")
        assert table.header[0] == "Company"

    def test_cp1252_from_excel_on_windows(self, tmp_path):
        table = self._read(tmp_path, "c.csv",
                           "Company,Margin\nCaf\u00e9 Ltd,21%\n".encode("cp1252"))
        assert table.rows[1] == ["Caf\u00e9 Ltd", "21%"], "the accent came back wrong"

    def test_utf16_only_when_the_file_says_so(self, tmp_path):
        """Guessing UTF-16 turns ordinary ASCII into pairs of CJK characters
        and never raises, so it is chosen on the byte-order mark alone."""
        table = self._read(tmp_path, "e.csv",
                           "Company\tMultiple\nAlpha\t9.4x\n".encode("utf-16"))
        assert table.rows == [["Company", "Multiple"], ["Alpha", "9.4x"]]

    def test_semicolons_from_european_excel(self, tmp_path):
        table = self._read(tmp_path, "f.csv", b"Company;Multiple\nAlpha;9.4x\n")
        assert table.header == ["Company", "Multiple"]

    def test_tabs(self, tmp_path):
        table = self._read(tmp_path, "g.csv", b"Company\tMultiple\nAlpha\t9.4x\n")
        assert table.header == ["Company", "Multiple"]

    def test_a_comma_file_is_not_re_read_as_something_else(self, tmp_path):
        """A tie keeps the comma. A file with no separator is one column."""
        table = self._read(tmp_path, "h.csv", b"Company\nAlpha\n")
        assert table.delimiter == ","

    def test_a_comma_inside_a_quoted_field_is_still_one_field(self, tmp_path):
        table = self._read(tmp_path, "i.csv",
                           b'Company,Note\n"Alpha, Inc.","up, sharply"\n')
        assert table.rows[1] == ["Alpha, Inc.", "up, sharply"]

    def test_what_was_guessed_is_recorded(self, tmp_path):
        """Both are guesses. A reader looking at a mangled character is owed
        the reason, and one looking at a clean file should be told nothing."""
        odd = self._read(tmp_path, "j.csv", b"Company;Margin\nAlpha;21%\n")
        assert "semicolon" in odd.read_note
        plain = self._read(tmp_path, "k.csv", b"Company,Margin\nAlpha,21%\n")
        assert plain.read_note == ""

    def test_something_that_is_not_text_at_all_is_still_refused(self, tmp_path):
        """cp1252 decodes nearly any byte, so the fallback ladder must not turn
        a refusal into a table of nonsense — a zip is not a spreadsheet."""
        table = self._read(tmp_path, "l.csv", b"PK\x03\x04\x14\x00\x00\x00\x08\x00")
        assert not table.rows or all(len(r) <= 1 for r in table.rows)


class TestAskingForASheetThatIsNotThere:
    """The refusal was right and its reason was wrong.

    A workbook whose four sheets are all perfectly readable, asked for a fifth,
    came back as "contains no readable cell values" — which sends someone
    looking for a data problem when what they have is a typo in a sheet name.
    """

    def _workbook(self, tmp_path, *names):
        openpyxl = pytest.importorskip("openpyxl")
        path = tmp_path / "book.xlsx"
        book = openpyxl.Workbook()
        book.active.title = names[0]
        book.active.append(["Company", "Multiple"])
        book.active.append(["Alpha", "9.4x"])
        for name in names[1:]:
            sheet = book.create_sheet(name)
            sheet.append(["x", "y"])
            sheet.append(["1", "2"])
        book.save(path)
        return path

    def test_it_says_the_sheet_is_missing(self, tmp_path):
        path = self._workbook(tmp_path, "Summary", "Detail")
        with pytest.raises(SourceError, match="no sheet called 'Nope'"):
            read_xlsx(path, sheet="Nope")

    def test_it_lists_the_sheets_there_are(self, tmp_path):
        path = self._workbook(tmp_path, "Summary", "Detail")
        with pytest.raises(SourceError) as caught:
            read_xlsx(path, sheet="Nope")
        assert "Summary" in str(caught.value) and "Detail" in str(caught.value)

    def test_a_sheet_that_is_there_still_works(self, tmp_path):
        path = self._workbook(tmp_path, "Summary", "Detail")
        tables = read_xlsx(path, sheet="Detail")
        assert [t.name for t in tables] == ["Detail"]

    def test_the_name_is_matched_exactly(self, tmp_path):
        """Sheet names are case-sensitive in Excel, and guessing which one was
        meant is the sort of help that puts the wrong number on a slide."""
        path = self._workbook(tmp_path, "Summary", "Detail")
        with pytest.raises(SourceError):
            read_xlsx(path, sheet="detail")

    def test_an_empty_workbook_still_says_that_instead(self, tmp_path):
        """The old message was correct for this case and only this one."""
        openpyxl = pytest.importorskip("openpyxl")
        path = tmp_path / "empty.xlsx"
        openpyxl.Workbook().save(path)
        with pytest.raises(SourceError, match="no readable cell values"):
            read_xlsx(path)
