"""Update a deck's figures from a refreshed source, deterministically.

The recurring-deck workflow: last quarter's deck plus this quarter's numbers,
producing this quarter's deck. It is the most valuable thing this product can
do and the most dangerous, because a wrong number here is invisible — it looks
exactly like a right one.

So no model is involved. Values are matched by **label**, not by position and
not by resemblance:

  · a table cell is refreshed when its row label and column header both match a
    row and column in the source;
  · every proposed change carries the citation it came from;
  · anything ambiguous is reported as unmatched rather than guessed.

A figure this module cannot justify with a coordinate is a figure it will not
change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from slide_wright.changeset import Change, ChangeSet, Op, Origin
from slide_wright.inspect import DeckInfo, ShapeInfo
from slide_wright.sources import Citation, SourceSet, SourceTable, _normalise


# How a figure is written is a decision the deck's author made, and a refresh
# is about what the figure *is*. These separate the two.
#
# A leading currency symbol and a trailing unit are decoration: "$120" against a
# source of 125 should become "$125", not "125". A percent sign or a magnitude
# suffix is not decoration -- it says what scale the number is on, and a source
# cell that does not carry it cannot be placed on that scale. Excel stores a
# cell formatted as 12.3% as 0.123, so "refreshing" a deck's "12.3%" from it
# writes "0.123": a figure off by two orders of magnitude, cited to a real
# coordinate, and marked SOURCE so it needs no human review.
_DECORATED = re.compile(
    r"^(?P<prefix>[^\d\-+.]*)(?P<number>[-+]?[\d,\s]*\.?\d+)(?P<suffix>.*)$"
)
_SCALE_SUFFIX = re.compile(r"^\s*(%|k|m|bn?|tn?|x)\s*$", re.I)


@dataclass
class Shape:
    """A figure split into how it is written and what it says."""

    prefix: str
    number: float | None
    suffix: str
    raw: str

    @property
    def is_scaled(self) -> bool:
        """Whether the decoration changes what the number *means*, not just how
        it looks."""
        return bool(_SCALE_SUFFIX.match(self.suffix) or self.prefix.strip() == "%")

    @property
    def is_plain(self) -> bool:
        return not self.prefix.strip() and not self.suffix.strip()


def read_figure(text: str) -> Shape:
    match = _DECORATED.match(text.strip())
    if match is None:
        return Shape(prefix="", number=None, suffix="", raw=text.strip())
    try:
        number = float(re.sub(r"[\s,]", "", match.group("number")))
    except ValueError:
        number = None
    return Shape(
        prefix=match.group("prefix"),
        number=number,
        suffix=match.group("suffix"),
        raw=text.strip(),
    )


@dataclass
class Match:
    """One deck cell that a source row/column pair explains."""

    slide: int
    shape_id: str
    row: int
    col: int
    current: str
    citation: Citation

    @property
    def changed(self) -> bool:
        """Whether the figure moved -- not whether the text differs.

        Compared as numbers when both sides are numbers, so a source that writes
        120.00 where the deck writes 120 does not churn the file to say the same
        thing. Precision in a deck is a presentation choice; a refresh is not
        entitled to overrule one on the way past.
        """
        here, there = read_figure(self.current), read_figure(self.citation.value)
        if here.number is not None and there.number is not None and self.writable:
            return here.number != there.number
        return _normalise(self.current) != _normalise(self.citation.value)

    @property
    def writable(self) -> bool:
        """Whether this cell can be refreshed without changing what it says."""
        return not self.refusal

    @property
    def refusal(self) -> str:
        """Why this cell must not be written, in words a reviewer can act on."""
        here, there = read_figure(self.current), read_figure(self.citation.value)

        if not there.raw:
            return ("the source cell is empty; a blank is missing data, not a "
                    "value of nothing")
        if here.is_scaled and not there.is_scaled:
            return (f"the deck writes {here.raw!r} and the source has "
                    f"{there.raw!r}, which is not on that scale — a spreadsheet "
                    "stores 12.3% as 0.123, and writing it here would move the "
                    "figure by two orders of magnitude")
        if there.is_scaled and not here.is_scaled:
            return (f"the source writes {there.raw!r} and the deck cell is not "
                    "on that scale")
        return ""

    @property
    def replacement(self) -> str:
        """What to write, keeping how the deck writes it.

        "$120" refreshed from 125 becomes "$125". The author chose the symbol
        and the unit; only the figure came from the source.
        """
        here, there = read_figure(self.current), read_figure(self.citation.value)
        if here.is_plain or here.number is None or there.number is None:
            return self.citation.value
        number = re.sub(r"[\s,]", "", _DECORATED.match(there.raw).group("number"))
        return f"{here.prefix}{number}{here.suffix}"

    @property
    def target(self) -> str:
        return f"{self.shape_id}/r{self.row}/c{self.col}"


@dataclass
class RefreshPlan:
    """What a refresh would do, before anything is written."""

    matches: list[Match] = field(default_factory=list)
    unmatched: list[tuple[int, str, str]] = field(default_factory=list)  # slide, label, why

    @property
    def updates(self) -> list[Match]:
        return [m for m in self.matches if m.changed and m.writable]

    @property
    def refused(self) -> list[Match]:
        """Cells the source disagrees with but must not overwrite.

        Surfaced rather than dropped: "the source has a different number here
        and I will not write it" is exactly what a reviewer needs to know, and
        silently filtering it would make the refresh look complete when it is
        not.
        """
        return [m for m in self.matches if m.changed and not m.writable]

    @property
    def confirmed(self) -> list[Match]:
        """Cells the source agrees with. Evidence that the deck is still correct."""
        return [m for m in self.matches if not m.changed]

    def to_changeset(self, deck_path: str, instruction: str = "") -> ChangeSet:
        changeset = ChangeSet(deck=deck_path, instruction=instruction or "refresh figures from source")
        for i, match in enumerate(self.updates, start=1):
            changeset.add(Change(
                id=f"r{i}",
                op=Op.SET_TABLE_CELL,
                slide=match.slide,
                target=match.target,
                before=match.current,
                after=match.replacement,
                rationale=f"source: {match.citation.reference}",
                # Grounded in a coordinate, not a model's opinion — so this is
                # SOURCE origin and does not need human review to be trusted.
                origin=Origin.SOURCE,
                citation=match.citation.reference,
                object_kind="table",
            ))
        return changeset

    def render(self) -> str:
        lines = ["REFRESH PLAN", ""]
        lines.append(
            f"  {len(self.updates)} figure(s) to update · "
            f"{len(self.confirmed)} already correct · "
            f"{len(self.refused)} the source cannot safely replace · "
            f"{len(self.unmatched)} not found in the source"
        )
        if self.updates:
            lines += ["", "  Updates"]
            for m in self.updates:
                lines.append(
                    f"    · slide {m.slide} r{m.row}c{m.col}: "
                    f"{m.current!r} -> {m.replacement!r}"
                )
                lines.append(f"        from {m.citation.reference}")
        if self.confirmed:
            lines += ["", "  Confirmed unchanged"]
            for m in self.confirmed[:8]:
                lines.append(f"    · slide {m.slide}: {m.current!r} matches "
                             f"{m.citation.reference}")
            if len(self.confirmed) > 8:
                lines.append(f"    · … {len(self.confirmed) - 8} more")
        if self.refused:
            # Louder than "not found", and it should be: the source has a
            # different figure here and the engine is declining to write it.
            lines += ["", "  The source disagrees, and this will not be written"]
            for m in self.refused[:8]:
                lines.append(f"    · slide {m.slide} r{m.row}c{m.col}: {m.refusal}")
                lines.append(f"        source {m.citation.reference}")
            if len(self.refused) > 8:
                lines.append(f"    · … {len(self.refused) - 8} more")
        if self.unmatched:
            lines += ["", "  Not found in the source (left untouched)"]
            for slide, label, why in self.unmatched[:8]:
                lines.append(f"    · slide {slide}: {label!r} — {why}")
            if len(self.unmatched) > 8:
                lines.append(f"    · … {len(self.unmatched) - 8} more")
        return "\n".join(lines)


def plan_refresh(deck: DeckInfo, sources: SourceSet) -> RefreshPlan:
    """Match every deck table against the sources, by label."""
    plan = RefreshPlan()
    for slide in deck.slides:
        for shape in slide.shapes:
            if shape.kind != "table":
                continue
            _match_table(slide.number, shape, sources, plan)
    return plan


def _match_table(slide_no: int, shape: ShapeInfo, sources: SourceSet, plan: RefreshPlan) -> None:
    """Match one deck table against whichever source table shares its labels."""
    grid = _grid(shape)
    if not grid:
        # A table whose cells could not be reconstructed used to vanish from the
        # plan entirely -- no update, no refusal, no note. The user saw
        # "0 figures to update" and read it as the deck agreeing with the
        # source, when the engine had never looked at that table.
        #
        # Not a rare shape either. `ShapeInfo` flattens a table into runs, and a
        # cell with one bold word is two runs, so the count stops dividing on
        # the first table anyone has emphasised anything in.
        expected = (shape.table_rows or 0) * (shape.table_cols or 0)
        plan.unmatched.append((
            slide_no, shape.name or f"table {shape.id}",
            f"this table's cells could not be read: {len(shape.runs)} text run(s) "
            f"across a {shape.table_rows}x{shape.table_cols} grid, where "
            f"{expected} were expected. A cell split by formatting -- one bold "
            f"word -- does that. Nothing here was checked against the source"
        ))
        return
    if len(grid) < 2:
        plan.unmatched.append((
            slide_no, shape.name or f"table {shape.id}",
            "this table has a header and no rows under it, so there is nothing "
            "to look up"
        ))
        return
    header = grid[0]

    source = _best_source(grid, sources)
    if source is None:
        plan.unmatched.append(
            (slide_no, header[0] if header else "table",
             "no source table shares this table's labels")
        )
        return

    for r, row in enumerate(grid[1:], start=1):
        if not row:
            continue
        row_label = row[0]
        for c, current in enumerate(row[1:], start=1):
            if c >= len(header):
                continue
            citation, why = source.resolve(row_label, header[c])
            if citation is None:
                # The source's own words, not a generic one. "2 rows are
                # labelled 'EMEA' (A2, A3)" tells someone how to fix the
                # spreadsheet; "no cell has both labels" sends them looking for
                # a row that is demonstrably there.
                plan.unmatched.append(
                    (slide_no, f"{row_label} / {header[c]}",
                     why or "no cell in the source has both labels")
                )
                continue
            plan.matches.append(Match(
                slide=slide_no, shape_id=shape.id, row=r, col=c,
                current=current, citation=citation,
            ))


def _best_source(grid: list[list[str]], sources: SourceSet) -> SourceTable | None:
    """Pick the source table sharing the most labels with this deck table.

    Requires a real overlap — at least two shared labels — so a coincidental
    single match never drives an update.
    """
    deck_labels = {_normalise(c) for row in grid for c in row if c}
    best, best_score = None, 0
    for table in sources.tables:
        labels = {_normalise(c) for row in table.rows for c in row if c}
        score = len(deck_labels & labels)
        if score > best_score:
            best, best_score = table, score
    return best if best_score >= 2 else None


def _grid(shape: ShapeInfo) -> list[list[str]]:
    """Reconstruct a table's cell grid from its runs.

    `ShapeInfo` flattens a table's text into runs in reading order, so the grid
    is rebuilt from the known row and column counts. A shape whose run count
    does not divide evenly is skipped rather than guessed at.
    """
    if not shape.table_rows or not shape.table_cols:
        return []
    cells = [run.text.strip() for run in shape.runs]
    expected = shape.table_rows * shape.table_cols
    if len(cells) != expected:
        return []
    return [
        cells[r * shape.table_cols:(r + 1) * shape.table_cols]
        for r in range(shape.table_rows)
    ]
