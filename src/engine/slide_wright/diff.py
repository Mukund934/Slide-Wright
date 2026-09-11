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
    kind: str            # text | notes | geometry | size | formatting | link | table | added | removed
    description: str
    before: object = None
    after: object = None

    #: The change with the location removed: "font 'Century Gothic' -> '+mn-lt'"
    #: rather than "TextBox 4 (id=5) run 62 font 'Century Gothic' -> '+mn-lt'".
    #:
    #: A conformance pass on a real deck produces 272 of these and 266 of them
    #: are the same change in different places. A reader given one line each
    #: stops reading, which is the failure `report.py` describes for the CLI and
    #: which the web surfaces inherited. Grouping needs a key that is the change
    #: itself, and deriving one by trimming the description in a client would be
    #: string surgery on a sentence -- so the engine, which composed the
    #: sentence, says which half is which.
    summary: str = ""

    @property
    def is_content(self) -> bool:
        """Content changes alter what the deck says. Everything else is presentation.

        `notes` counts. A presenter's script is words somebody wrote, and the
        question this property answers is whether the deck still says what it
        said -- not whether the change is visible from the back of the room.
        """
        return self.kind in {"text", "notes", "table", "added", "removed", "link"}

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
            description=f"{_name(shape)} removed", summary="removed",
            before=shape.text or None,
        ))
    for shape_id in sorted(set(new) - set(old)):
        shape = new[shape_id]
        result.deltas.append(ShapeDelta(
            slide=number, shape_id=shape_id, kind="added",
            description=f"{_name(shape)} added", summary="added",
            after=shape.text or None,
        ))
    for shape_id in sorted(set(old) & set(new)):
        _diff_shape(number, old[shape_id], new[shape_id], result)

    _diff_notes(number, before, after, result)


def _diff_notes(number: int, before, after, result: DeckDiff) -> None:
    """What the presenter wrote under the slide.

    Notes live in their own part, so an edit never touches them and `verify`
    already compares them byte for byte. What nothing could do was say *what*
    changed in them, and that gap reached further than it looks: every
    verifier-side lock is a question put to this module, so `wording` --
    "leave my words exactly as written" -- was silent about a third of the
    words in a real deck.

    On `nasa-bhutan-water`, the deck this project quotes for *"272 corrections,
    0 change what the deck says"*, there are 2,708 words of speaker script
    against 983 on the slides. The claim was true and measured over 27% of what
    the deck says.

    Its own kind, rather than `text`: a reviewer needs to know the change is in
    the script and not on the page, and a delta that said "text" with no shape
    to point at would read as a shape edit. It is content all the same -- words
    a person wrote, which is the test `is_content` applies.
    """
    if before.notes == after.notes:
        return
    result.deltas.append(ShapeDelta(
        slide=number, shape_id="", kind="notes",
        description=f"speaker notes {_describe_text_change(before.notes, after.notes)}",
        summary=f"speaker notes {_describe_text_change(before.notes, after.notes)}",
        before=before.notes, after=after.notes,
    ))


def _diff_shape(number: int, old: ShapeInfo, new: ShapeInfo, result: DeckDiff) -> None:
    def add(kind: str, summary: str, b=None, a=None, *, where: str = "") -> None:
        """`summary` is the change; `where` is the part of the location that is
        finer than the shape, and belongs only in the description."""
        located = f"{_name(old)}{where} {summary}"
        result.deltas.append(ShapeDelta(
            slide=number, shape_id=old.id, kind=kind,
            description=located, summary=summary, before=b, after=a,
        ))

    if old.text != new.text:
        add("text", f"text {_describe_text_change(old.text, new.text)}",
            old.text, new.text)
        # Styling is suppressed when the text changed, because the text delta
        # has already re-described those runs. A link is not styling and the
        # text delta says nothing about one, so suppressing it hides the only
        # signal there was: a run emptied by an edit takes its hyperlink off the
        # slide while leaving the `a:hlinkClick` and the relationship in place,
        # and the deck comes back with a dead link and a clean report.
        _diff_links_by_presence(old, new, add)
        _diff_emphasis_lost(old, new, add)

    if (old.table_rows, old.table_cols) != (new.table_rows, new.table_cols):
        add("table",
            f"table {old.table_rows}x{old.table_cols} -> "
            f"{new.table_rows}x{new.table_cols}",
            (old.table_rows, old.table_cols), (new.table_rows, new.table_cols))

    _diff_geometry(old, new, add)
    _diff_paragraphs(old, new, add)
    _diff_formatting(old, new, add)


