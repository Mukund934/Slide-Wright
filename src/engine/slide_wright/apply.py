"""Apply an approved change set to a deck.

Text and cell edits are written by **patching the target run in place** rather
than rebuilding the slide. That matters more than it sounds.

The round-trip engine rebuilds any slide it touches from an intermediate
representation, so a one-word edit re-serialises the whole slide: untouched
slides stay byte-identical, but untouched *objects on the edited slide* do not.
Patching the run directly narrows the guarantee from slide-level to
object-level — every other byte of the edited slide survives too.

Structural and visual changes still need the full engine; those route through
`engines/pptmaster.py`. This module deliberately handles only the operations it
can perform without re-serialising anything, and refuses the rest rather than
silently widening its blast radius.
"""

from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from slide_wright.changeset import Change, ChangeSet, Op, Status
from slide_wright.inspect import NS
from slide_wright.package import Package
from slide_wright.charts import ChartUnsupported
from slide_wright.charts import assert_preserved as assert_charts_preserved
from slide_wright.charts import find_all as find_charts
from slide_wright.charts import guard_edit as guard_chart_edit
from slide_wright.smartart import SmartArtUnsupported, assert_preserved, guard_edit
from slide_wright.smartart import find_all as find_diagrams

# Operations this module can perform in place, without a slide rebuild.
IN_PLACE_OPS = {Op.SET_TEXT, Op.SET_TABLE_CELL, Op.SET_FONT_SIZE, Op.SET_FONT,
                Op.SET_COLOR, Op.MOVE, Op.RESIZE}

# Operations that change what an object *says*. The two content guards below
# exist because a chart and a diagram each keep the same information twice, and
# an edit that updates one copy and not the other is the silent corruption this
# product refuses. Geometry is not one of those copies: a graphicFrame's box
# lives in the slide part, and neither the chart part nor the diagram model
# records where on the slide it sits.
CONTENT_OPS = IN_PLACE_OPS - {Op.MOVE, Op.RESIZE}


class ApplyError(Exception):
    """A change could not be applied. Fail closed rather than approximate."""


@dataclass
class ApplyResult:
    output: Path
    applied: list[Change] = field(default_factory=list)
    failed: list[tuple[Change, str]] = field(default_factory=list)
    touched_parts: set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return not self.failed


