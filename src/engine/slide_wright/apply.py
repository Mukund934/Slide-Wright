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

import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from slide_wright.changeset import Change, ChangeSet, Op, Status
from slide_wright.inspect import NS
from slide_wright.package import Package
from slide_wright.smartart import SmartArtUnsupported, assert_preserved, guard_edit

# Operations this module can perform in place, without a slide rebuild.
IN_PLACE_OPS = {Op.SET_TEXT, Op.SET_TABLE_CELL, Op.SET_FONT_SIZE, Op.MOVE, Op.RESIZE}


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

    # SmartArt is refused up front, before anything is written. A diagram is
    # four correlated parts plus a drawing cache; editing one out of step with
    # the others silently renders a stale diagram. See smartart.py.
    for change in approved:
        try:
            guard_edit(pkg, change.slide, change.target)
        except SmartArtUnsupported as exc:
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

    # Even when nothing targeted a diagram, prove none was collateral damage.
    try:
        assert_preserved(pkg, output)
    except SmartArtUnsupported as exc:
        raise ApplyError(str(exc)) from exc

    return result


# ── per-operation handlers ───────────────────────────────────────────────────

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

    if change.op is Op.SET_TEXT:
        if _set_text(shape, str(change.before), str(change.after)):
            return True, ""
        return False, (f"shape {shape_id} does not contain the text "
                       f"{str(change.before)!r}")
    if change.op is Op.SET_TABLE_CELL:
        if _set_table_cell(shape, change):
            return True, ""
        return False, f"cell {change.target} does not hold {str(change.before)!r}"
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


def _set_font_size(shape, size_pt: float) -> bool:
    hundredths = str(int(round(size_pt * 100)))
    changed = False
    for rpr in shape.findall(".//a:rPr", NS):
        rpr.set("sz", hundredths)
        changed = True
    return changed


def _set_geometry(shape, change: Change) -> bool:
    xfrm = shape.find(".//a:xfrm", NS)
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


def _write_package(source: Path, output: Path, patched: dict[str, bytes]) -> None:
    """Copy the package, substituting only the patched parts.

    Every other part is copied byte-for-byte from the source, which is what
    makes the fidelity claim hold by construction rather than by hope.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    if not patched:
        shutil.copy(source, output)
        return
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(
        output, "w", zipfile.ZIP_DEFLATED
    ) as zout:
        for info in zin.infolist():
            data = patched.get(info.filename)
            zout.writestr(info.filename, data if data is not None else zin.read(info.filename))