def _diff_paragraphs(old: ShapeInfo, new: ShapeInfo, add) -> None:
    """Compare the shape's line structure, which no comparison here could see.

    `runs` holds only runs with text, so `ShapeInfo.text` cannot distinguish a
    shape whose four bullets became one from a shape whose four bullets became
    one bullet and three blank ones. Both read back as the same string, and the
    diff said *0 change how it looks* over a slide that had grown three empty
    bullet points.

    That is not a hypothetical: a typed edit in the workspace carries no
    `before`, so it replaces the whole shape, and `_replace_slots` empties every
    run the replaced span covered. On a four-line placeholder in a real deck
    that left three blank paragraphs behind, each still drawing the bullet it
    inherits from the layout.

    Counted rather than paired: a scalar needs no run correspondence, so unlike
    the formatting comparison this one is meaningful *precisely* when the text
    changed, which is the only case it exists for.
    """
    if "table" in (old.kind, new.kind):
        # `.//a:p` inside a graphicFrame finds every cell's paragraphs, so this
        # count would describe the grid rather than a line of text. The table
        # delta already reports shape changes there.
        return

    before, after = old.paragraph_count, new.paragraph_count
    blank_before = before - len({r.paragraph for r in old.runs})
    blank_after = after - len({r.paragraph for r in new.runs})
    if (before, blank_before) != (after, blank_after):
        if before != after and blank_before != blank_after:
            summary = (f"text lines {before} -> {after}, "
                       f"blank {blank_before} -> {blank_after}")
        elif before != after:
            summary = f"text lines {before} -> {after}"
        else:
            summary = f"blank lines {blank_before} -> {blank_after}"
        add("formatting", summary, (before, blank_before), (after, blank_after))

    if before == after:
        _diff_paragraph_properties(old, new, add)


#: How a line sits, and what to call each of them in a sentence a reviewer reads.
PARAGRAPH_ATTRIBUTES = (("bullet", "bullet"), ("level", "indent level"),
                        ("alignment", "alignment"), ("line_spacing", "line spacing"))


def _diff_paragraph_properties(old: ShapeInfo, new: ShapeInfo, add) -> None:
    """Compare how each line sits, when the two shapes still have the same lines.

    Paired by index, which is sound only while the counts agree -- and when they
    do not, the delta above has already said so, so nothing is passed over in
    silence. That is the difference from the run comparison, where returning
    early on a count mismatch produced *"no structural differences"* over a deck
    whose figure had lost its bold.

    Measured across the corpus, these are not rare: 1,635 explicitly aligned
    paragraphs in 10 of 26 decks, 1,137 that declare a bullet in 8, 707 that set
    their own line spacing, 212 indented. Nothing in the engine writes any of
    them today. An edit *moves text between* them -- a replacement spanning two
    bullets leaves its tail under the first one's bullet and indent -- so the
    line a sentence sits on can change without the sentence changing.
    """
    for index, (a, b) in enumerate(zip(old.paragraphs, new.paragraphs)):
        for attribute, label in PARAGRAPH_ATTRIBUTES:
            was, now = getattr(a, attribute), getattr(b, attribute)
            if was == now:
                continue
            add("formatting", f"{label} {_shown(was)} -> {_shown(now)}",
                was, now, where=f" line {index + 1}")


def _shown(value: object) -> str:
    """A paragraph property as a reader would see it named.

    None is *inherited*, not *absent*: a line with no `<a:pPr>` takes the
    property from its layout, and printing "None" would read as "nothing".
    """
    return "inherited" if value is None else str(value)


def _diff_geometry(old: ShapeInfo, new: ShapeInfo, add) -> None:
    if None not in (old.x, old.y, new.x, new.y) and (old.x, old.y) != (new.x, new.y):
        dx, dy = new.x - old.x, new.y - old.y
        if max(abs(dx), abs(dy)) >= VISIBLE_MOVE_EMU:
            add("geometry",
                f"moved {_inches(dx)} right, {_inches(dy)} down",
                (old.x, old.y), (new.x, new.y))
        else:
            add("geometry",
                "moved by less than 0.01in (probably rounding)",
                (old.x, old.y), (new.x, new.y))

    if None not in (old.cx, old.cy, new.cx, new.cy) and (old.cx, old.cy) != (new.cx, new.cy):
        add("size",
            f"resized {_size(old.cx, old.cy)} -> {_size(new.cx, new.cy)}",
            (old.cx, old.cy), (new.cx, new.cy))

    if old.rotation_deg != new.rotation_deg:
        add("geometry",
            f"rotation {old.rotation_deg or 0:g}° -> {new.rotation_deg or 0:g}°",
            old.rotation_deg, new.rotation_deg)


#: The run attributes a reader would see change, each with the kind of delta it
#: produces. Order is the order they are reported in, so it is the order a
#: reviewer reads them.
#:
#: `link` is not formatting. A hyperlink is what the deck *points at*, and a
#: reader notices losing one the way they notice losing a sentence -- so it
#: counts as content, and a deck that comes back with a dead link may not be
#: described as "nothing changed about what it says".
RUN_ATTRIBUTES = (("size_pt", "size", "formatting"), ("bold", "bold", "formatting"),
                  ("italic", "italic", "formatting"),
                  ("underline", "underline", "formatting"),
                  ("strike", "strikethrough", "formatting"),
                  ("baseline", "baseline", "formatting"),
                  ("caps", "capitals", "formatting"),
                  ("font", "font", "formatting"),
                  ("color", "colour", "formatting"), ("link", "link", "link"))