def apply_changes(deck: str | Path, changeset: ChangeSet, output: str | Path) -> ApplyResult:
    """Write approved changes to a new file. The source is never modified."""
    deck, output = Path(deck), Path(output)
    approved = changeset.approved
    if not approved:
        raise ApplyError("no approved changes; nothing to apply")

    unsupported = [c for c in approved if c.op not in IN_PLACE_OPS]
    if unsupported:
        ops = ", ".join(sorted({c.op.value for c in unsupported}))
        raise ApplyError(
            f"in-place applier cannot perform: {ops}. "
            "Route these through the round-trip engine instead."
        )

    pkg = Package.open(deck)

    # Two constructs are refused up front, before anything is written. Both
    # keep the same information twice and go quietly wrong when the copies
    # drift: a diagram's model against its drawing cache (smartart.py), and a
    # chart's cached series against its embedded workbook (charts.py).
    # Which shapes are charts and which are diagrams, found once.
    #
    # `guard_edit` used to be called per change, and each call scanned the whole
    # package: every slide, every rels file, every chart part. Profiled on a
    # 272-change tidy of a 26-slide deck, that was 32,778 reads of the archive
    # and 102 of the 103 seconds it took. A 1,199-change tidy took 7.8 minutes,
    # and the cost per change was *rising* -- the scan is the same size every
    # time, so the more changes there are the more times it runs.
    #
    # The set is identical for every change, so it is computed once and each
    # change is a lookup. The expensive descriptive call still happens, but only
    # on the one change that is about to be refused, where its message is what
    # the user reads.
    diagram_shapes = {(d.slide, d.shape_id) for d in find_diagrams(pkg)}
    chart_shapes = {(c.slide, c.shape_id) for c in find_charts(pkg)}

    for change in approved:
        # Moving either kind is safe. Resizing a chart is too -- PowerPoint lays
        # the chart out into whatever frame it is given, and the chart part
        # carries no geometry to fall out of step. A diagram is the exception:
        # its drawing cache holds absolute coordinates produced by the layout
        # engine at one particular size, so a resized frame renders a cache that
        # no regeneration would produce.
        target = change.target.split("/")[0]
        try:
            if ((change.op in CONTENT_OPS or change.op is Op.RESIZE)
                    and (change.slide, target) in diagram_shapes):
                guard_edit(pkg, change.slide, change.target)
            if change.op in CONTENT_OPS and (change.slide, target) in chart_shapes:
                guard_chart_edit(pkg, change.slide, change.target)
        except (SmartArtUnsupported, ChartUnsupported) as exc:
            raise ApplyError(str(exc)) from exc

    result = ApplyResult(output=output)

    # Group by slide part so each part is parsed and written once.
    by_part: dict[str, list[Change]] = {}
    for change in approved:
        part = _slide_part_for(pkg, change.slide)
        if part is None:
            result.failed.append((change, f"slide {change.slide} not found"))
            continue
        by_part.setdefault(part, []).append(change)

    patched: dict[str, bytes] = {}
    for part_name, changes in by_part.items():
        root = etree.fromstring(pkg.read(part_name))
        # Resolved against the part as it was read, before any change alters it.
        pinned = _pin_run_targets(root, changes)
        part_applied = False
        for change in changes:
            try:
                ok, why = _apply_one(root, change, pinned)
                if ok:
                    change.status = Status.APPLIED
                    result.applied.append(change)
                    part_applied = True
                else:
                    change.status = Status.FAILED
                    result.failed.append((change, why))
            except ApplyError as exc:
                change.status = Status.FAILED
                result.failed.append((change, str(exc)))
        if part_applied:
            patched[part_name] = etree.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )
            result.touched_parts.add(part_name)

    _write_package(deck, output, patched)

    # Even when nothing targeted them, prove neither was collateral damage. A
    # chart that loses its workbook still looks correct and only fails when
    # someone clicks "Edit Data", so it is checked rather than assumed.
    try:
        assert_preserved(pkg, output)
        assert_charts_preserved(pkg, output)
    except (SmartArtUnsupported, ChartUnsupported) as exc:
        raise ApplyError(str(exc)) from exc

    return result


# ── per-operation handlers ───────────────────────────────────────────────────

# Operations whose replacement is text. A number is fine -- a model writing 42
# where a cell wants "42" is not an error -- but a structure is not: `after` as
# {"value": 12} used to be written into the slide as the literal Python repr of
# a dict, on a board slide, with the report calling it verified.
_TEXTUAL_OPS = {Op.SET_TEXT, Op.SET_TABLE_CELL, Op.SET_FONT, Op.SET_COLOR}


def _unusable_value(change: Change) -> str:
    """Whether `after` is the kind of thing this operation can write.

    The applier is the last thing between a proposal and the file, and it was
    reachable with values no validator upstream had ruled out. Two shapes of
    failure, both live:

      · a structure or None reached the file, stringified. `{'nested': 'x'}`
        and `None` were written into slide text verbatim.
      · a non-numeric size raised ValueError out of `float()`, which is not an
        ApplyError, so it escaped the caller's handler and surfaced as a 500
        rather than as a refusal with a reason.

    Refusing here rather than upstream is deliberate: the planner is not the
    only way a change is built, and a guarantee that lives in one caller is a
    guarantee the next caller does not have.
    """
    after = change.after
    if change.op in _TEXTUAL_OPS:
        if not isinstance(after, (str, int, float)) or isinstance(after, bool):
            return (f"the replacement for {change.target} is "
                    f"{type(after).__name__}, and this writes text")
        return ""
    if change.op is Op.SET_FONT_SIZE:
        try:
            float(after)
        except (TypeError, ValueError):
            return f"{after!r} is not a point size"
        return ""
    if change.op in (Op.MOVE, Op.RESIZE):
        pair = after if isinstance(after, (list, tuple)) else (after, None)
        if len(pair) != 2:
            return f"{change.op.value} needs two coordinates, got {after!r}"
        for value in pair:
            if value is None:
                continue
            try:
                int(value)
            except (TypeError, ValueError):
                return f"{value!r} is not a coordinate"
        return ""
    return ""


