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

from dataclasses import dataclass, field

from slide_wright.changeset import Change, ChangeSet, Op, Origin
from slide_wright.inspect import DeckInfo, ShapeInfo
from slide_wright.sources import Citation, SourceSet, SourceTable, _normalise


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
        return _normalise(self.current) != _normalise(self.citation.value)

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
        return [m for m in self.matches if m.changed]

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
                after=match.citation.value,
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
            f"{len(self.unmatched)} not found in the source"
        )
        if self.updates:
            lines += ["", "  Updates"]
            for m in self.updates:
                lines.append(
                    f"    · slide {m.slide} r{m.row}c{m.col}: "
                    f"{m.current!r} -> {m.citation.value!r}"
                )
                lines.append(f"        from {m.citation.reference}")
        if self.confirmed:
            lines += ["", "  Confirmed unchanged"]
            for m in self.confirmed[:8]:
                lines.append(f"    · slide {m.slide}: {m.current!r} matches "
                             f"{m.citation.reference}")
            if len(self.confirmed) > 8:
                lines.append(f"    · … {len(self.confirmed) - 8} more")
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
    if len(grid) < 2:
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
            citation = source.lookup(row_label, header[c])
            if citation is None:
                plan.unmatched.append(
                    (slide_no, f"{row_label} / {header[c]}",
                     "no cell in the source has both labels")
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
