"""What actually changed between two decks, in the terms a reviewer thinks in.

`fidelity.py` answers a different question: *which parts of the package differ*.
That is the right question for a guarantee — it is complete, it cannot be
argued with, and it is what makes "nothing else changed" checkable. It is the
wrong question for a person, because `ppt/slides/slide4.xml changed` tells them
something happened and nothing about what.

That gap matters most at the worst moment. When an edit produces a change
nobody requested, the deck is blocked and the reviewer is handed a part name.
They then have to unzip a file and read XML to find out whether the tool moved
a logo by a millimetre or rewrote a number in front of an investment committee.

So this module compares the two decks *structurally* — shapes matched by id,
then text, geometry, formatting and table shape compared — and describes the
difference. It is deliberately built on `inspect()` rather than on raw XML: the
same structural model the rest of the system reasons about, so a difference the
diff can see is a difference the applier could have made.

What this is not: a rendering comparison. Two decks can be structurally
identical and look different (a missing font substitutes silently), and the
honest way to catch that is to render both — expensive, flaky, and deferred on
purpose. Everything here is an assertion about structure, and says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from slide_wright.inspect import EMU_PER_INCH, DeckInfo, ShapeInfo, inspect

# Geometry is stored in EMUs, and rounding through a save can shift a value by
# a few of them without moving anything a human could see. A 91440-EMU tenth of
# an inch is far above that noise floor and far below a meaningful nudge, so
# smaller moves are reported as sub-threshold rather than silently dropped.
VISIBLE_MOVE_EMU = 9144  # 0.01in


@dataclass
class ShapeDelta:
    """One difference on one shape."""

    slide: int
    shape_id: str
    kind: str            # text | geometry | size | formatting | table | added | removed
    description: str
    before: object = None
    after: object = None

    @property
    def is_content(self) -> bool:
        """Content changes alter what the deck says. Everything else is presentation."""
        return self.kind in {"text", "table", "added", "removed"}

    @property
    def changes_figures(self) -> bool:
        """Whether a number moved.

        The sharpest claim this product can make is not "content unchanged" but
        *"no figure changed"*. Someone asking for a formatting pass on a
        pitchbook does not want reassurance about prose; they want to know the
        multiples are the ones they signed off.

        Digits are compared rather than parsed. A parser would need to decide
        what counts as a quantity -- is `Q3` a figure? is `2026`? -- and every
        such decision is a way to answer "no figures changed" wrongly. Comparing
        the digit runs either side errs toward flagging, which is the safe
        direction here: this claim is a negative being proved, so a false alarm
        costs a second look and a missed one costs the guarantee.
        """
        if not self.is_content:
            return False
        return _digits(self.before) != _digits(self.after)


@dataclass
class DeckDiff:
    """Every structural difference between two decks."""

    source: str
    output: str
    deltas: list[ShapeDelta] = field(default_factory=list)
    slides_added: list[int] = field(default_factory=list)
    slides_removed: list[int] = field(default_factory=list)

    @property
    def figure_deltas(self) -> list[ShapeDelta]:
        """Differences where a number moved. The count nobody wants to be non-zero."""
        return [d for d in self.deltas if d.changes_figures]

    @property
    def changed(self) -> bool:
        return bool(self.deltas or self.slides_added or self.slides_removed)

    @property
    def content_deltas(self) -> list[ShapeDelta]:
        return [d for d in self.deltas if d.is_content]

    def for_slide(self, number: int) -> list[ShapeDelta]:
        return [d for d in self.deltas if d.slide == number]

    def render(self, limit: int = 40) -> str:
        if not self.changed:
            return "No structural differences."

        lines = ["STRUCTURAL DIFF", ""]
        if self.slides_removed:
            lines.append(f"  slides removed: {_join(self.slides_removed)}")
        if self.slides_added:
            lines.append(f"  slides added:   {_join(self.slides_added)}")

        by_slide: dict[int, list[ShapeDelta]] = {}
        for delta in self.deltas:
            by_slide.setdefault(delta.slide, []).append(delta)

        shown = 0
        for number in sorted(by_slide):
            lines += ["", f"  slide {number}"]
            for delta in by_slide[number]:
                if shown >= limit:
                    remaining = len(self.deltas) - shown
                    lines.append(f"    … {remaining} more difference(s)")
                    return "\n".join(lines)
                lines.append(f"    · {delta.description}")
                shown += 1

        content = len(self.content_deltas)
        lines += [
            "",
            f"  {len(self.deltas)} difference(s) — {content} change what the deck says, "
            f"{len(self.deltas) - content} change how it looks",
        ]
        return "\n".join(lines)


def diff(source, output) -> DeckDiff:
    """Compare two decks structurally.

    Accepts paths or already-inspected decks, so a caller that has inspected a
    deck for another reason does not pay to parse it twice.
    """
    a = source if isinstance(source, DeckInfo) else inspect(source)
    b = output if isinstance(output, DeckInfo) else inspect(output)
    result = DeckDiff(source=str(getattr(a, "path", source)),
                      output=str(getattr(b, "path", output)))

    before = {s.number: s for s in a.slides}
    after = {s.number: s for s in b.slides}
    result.slides_removed = sorted(set(before) - set(after))
    result.slides_added = sorted(set(after) - set(before))

    for number in sorted(set(before) & set(after)):
        _diff_slide(number, before[number], after[number], result)
    return result


def _diff_slide(number: int, before, after, result: DeckDiff) -> None:
    old = {s.id: s for s in before.shapes}
    new = {s.id: s for s in after.shapes}

    for shape_id in sorted(set(old) - set(new)):
        shape = old[shape_id]
        result.deltas.append(ShapeDelta(
            slide=number, shape_id=shape_id, kind="removed",
            description=f"{_name(shape)} removed",
            before=shape.text or None,
        ))
    for shape_id in sorted(set(new) - set(old)):
        shape = new[shape_id]
        result.deltas.append(ShapeDelta(
            slide=number, shape_id=shape_id, kind="added",
            description=f"{_name(shape)} added",
            after=shape.text or None,
        ))
    for shape_id in sorted(set(old) & set(new)):
        _diff_shape(number, old[shape_id], new[shape_id], result)


def _diff_shape(number: int, old: ShapeInfo, new: ShapeInfo, result: DeckDiff) -> None:
    def add(kind: str, description: str, b=None, a=None) -> None:
        result.deltas.append(ShapeDelta(
            slide=number, shape_id=old.id, kind=kind,
            description=description, before=b, after=a,
        ))

    if old.text != new.text:
        add("text", f"{_name(old)} text {_describe_text_change(old.text, new.text)}",
            old.text, new.text)

    if (old.table_rows, old.table_cols) != (new.table_rows, new.table_cols):
        add("table",
            f"{_name(old)} table {old.table_rows}x{old.table_cols} -> "
            f"{new.table_rows}x{new.table_cols}",
            (old.table_rows, old.table_cols), (new.table_rows, new.table_cols))

    _diff_geometry(old, new, add)
    _diff_formatting(old, new, add)


def _diff_geometry(old: ShapeInfo, new: ShapeInfo, add) -> None:
    if None not in (old.x, old.y, new.x, new.y) and (old.x, old.y) != (new.x, new.y):
        dx, dy = new.x - old.x, new.y - old.y
        if max(abs(dx), abs(dy)) >= VISIBLE_MOVE_EMU:
            add("geometry",
                f"{_name(old)} moved {_inches(dx)} right, {_inches(dy)} down",
                (old.x, old.y), (new.x, new.y))
        else:
            add("geometry",
                f"{_name(old)} moved by less than 0.01in (probably rounding)",
                (old.x, old.y), (new.x, new.y))

    if None not in (old.cx, old.cy, new.cx, new.cy) and (old.cx, old.cy) != (new.cx, new.cy):
        add("size",
            f"{_name(old)} resized {_size(old.cx, old.cy)} -> {_size(new.cx, new.cy)}",
            (old.cx, old.cy), (new.cx, new.cy))

    if old.rotation_deg != new.rotation_deg:
        add("geometry",
            f"{_name(old)} rotation {old.rotation_deg or 0:g}° -> {new.rotation_deg or 0:g}°",
            old.rotation_deg, new.rotation_deg)


def _diff_formatting(old: ShapeInfo, new: ShapeInfo, add) -> None:
    """Compare run formatting, but only where the text itself is unchanged.

    When the text changed the runs have already been re-described by the text
    delta, and reporting every formatting field of a rewritten run as its own
    difference buries the one line the reviewer needs.
    """
    if old.text != new.text or len(old.runs) != len(new.runs):
        return
    for i, (a, b) in enumerate(zip(old.runs, new.runs)):
        for attr, label in (("size_pt", "size"), ("bold", "bold"),
                            ("italic", "italic"), ("font", "font"),
                            ("color", "colour")):
            av, bv = getattr(a, attr), getattr(b, attr)
            if av != bv:
                add("formatting",
                    f"{_name(old)} run {i + 1} {label} {av!r} -> {bv!r}", av, bv)


def _describe_text_change(before: str, after: str, context: int = 14) -> str:
    """Describe *where* two strings differ, not merely that they do.

    Truncating both sides from the front is worse than useless on a long run:
    a table whose only change is one cell renders as two identical-looking
    ellipses. So the common prefix and suffix are trimmed away and only the
    differing region is shown, with a little context so it can be located.
    """
    a, b = before.replace("\n", " "), after.replace("\n", " ")

    head = 0
    while head < len(a) and head < len(b) and a[head] == b[head]:
        head += 1
    tail = 0
    while (tail < len(a) - head and tail < len(b) - head
           and a[len(a) - 1 - tail] == b[len(b) - 1 - tail]):
        tail += 1

    lead = a[max(0, head - context):head]
    trail = a[len(a) - tail:len(a) - tail + context]
    prefix = "…" if head - context > 0 else ""
    suffix = "…" if tail - context > 0 else ""

    changed_before = a[head:len(a) - tail]
    changed_after = b[head:len(b) - tail]

    where = f"{prefix}{lead}[" if (lead or prefix) else "["
    return (f"{where}{changed_before or '(nothing)'} -> "
            f"{changed_after or '(nothing)'}]{trail}{suffix}")


def _digits(value: object) -> list[str]:
    """Every run of digits in a value, in order.

    Order matters: `9.4 -> 4.9` is a different deck even though the same digits
    appear. Comparing sets would call that unchanged.
    """
    return re.findall(r"\d+", "" if value is None else str(value))


def _name(shape: ShapeInfo) -> str:
    label = shape.name or shape.kind
    return f"{label} (id={shape.id})"


def _quote(text: str, limit: int = 36) -> str:
    flat = text.replace("\n", " ")
    if not flat:
        return "(empty)"
    return repr(flat if len(flat) <= limit else flat[:limit] + "…")


def _inches(emu: int) -> str:
    return f"{emu / EMU_PER_INCH:+.2f}in"


def _size(cx: int, cy: int) -> str:
    return f"{cx / EMU_PER_INCH:.2f}x{cy / EMU_PER_INCH:.2f}in"


def _join(numbers: list[int]) -> str:
    return ", ".join(str(n) for n in numbers)