def _pin_run_targets(root, changes) -> dict[int, list]:
    """Resolve every `<shape>/run/<index>` target against the part as it was read.

    A run index names a position in `inspect`'s enumeration, which skips runs
    with no text. Applying a text edit can empty a run -- it happens whenever a
    replaced span covers one -- so the enumeration shifts *while the change set
    is being applied*, and an index resolved afterwards names a different run
    than the one the reviewer was shown.

    Measured on five runs AAA|BBB|CCC|DDD|EEE: replace "AAABBB" with "X", then a
    typeface change addressed at `run/3`. Run 3 is DDD in the deck as read and
    EEE by the time the second change lands. Applied: 2. Failed: 0. DDD keeps its
    typeface and EEE, which nobody named, changes.

    Pinning to the element rather than the index makes the target mean what it
    meant when the change set was written, which is the only reading under which
    a reviewer's approval is about the thing they approved.
    """
    pinned: dict[int, list] = {}
    for change in changes:
        if not _names_a_run(change.target):
            continue
        parts = change.target.split("/")
        shape = _find_shape(root, parts[0])
        if shape is None:
            continue
        try:
            index = int(parts[2])
        except ValueError:
            continue
        runs = _addressable_runs(shape)
        if 0 <= index < len(runs):
            pinned[id(change)] = [runs[index]]
    return pinned


def _apply_one(root, change: Change, pinned: dict[int, list] | None = None) -> tuple[bool, str]:
    """Apply one change, returning whether it worked and, if not, why.

    A single boolean conflated two very different failures: the shape is not
    on this slide, and the shape is here but the edit does not apply to it.
    Both were reported as "target not found", which sends a reader looking for
    a shape id that is in fact present.
    """
    shape_id = change.target.split("/")[0]
    shape = _find_shape(root, shape_id)
    if shape is None:
        return False, f"no shape with id {shape_id!r} on slide {change.slide}"

    unusable = _unusable_value(change)
    if unusable:
        return False, unusable

    if change.op is Op.SET_TEXT:
        if _set_text(shape, str(change.before), str(change.after)):
            return True, ""
        return False, (f"shape {shape_id} does not contain the text "
                       f"{str(change.before)!r}")
    if change.op is Op.SET_TABLE_CELL:
        if _set_table_cell(shape, change):
            return True, ""
        size = _table_size(shape)
        m = re.search(r"/r(\d+)/c(\d+)$", change.target)
        if m and size and (int(m.group(1)) >= size[0] or int(m.group(2)) >= size[1]):
            # "does not hold 'None'" for a cell that is not on the table sends
            # a reader looking for a value in a cell that does not exist.
            return False, (f"the table on slide {change.slide} is "
                           f"{size[0]}x{size[1]}; {change.target} is off it")
        return False, f"cell {change.target} does not hold {str(change.before)!r}"
    if change.op in (Op.SET_FONT, Op.SET_COLOR):
        if _set_run_format(shape, change, pinned or {}):
            return True, ""
        return False, (f"run {change.target} not found, or it carries no explicit "
                       f"formatting to change")
    if change.op is Op.SET_FONT_SIZE:
        if _set_font_size(shape, change, pinned or {}):
            return True, ""
        # Runs inherit their size from the layout unless they carry an explicit
        # override. There is nothing to change, and saying "not found" would be
        # a lie about the shape rather than a fact about its formatting.
        return False, (f"shape {shape_id} has no explicit text formatting to "
                       f"change; its size is inherited from the layout")
    if change.op in (Op.MOVE, Op.RESIZE):
        if _set_geometry(shape, change):
            return True, ""
        return False, (f"shape {shape_id} has no position of its own; it is "
                       f"placed by the layout")
    raise ApplyError(f"unhandled op {change.op}")


