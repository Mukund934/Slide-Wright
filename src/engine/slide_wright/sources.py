"""Source documents, and the citations that tie a slide figure back to a cell.

The expensive failure in this category is a number on a slide that nobody can
trace three weeks later. A documented Copilot example put "43%" on a banking
slide when the real figure was 12%, and nothing in the deck could have caught it.

So a figure that comes from a spreadsheet keeps a `Citation` naming the file,
the sheet and the cell. That makes two things possible that are otherwise not:

  · updating a deck from a refreshed workbook without a model inventing values;
  · answering "where did this come from?" with a coordinate rather than a guess.

Extraction here is deterministic. A model may decide *which* cell answers a
question; it may never decide what the cell contains.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path


class SourceError(Exception):
    """The source could not be read. Never guess at its contents."""


@dataclass(frozen=True)
class Citation:
    """Where a value came from, precisely enough to check."""

    document: str
    locator: str          # "Comps!D14", "row 3, column 2", "page 4"
    value: str
    label: str = ""       # the nearest header or row label, when there is one

    def render(self) -> str:
        label = f" ({self.label})" if self.label else ""
        return f"{self.value}{label} — {self.reference}"

    @property
    def reference(self) -> str:
        """`file!Sheet!A1`, collapsing the sheet when it just repeats the filename.

        A CSV's "sheet" is its own stem, so citing `comps.csv!comps!B2` is noise.
        """
        name = Path(self.document).name
        locator = self.locator
        stem = Path(self.document).stem
        if locator.startswith(f"{stem}!"):
            locator = locator[len(stem) + 1:]
        return f"{name}!{locator}"


@dataclass
class SourceTable:
    """A rectangular block of cells with a name and an origin."""

    document: str
    name: str
    rows: list[list[str]] = field(default_factory=list)

    @property
    def header(self) -> list[str]:
        return self.rows[0] if self.rows else []

    @property
    def body(self) -> list[list[str]]:
        return self.rows[1:] if len(self.rows) > 1 else []

    def cell(self, row: int, col: int) -> Citation | None:
        """Cite one cell. Rows and columns are zero-indexed over the whole block."""
        if not (0 <= row < len(self.rows)):
            return None
        line = self.rows[row]
        if not (0 <= col < len(line)):
            return None
        label = ""
        if row > 0 and col < len(self.header):
            label = self.header[col]
        return Citation(
            document=self.document,
            locator=f"{self.name}!{_a1(row, col)}",
            value=line[col],
            label=label,
        )

    def find(self, needle: str) -> list[Citation]:
        """Every cell whose text equals `needle`, with its citation.

        Used to answer "this figure on the slide — is it still what the
        workbook says?" without a model reading the spreadsheet.
        """
        wanted = _normalise(needle)
        hits = []
        for r, line in enumerate(self.rows):
            for c, value in enumerate(line):
                if _normalise(value) == wanted:
                    citation = self.cell(r, c)
                    if citation:
                        hits.append(citation)
        return hits

    def lookup(self, row_label: str, column_label: str) -> Citation | None:
        """Find a value by its row and column labels, the way a person would."""
        return self.resolve(row_label, column_label)[0]

    def resolve(self, row_label: str, column_label: str) -> tuple[Citation | None, str]:
        """The same lookup, and why it failed when it did.

        Ambiguity is refused rather than resolved. Two rows labelled "EMEA" --
        a restated figure beside the original, a subtotal beside its parts --
        used to return the first one silently, with a citation to a coordinate
        that reads as authoritative because it is one. The value was simply the
        wrong cell.

        That is the failure this whole module exists to prevent, and it was
        worse than the case it was written against: the documented Copilot
        example put 43% on a banking slide where the truth was 12%, and nothing
        could trace it. Here it would have been traceable to `B2` -- and still
        wrong, and now believed.

        A refresh whose source is unambiguous is worth having. One that guesses
        is worth less than nothing, because it is trusted.
        """
        want_col = _normalise(column_label)
        columns = [i for i, h in enumerate(self.header) if _normalise(h) == want_col]
        if not columns:
            return None, f"no column is labelled {column_label!r}"
        if len(columns) > 1:
            where = ", ".join(_a1(0, i) for i in columns)
            return None, (
                f"{len(columns)} columns are labelled {column_label!r} ({where}); "
                "the source does not say which one the deck means"
            )

        want_row = _normalise(row_label)
        rows = [
            r for r, line in enumerate(self.rows[1:], start=1)
            if line and _normalise(line[0]) == want_row
        ]
        if not rows:
            return None, f"no row is labelled {row_label!r}"
        if len(rows) > 1:
            where = ", ".join(_a1(r, 0) for r in rows)
            return None, (
                f"{len(rows)} rows are labelled {row_label!r} ({where}); "
                "the source does not say which one the deck means"
            )
        return self.cell(rows[0], columns[0]), ""


@dataclass
class SourceSet:
    """Every source document attached to a job."""

    tables: list[SourceTable] = field(default_factory=list)

    def add(self, table: SourceTable) -> SourceTable:
        self.tables.append(table)
        return table

    def find(self, needle: str) -> list[Citation]:
        return [c for t in self.tables for c in t.find(needle)]

    def summarise(self, max_rows: int = 8) -> str:
        """A compact view for a model. Values only — never formulas or macros."""
        lines = []
        for table in self.tables:
            lines.append(f"table {table.name} ({len(table.rows)} rows) "
                         f"from {Path(table.document).name}")
            for r, row in enumerate(table.rows[:max_rows]):
                lines.append("  " + " | ".join(cell[:24] for cell in row))
            if len(table.rows) > max_rows:
                lines.append(f"  … {len(table.rows) - max_rows} more rows")
        return "\n".join(lines)


# ── readers ──────────────────────────────────────────────────────────────────

def read_csv(path: str | Path, name: str = "") -> SourceTable:
    path = Path(path)
    if not path.is_file():
        raise SourceError(f"not a file: {path}")
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = [[cell.strip() for cell in row] for row in csv.reader(handle)]
    except (OSError, UnicodeDecodeError) as exc:
        raise SourceError(f"could not read {path.name}: {exc}") from exc
    return SourceTable(document=str(path), name=name or path.stem,
                       rows=[r for r in rows if any(r)])


def read_xlsx(path: str | Path, sheet: str | None = None) -> list[SourceTable]:
    """Read a workbook's cell *values*.

    Formulas are deliberately not evaluated — `data_only=True` returns the last
    value Excel cached. A workbook never opened in Excel yields empty cells, and
    that is reported rather than silently treated as zero.
    """
    path = Path(path)
    if not path.is_file():
        raise SourceError(f"not a file: {path}")
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise SourceError(
            "reading .xlsx needs openpyxl; `pip install openpyxl`"
        ) from exc

    try:
        book = load_workbook(filename=str(path), data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many types
        raise SourceError(f"could not read {path.name}: {exc}") from exc

    tables = []
    for worksheet in book.worksheets:
        if sheet and worksheet.title != sheet:
            continue
        rows = [
            ["" if cell is None else str(cell).strip() for cell in row]
            for row in worksheet.iter_rows(values_only=True)
        ]
        rows = [r for r in rows if any(r)]
        if rows:
            tables.append(SourceTable(document=str(path), name=worksheet.title, rows=rows))
    book.close()

    if not tables:
        raise SourceError(f"{path.name} contains no readable cell values "
                          "(a workbook of unevaluated formulas reads as empty)")
    return tables


def load(path: str | Path) -> list[SourceTable]:
    """Read any supported source document."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return [read_csv(path)]
    if suffix in {".xlsx", ".xlsm"}:
        return read_xlsx(path)
    raise SourceError(f"unsupported source type {suffix!r}; use .csv or .xlsx")


# ── helpers ──────────────────────────────────────────────────────────────────

def _a1(row: int, col: int) -> str:
    """Zero-indexed (row, col) to an A1 reference, as a spreadsheet user reads it."""
    letters = ""
    n = col
    while True:
        letters = chr(ord("A") + n % 26) + letters
        n = n // 26 - 1
        if n < 0:
            break
    return f"{letters}{row + 1}"


def _normalise(text: str) -> str:
    """Compare cell text the way a person would: ignoring case, spaces and commas."""
    return re.sub(r"[\s,]", "", str(text)).strip().lower()
