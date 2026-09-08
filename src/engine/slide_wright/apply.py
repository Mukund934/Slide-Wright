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
        part_applied = False
        for change in changes:
            try:
                ok, why = _apply_one(root, change)
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


def _apply_one(root, change: Change) -> tuple[bool, str]:
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
        if _set_run_format(shape, change):
            return True, ""
        return False, (f"run {change.target} not found, or it carries no explicit "
                       f"formatting to change")
    if change.op is Op.SET_FONT_SIZE:
        if _set_font_size(shape, float(change.after)):
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
      2. one paragraph holds it              — collapse into that paragraph
      3. the whole shape holds it            — collapse into the first run

    Steps 2 and 3 lose intra-run formatting inside the matched span, which is
    unavoidable when replacing text that spans differently formatted runs.
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

    # 3. The span crosses paragraphs; treat the shape as one text block.
    all_runs = [r for para in paragraphs for r in para.findall("a:r", NS)]
    return _replace_within(all_runs, before, after)


def _replace_within(runs, before: str, after: str) -> bool:
    """Replace `before` across a run sequence, writing the result into the first."""
    texts = [(r, r.find("a:t", NS)) for r in runs]
    texts = [(r, t) for r, t in texts if t is not None]
    if not texts:
        return False

    joined = "".join(t.text or "" for _, t in texts)
    if not before or before not in joined:
        return False

    replaced = joined.replace(before, after, 1)
    for index, (_, t) in enumerate(texts):
        t.text = replaced if index == 0 else ""
    return True


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
    for t in cell.findall(".//a:t", NS):
        if change.before is None or t.text == str(change.before):
            t.text = str(change.after)
            return True
    # Empty cell: write into the first run if one exists.
    first = cell.find(".//a:t", NS)
    if first is not None:
        first.text = str(change.after)
        return True
    return False


def _table_size(shape) -> tuple[int, int] | None:
    rows = shape.findall(".//a:tr", NS)
    if not rows:
        return None
    return len(rows), max(len(r.findall("a:tc", NS)) for r in rows)


def _set_font_size(shape, size_pt: float) -> bool:
    hundredths = str(int(round(size_pt * 100)))
    changed = False
    for rpr in shape.findall(".//a:rPr", NS):
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


def _set_run_format(shape, change: Change) -> bool:
    """Change one run's typeface or colour, touching nothing else.

    Targets are `<shape>/run/<index>`; a bare shape id applies to every run in
    the shape. Addressing a single run matters for conformance work: a text box
    where one word was pasted in the wrong font should come back with that word
    fixed and every other run byte-identical, which is what makes "we changed
    only what did not conform" a checkable claim rather than a slogan.
    """
    runs = _addressable_runs(shape)
    if not runs:
        return False

    parts = change.target.split("/")
    if len(parts) >= 3 and parts[1] == "run":
        try:
            index = int(parts[2])
        except ValueError:
            return False
        if not 0 <= index < len(runs):
            return False
        runs = [runs[index]]

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