def _set_text(shape, before: str, after: str) -> bool:
    """Replace text in the run that holds it, leaving every other run alone.

    Text in a shape is not one string. It is runs inside paragraphs, split
    wherever formatting changes, and `ShapeInfo.text` joins *all* of them. So a
    caller's `before` may correspond to a single run, to one paragraph, or to
    the whole shape across several paragraphs.

    Tried narrowest-first, because a narrower match means fewer bytes touched:
      1. one run holds it exactly            — two-character edits land here
      2. one paragraph holds it              — write back across that paragraph
      3. the whole shape holds it            — write back across every paragraph

    Steps 2 and 3 lose intra-run formatting inside the matched span, which is
    unavoidable when replacing text that spans differently formatted runs. They
    lose nothing outside it: a run the span does not reach keeps its own text
    and its own formatting, which is what makes the promise checkable at run
    granularity rather than only at slide granularity.
    """
    # 1. Exact single-run match: the narrowest possible edit.
    for run in shape.findall(".//a:r", NS):
        t = run.find("a:t", NS)
        if t is not None and t.text == before:
            t.text = after
            return True

    paragraphs = shape.findall(".//a:p", NS)

    # 2. The span sits inside one paragraph.
    for para in paragraphs:
        if _replace_within(para.findall("a:r", NS), before, after):
            return True

    # 3. The span crosses paragraphs. The joined view puts a newline between
    #    them, exactly as `ShapeInfo.text` does -- the two have to agree, or a
    #    `before` read off the deck could not be found in it.
    return _replace_across_paragraphs(paragraphs, before, after)


def _replace_across_paragraphs(paragraphs, before: str, after: str) -> bool:
    """Replace a span that crosses paragraphs, and end with the lines it asks for.

    A newline in this view is a paragraph boundary, so a replacement's newlines
    have to become paragraphs and a replacement with fewer of them has to leave
    fewer behind. Writing the whole thing into one run and emptying the rest is
    right about the words and wrong about the shape: a four-line placeholder set
    to one line came back as one line plus **three empty paragraphs**, each
    still drawing the bullet it inherits from the layout, and `ShapeInfo.text`
    could not see them because it reads runs and an emptied run is not one.

    It matters because every typed edit in the workspace arrives here. `SetSpec`
    carries no `before`, so the API fills it with the shape's whole text, and a
    body placeholder is the most ordinary shape in a deck.

    The other half is the same mistake facing the other way: a literal newline
    written into an `<a:t>` is not a line break in OOXML, and reading it back
    gives a string indistinguishable from two paragraphs. The engine could not
    tell its own two outcomes apart.

    Formatting is preserved run by run wherever the structure allows it. Text
    that has to *move* between paragraphs -- the tail of the last covered
    paragraph, when the paragraphs under it are being removed or added -- keeps
    its words and not its styling, which is the same trade `_set_text` has
    always documented for a span that crosses differently formatted runs.
    """
    entries = []          # [a:p element, [writable (a:t, text)], joined text]
    for para in paragraphs:
        slots = [
            (t, t.text)
            for t in (r.find("a:t", NS) for r in para.findall("a:r", NS))
            if t is not None and t.text
        ]
        if slots:
            entries.append([para, slots, "".join(text for _, text in slots)])
    if not entries:
        return False

    joined = "\n".join(text for _, _, text in entries)
    start = joined.find(before) if before else -1
    if start < 0:
        return False
    end = start + len(before)

    # Which entries the span touches, and where inside them it begins and ends.
    offsets, cursor = [], 0
    for _, _, text in entries:
        offsets.append(cursor)
        cursor += len(text) + 1          # +1 for the separator after it
    first = max(i for i, off in enumerate(offsets) if off <= start)
    last = max(i for i, off in enumerate(offsets) if off <= end)
    head_at = start - offsets[first]
    tail_at = end - offsets[last]
    tail = entries[last][2][tail_at:]

    lines = after.split("\n")
    covered = last - first + 1
    reused = min(len(lines), covered)

    # A tail that cannot stay where it is has to travel with the last line.
    carry = len(lines) > covered and bool(tail)
    template = copy.deepcopy(entries[last][0]) if len(lines) > covered else None

    for k in range(reused):
        _, slots, text = entries[first + k]
        lo = head_at if k == 0 else 0
        at_last_covered = first + k == last
        hi = tail_at if at_last_covered and not carry else len(text)
        payload = lines[k]
        if k == reused - 1 and not at_last_covered and not carry:
            payload += tail          # the paragraphs below are about to go
        _write_span(slots, lo, hi, payload)

    if len(lines) < covered:
        for element, _, _ in entries[first + reused:last + 1]:
            _drop_paragraph(element)
    elif len(lines) > covered:
        extra = lines[covered:]
        if carry:
            extra = extra[:-1] + [extra[-1] + tail]
        anchor = entries[last][0]
        for line in extra:
            anchor.addnext(_paragraph_like(template, line))
            anchor = anchor.getnext()
    return True