def _diff_formatting(old: ShapeInfo, new: ShapeInfo, add) -> None:
    """Compare run formatting, but only where the text itself is unchanged.

    When the text changed the runs have already been re-described by the text
    delta, and reporting every formatting field of a rewritten run as its own
    difference buries the one line the reviewer needs.

    Pairing runs by index is only meaningful when both sides split the text the
    same way. It used to be the only path, guarded by an equal-count check that
    returned silently when the counts differed -- so two decks both reading
    "Total 42", one with the figure bold and one without, came back **"No
    structural differences"**. Same text, one run against two, and the panel
    whose question is *is my deck still my deck* answered yes.

    Equal counts were not safe either, in the same direction. Move one boundary
    by a character -- "Total " + "**42**" against "Total" + "** 42**", where the
    space changed weight -- and index pairing finds both pairs equal and reports
    nothing, because it compares run 1 to run 1 rather than character to
    character. Measured: 0 deltas before, 1 after.

    So the partition decides. Identical partitions keep index pairing, which
    names the run and keeps one delta per run. Anything else is compared
    character by character and reported over the text it covers, which is the
    only description that survives runs being split or merged.
    """
    if old.text != new.text:
        return
    if [r.text for r in old.runs] == [r.text for r in new.runs]:
        for i, (a, b) in enumerate(zip(old.runs, new.runs)):
            for attr, label, kind in RUN_ATTRIBUTES:
                av, bv = getattr(a, attr), getattr(b, attr)
                if av != bv:
                    # The run index is location, so it goes in `where`. Without
                    # that, 266 identical font changes are 266 distinct
                    # summaries and grouping them is impossible.
                    add(kind, f"{label} {av!r} -> {bv!r}", av, bv,
                        where=f" run {i + 1}")
        return
    _diff_formatting_by_character(old, new, add)


def _diff_emphasis_lost(old: ShapeInfo, new: ShapeInfo, add) -> None:
    """Styling that the shape used before the edit and does not use after it.

    Run-by-run formatting is deliberately not compared when the text changed:
    the runs have been re-described by the text delta and reporting every field
    of every rewritten run buries the one line the reviewer needs. That
    reasoning holds for *changes* and not for *disappearances*.

    Replacing a shape's text is the only way the workspace edits words -- the
    contract names an object and its new full text -- so every intra-shape
    emphasis collapses into one run every time. Change FY25 to FY26 on
    "Revenue grew **15%** in FY25" and the bold on the figure is gone, with the
    diff saying `text ...FY2[5 -> 6]` and nothing else. The user changed a year.

    One delta per attribute, not per run: the claim is about the shape, and the
    reviewer needs to know the emphasis went, not where it went from.
    """
    for attr, label, kind in RUN_ATTRIBUTES:
        if kind != "formatting":
            continue
        was = {getattr(run, attr) for run in old.runs}
        if len(was) < 2:
            continue          # nothing to lose: the shape was uniform already
        lost = was - {getattr(run, attr) for run in new.runs}
        if not lost:
            continue
        gone = ", ".join(repr(value) for value in sorted(lost, key=str))
        add("formatting", f"{label} {gone} no longer used in this text", sorted(lost, key=str), None)


def _diff_links_by_presence(old: ShapeInfo, new: ShapeInfo, add) -> None:
    """Which links the shape carries, when the runs cannot be lined up.

    Compared as a set rather than by position: the text changed, so there is no
    correspondence between the runs either side, and "the deck used to point
    here and no longer does" is the whole of what a reviewer needs.
    """
    before = {r.link for r in old.runs if r.link}
    after = {r.link for r in new.runs if r.link}
    for gone in sorted(before - after):
        add("link", f"link to {gone!r} removed", gone, None)
    for arrived in sorted(after - before):
        add("link", f"link to {arrived!r} added", None, arrived)


def _diff_formatting_by_character(old: ShapeInfo, new: ShapeInfo, add) -> None:
    """Compare formatting position by position, for runs that do not line up."""
    text = old.text
    before = _run_per_character(old.runs)
    after = _run_per_character(new.runs)
    if not len(before) == len(after) == len(text):
        return
    for attr, label, kind in RUN_ATTRIBUTES:
        for (av, bv), span in _differing_spans(text, before, after, attr):
            add(kind, f"{label} {av!r} -> {bv!r}", av, bv,
                where=f" in {_quote(span)}")


def _run_per_character(runs) -> list:
    """The run each character of the shape's text belongs to."""
    return [run for run in runs for _ in run.text]


def _differing_spans(text: str, before: list, after: list, attr: str):
    """Maximal stretches of text over which one attribute differs the same way.

    Merging adjacent positions matters: a typeface change across a whole run is
    one sentence a reviewer reads once, not forty characters of it.
    """
    spans: list[list] = []
    for index in range(len(text)):
        pair = (getattr(before[index], attr), getattr(after[index], attr))
        if pair[0] == pair[1]:
            continue
        if spans and spans[-1][0] == pair and spans[-1][2] == index:
            spans[-1][2] = index + 1
        else:
            spans.append([pair, index, index + 1])
    return [(pair, text[start:end]) for pair, start, end in spans]


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