def _write_span(slots, lo: int, hi: int, after: str) -> bool:
    """Replace `[lo, hi)` of a run sequence's joined text, run by run.

    The run holding the start keeps its prefix and receives `after`; the run
    holding the end keeps its suffix; runs entirely inside are emptied. A run
    the span does not reach keeps its text and its formatting, which is what
    makes the promise checkable at run granularity rather than only at slide
    granularity.

    `lo == hi` is an insertion, not a no-op. It arrives whenever a replacement
    begins on a paragraph boundary -- "\ndef" covers no character of the
    paragraph above it -- and the first version of this dropped the replacement
    on the floor and removed the paragraph below, so a deck lost a line and
    gained nothing.
    """
    offset, written = 0, False
    for element, text in slots:
        slot_start, slot_end = offset, offset + len(text)
        offset = slot_end
        if element is None:
            continue
        covers = slot_start < hi and slot_end > lo
        if not covers and not (lo == hi and slot_start <= lo <= slot_end):
            continue
        prefix = text[: lo - slot_start] if slot_start < lo else ""
        suffix = text[hi - slot_start:] if slot_end > hi else ""
        element.text = prefix + after + suffix if not written else prefix + suffix
        written = True
    return written


def _drop_paragraph(para) -> None:
    """Remove a paragraph, unless it is the only one its body has.

    A `<a:txBody>` and a table cell both require at least one `<a:p>`, and a
    deck that opens is the floor everything else here stands on. Emptying is the
    fallback, and it is the right answer when it is the last one: a shape with
    one line set to nothing is a shape with one empty line.
    """
    parent = para.getparent()
    if parent is None:
        return
    if len(parent.findall("a:p", NS)) <= 1:
        for t in para.findall(".//a:t", NS):
            t.text = ""
        return
    parent.remove(para)


def _paragraph_like(template, text: str):
    """A new paragraph carrying `template`'s properties and one run of `text`.

    Cloned rather than built, so the bullet, indent, alignment and run styling
    of the line it follows all carry -- a user who adds a line to a bulleted
    list means a bullet, and a paragraph assembled from nothing would arrive
    unstyled in a deck whose whole promise is that it still looks like itself.

    The hyperlink does not carry, and it is the one thing here that must not.
    Cloning a linked line made the new line point at the same target: an edit
    creating a link nobody asked for, which is the same category of wrong as an
    edit destroying one, arriving from the other direction. Styling describes
    the line; a link is what the deck *does*.
    """
    clone = copy.deepcopy(template)
    for link in clone.findall(f".//a:hlinkClick", NS):
        link.getparent().remove(link)
    runs = clone.findall("a:r", NS)
    for extra in runs[1:]:
        clone.remove(extra)
    if runs:
        t = runs[0].find("a:t", NS)
        if t is None:
            t = etree.SubElement(runs[0], "{%s}t" % NS["a"])
        t.text = text
    else:
        run = etree.SubElement(clone, "{%s}r" % NS["a"])
        etree.SubElement(run, "{%s}t" % NS["a"]).text = text
    return clone


def _replace_within(runs, before: str, after: str) -> bool:
    """Replace `before` across a run sequence, touching only the runs it covers.

    The span is located in the joined text and then written back run by run, so
    a run that lies wholly outside it keeps its text *and its formatting*. The
    run holding the start of the span keeps its prefix and receives `after`; the
    run holding the end keeps its suffix; runs entirely inside are emptied.

    This used to write the whole joined string into the first run and blank
    every other one, which is correct about the text and destroys everything
    else. Editing two words at the front of a paragraph unbolded the figure at
    the back of it, and an edit spanning two bullets collapsed the third —
    reported applied, with the text identical, so a content diff saw nothing.
    Formatting is still lost *inside* the span, which is unavoidable when the
    replacement crosses runs that are formatted differently; the docstring above
    `_set_text` has always said that, and now it is true.
    """
    slots = [(t, t.text or "") for t in (r.find("a:t", NS) for r in runs) if t is not None]
    return _replace_slots(slots, before, after)


def _replace_slots(slots, before: str, after: str) -> bool:
    """Find `before` in a run sequence's joined text and replace it in place.

    A slot is `(element, text)`. Locating and writing are separate because the
    span has to be found in the string the caller was reading and written back
    to the runs that string was assembled from.
    """
    if not slots:
        return False

    joined = "".join(text for _, text in slots)
    start = joined.find(before) if before else -1
    if start < 0:
        return False
    return _write_span(slots, start, start + len(before), after)


def _set_table_cell(shape, change: Change) -> bool:
    m = re.search(r"/r(\d+)/c(\d+)$", change.target)
    if not m:
        raise ApplyError(f"table cell target must end in /r{{row}}/c{{col}}: {change.target}")
    row_idx, col_idx = int(m.group(1)), int(m.group(2))

    rows = shape.findall(".//a:tr", NS)
    if row_idx >= len(rows):
        return False
    cells = rows[row_idx].findall("a:tc", NS)
    if col_idx >= len(cells):
        return False

    cell = cells[col_idx]
    slots = cell.findall(".//a:t", NS)
    if not slots:
        return False

    after = str(change.after)
    before = None if change.before is None else str(change.before)

    if before:
        # 1. One run holds exactly the value being replaced.
        for t in slots:
            if t.text == before:
                t.text = after
                return True
        # 2. The value is split across runs, which is ordinary in a real deck:
        #    part of a figure gets bolded, or a language run boundary falls
        #    inside it, and "1,234" is stored as "1,2" + "34".
        #
        #    This used to fall through to the empty-cell branch below and write
        #    the new value into the *first* run while leaving the rest in place:
        #    replacing 1,234 with 1,987 produced **1,98734**. Applied, verified,
        #    cited to a spreadsheet coordinate, and wrong -- in the one place the
        #    product exists to be trusted about.
        return _replace_within(cell.findall(".//a:r", NS), before, after)

    if before is None:
        # No value to match: the change is "this cell now says X". Blanking the
        # trailing runs is right here, because the whole cell is the target.
        slots[0].text = after
        for t in slots[1:]:
            t.text = ""
        return True

    # `before` was the empty string. If the cell really is empty, write into it;
    # if it is not, the caller is wrong about the cell and must be told so.
    if "".join(t.text or "" for t in slots):
        return False
    slots[0].text = after
    return True


def _table_size(shape) -> tuple[int, int] | None:
    rows = shape.findall(".//a:tr", NS)
    if not rows:
        return None
    return len(rows), max(len(r.findall("a:tc", NS)) for r in rows)


def _set_font_size(shape, change: Change, pinned: dict[int, list]) -> bool:
    """Change one run's size, or every run's, according to what the target names.

    `_set_run_format` has honoured `<shape>/run/<index>` since run addressing
    existed and this did not: it set `sz` on every `a:rPr` in the shape whatever
    the target said. A change reviewed as "make the footnote 12pt" -- one run,
    named -- took the 28pt headline beside it down to 12pt as well, and reported
    itself applied. Three ops, two addressing rules, and the review UI showing
    the narrow one.
    """
    runs = _targeted_runs(shape, change, pinned)
    if not runs:
        return False
    hundredths = str(int(round(float(change.after) * 100)))
    changed = False
    for run in runs:
        rpr = run.find("a:rPr", NS)
        if rpr is not None:
            rpr.set("sz", hundredths)
            changed = True
    return changed


def _addressable_runs(shape) -> list:
    """The runs a `<shape>/run/<index>` target can address.

    This must enumerate exactly what `inspect` enumerates, because the index in
    the target was produced by reading the deck through `inspect`. It does not
    include runs with no text, and neither may this.

    That divergence was a real bug and an instructive one. A shape carrying two
    empty runs made `inspect` see fifteen runs where the applier saw seventeen,
    so a change addressed at run 13 was written to a different run entirely --
    and reported as applied, because a font change had indeed been made
    somewhere. Every check passed: the content was untouched, no native object
    was lost, the fidelity score was fine. Only running the pass twice revealed
    it, because the run that was supposed to change never did and kept being
    proposed again.
    """
    addressable = []
    for run in shape.findall(".//a:r", NS):
        text = run.find("a:t", NS)
        if text is None or not text.text:
            continue
        addressable.append(run)
    return addressable


def _names_a_run(target: str) -> bool:
    parts = target.split("/")
    return len(parts) >= 3 and parts[1] == "run"


def _targeted_runs(shape, change: Change, pinned: dict[int, list]) -> list | None:
    """The runs a change addresses: the one it named, or all of them.

    A target naming a run is answered only from `pinned`, never re-resolved
    here. Re-resolving would read an index against a tree that earlier changes
    in the same set may have altered, which is the whole defect `_pin_run_targets`
    exists to close. Nothing pinned means the target named a run that was not
    there when the set was written -- a refusal, not a quietly wider edit.
    """
    if _names_a_run(change.target):
        return pinned.get(id(change))
    return _addressable_runs(shape)


def _set_run_format(shape, change: Change, pinned: dict[int, list]) -> bool:
    """Change one run's typeface or colour, touching nothing else.

    Targets are `<shape>/run/<index>`; a bare shape id applies to every run in
    the shape. Addressing a single run matters for conformance work: a text box
    where one word was pasted in the wrong font should come back with that word
    fixed and every other run byte-identical, which is what makes "we changed
    only what did not conform" a checkable claim rather than a slogan.
    """
    runs = _targeted_runs(shape, change, pinned)
    if not runs:
        return False

    changed = False
    for run in runs:
        rpr = run.find("a:rPr", NS)
        if rpr is None:
            # A run with no explicit formatting inherits from the layout. We do
            # not invent an override here: doing so would silently detach the
            # run from a template change made later, which is the opposite of
            # what a conformance pass is for.
            continue
        if change.op is Op.SET_FONT:
            for tag in ("a:latin", "a:cs", "a:ea"):
                element = rpr.find(tag, NS)
                if element is not None:
                    element.set("typeface", str(change.after))
                    changed = True
        else:
            fill = rpr.find("a:solidFill/a:srgbClr", NS)
            if fill is not None:
                fill.set("val", str(change.after).lstrip("#").upper())
                changed = True
    return changed


def _set_geometry(shape, change: Change) -> bool:
    xfrm = shape.find(".//a:xfrm", NS)
    if xfrm is None:
        # A graphicFrame -- every native table and chart -- states its box as
        # `p:xfrm`. Missing it here did not fail loudly: the caller reported
        # "no position of its own; it is placed by the layout", which is untrue
        # of a graphicFrame and sends a reviewer looking for a layout that does
        # not place it.
        xfrm = shape.find("./p:xfrm", NS)
    if xfrm is None:
        return False
    if change.op is Op.MOVE:
        off = xfrm.find("a:off", NS)
        if off is None:
            return False
        x, y = change.after if isinstance(change.after, (list, tuple)) else (change.after, None)
        if x is not None:
            off.set("x", str(int(x)))
        if y is not None:
            off.set("y", str(int(y)))
        return True
    ext = xfrm.find("a:ext", NS)
    if ext is None:
        return False
    cx, cy = change.after if isinstance(change.after, (list, tuple)) else (change.after, None)
    if cx is not None:
        ext.set("cx", str(int(cx)))
    if cy is not None:
        ext.set("cy", str(int(cy)))
    return True


# ── helpers ──────────────────────────────────────────────────────────────────

def _find_shape(root, shape_id: str):
    for el in root.iter():
        if etree.QName(el).localname != "cNvPr":
            continue
        if el.get("id") == shape_id:
            parent = el.getparent()
            while parent is not None:
                if etree.QName(parent).localname in {"sp", "pic", "graphicFrame", "grpSp", "cxnSp"}:
                    return parent
                parent = parent.getparent()
    return None


def _slide_part_for(pkg: Package, slide_number: int) -> str | None:
    for part in pkg.slides():
        if part.slide_number == slide_number:
            return part.name
    return None


def _scratch_beside(destination: Path) -> Path:
    """A private name to write under, in the destination's own directory.

    Same directory so the move into place is a rename within one filesystem and
    therefore atomic. Unique per *call*: keying it on the process id was enough
    for two processes and not for two threads, and two threads is the ordinary
    case -- a server handling two requests. Three concurrent applies produced
    `PermissionError: the process cannot access the file because it is being
    used by another process`, one success and two raw 500s.
    """
    handle, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent
    )
    os.close(handle)
    return Path(name)


def _write_package(source: Path, output: Path, patched: dict[str, bytes]) -> None:
    """Copy the package, substituting only the patched parts.

    Every other part is copied byte-for-byte from the source, which is what
    makes the fidelity claim hold by construction rather than by hope.

    Written beside the destination and moved into place, never written at the
    destination. A zip whose writing stops halfway still gets a central
    directory when the file closes, so an interrupted apply used to leave a
    *valid* archive holding the first few parts -- measured: 8,579 bytes where
    45,348 belonged, at the exact path a finished deck goes, and `Package.open`
    accepted it. A partial deck that reads as a deck is the precise shape of
    failure this engine's third principle forbids.

    `os.replace` is atomic within a filesystem on Windows and POSIX alike, so
    the destination holds the whole previous file or the whole new one and
    never something in between.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = _scratch_beside(output)
    try:
        if patched:
            with zipfile.ZipFile(source) as zin, zipfile.ZipFile(
                scratch, "w", zipfile.ZIP_DEFLATED
            ) as zout:
                for info in zin.infolist():
                    data = patched.get(info.filename)
                    zout.writestr(
                        info.filename,
                        data if data is not None else zin.read(info.filename),
                    )
        else:
            shutil.copy(source, scratch)
        os.replace(scratch, output)
    finally:
        # A half-written file with a name nobody reads is litter; one at the
        # destination is a deliverable. Only the first kind can be left behind,
        # and it is not left behind either.
        scratch.unlink(missing_ok=True)
