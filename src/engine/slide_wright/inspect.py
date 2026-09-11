"""Deterministic structural inspection of a deck.

Answers "what is actually on this slide" by reading OOXML, not by asking a
model. Structural facts must be computed: a model asked how many shapes are on
a slide will sometimes be wrong, and every downstream decision would inherit
that error.

The model's job starts where this ends — meaning, intent, narrative. Counting
is ours.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

from lxml import etree

from slide_wright.package import Package

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

EMU_PER_INCH = 914400
EMU_PER_POINT = 12700


@dataclass
class TextRun:
    text: str
    size_pt: float | None = None
    bold: bool = False
    italic: bool = False
    font: str | None = None
    color: str | None = None
    #: Underline and strikethrough as OOXML states them ("sng", "dbl",
    #: "sngStrike"...), and baseline as thousandths of a percent -- positive for
    #: superscript, negative for subscript.
    #:
    #: Kept as values rather than flattened to booleans the way `bold` and
    #: `italic` are, because a double underline becoming a single one is
    #: something a reader sees. The off states -- absent, "none", "noStrike",
    #: zero -- are all normalised to None: they render identically, so telling
    #: them apart would report a difference nobody can see.
    underline: str | None = None
    strike: str | None = None
    baseline: int | None = None
    #: Capitalisation as OOXML states it -- "all" or "small". A run set to ALL
    #: CAPS says one thing on the slide and another in the XML, so a text edit
    #: that moves its words into a neighbouring run changes what a reader sees
    #: while changing no character of the text. "none" normalises to None for
    #: the same reason the other off states do: it renders identically to an
    #: absent attribute, and every one of the 363 in the corpus is that.
    caps: str | None = None
    #: Which paragraph of the shape this run belongs to. Only `ShapeInfo.text`
    #: uses it, and only to know where one line ends and the next begins.
    paragraph: int = 0
    #: Where this run points, resolved through the slide's relationships. A
    #: hyperlink is carried by the run, so an edit that empties a run takes the
    #: link off the slide while leaving both the `a:hlinkClick` and the
    #: relationship in the package -- nothing downstream could see that, because
    #: nothing here recorded it.
    link: str | None = None


@dataclass
class ParagraphInfo:
    """How one line of a shape sits, as distinct from what it says.

    Four properties, chosen because they are the four a reviewer names out
    loud: *the bullet went*, *it is not indented any more*, *that is centred
    now*, *the lines are tighter*. Each is kept as OOXML states it, with the
    inherited case as None -- a paragraph with no `<a:pPr>` takes all of this
    from its layout, and recording a default here would report a change the
    first time one was written explicitly with the same value.

    Deliberately absent: `marL`/`indent`, which move a line the same way `lvl`
    does and would report the same change twice, and `spcBef`/`spcAft`, which
    no operation here can alter and which vary by a hundredth of a point
    through an ordinary save.
    """

    #: Outline depth. `lvl` is absent for the first level, which is level 0.
    level: int = 0
    #: `algn` -- "l", "ctr", "r", "just"...  None means inherited.
    alignment: str | None = None
    #: "none", "char <c>", or "auto <type>". None means inherited.
    bullet: str | None = None
    #: Line spacing as written: "90%" or "12pt". None means inherited.
    line_spacing: str | None = None


@dataclass
class ShapeInfo:
    """One object on a slide, described structurally."""

    id: str
    name: str
    kind: str  # shape | picture | table | chart | group | placeholder | connector
    placeholder_type: str | None = None
    x: int | None = None
    y: int | None = None
    cx: int | None = None
    cy: int | None = None
    rotation_deg: float | None = None
    geometry: str | None = None       # preset name, or "custom"
    runs: list[TextRun] = field(default_factory=list)
    table_rows: int = 0
    table_cols: int = 0
    # Cell text, row-major, addressed the way a change addresses it: "r1/c1".
    # Counting rows was enough to describe a table and not enough to edit one --
    # a caller could not read what a cell held, so it could not name the value
    # it was replacing, and a `numbers` lock could not see the figures inside.
    table_cells: dict[str, str] = field(default_factory=dict)
    child_count: int = 0
    #: Every `<a:p>` in the shape, including the ones holding no text.
    #:
    #: `runs` carries only runs with text, so a paragraph left empty is absent
    #: from `text` and from every comparison built on it. That is the right
    #: reading for "what does this shape say" and the wrong one for "what does
    #: this shape look like": an empty paragraph in a body placeholder still
    #: draws its bullet. Counting them is what lets the diff tell a shape whose
    #: four lines became one from a shape whose four lines became one line and
    #: three blanks.
    paragraphs: list[ParagraphInfo] = field(default_factory=list)
    # True when x/y/cx/cy came from the layout or master rather than the slide.
    # The shape really is there; it just has no position of its own, which is
    # why the applier refuses to move it.
    geometry_inherited: bool = False
    # How this placeholder addresses its slot, most specific first. Internal to
    # geometry resolution; None for anything that is not a placeholder.
    placeholder_key: list[str] | None = None

    @property
    def paragraph_count(self) -> int:
        return len(self.paragraphs)

    @property
    def text(self) -> str:
        """Everything the shape says, with its paragraphs kept apart.

        Runs were joined with nothing between them, so a bulleted list came back
        as one string with the bullets welded together: `"March 16,
        2023www.eia.gov/aeo"` is a real shape from a real deck. Every consumer
        inherited it -- `word_count` read that as one word, so the audit's
        density measures ran **12.4% low** across the corpus (9,712 words
        counted where 11,085 exist, over 227 shapes in 11 of 26 decks); the
        model summary sent the welded version; and a `before` a caller built
        from this text could only match by welding too.

        `planner.summarise` has always done `shape.text.replace("\n", " ")`,
        which is what the separator was supposed to need. `apply._set_text`
        joins the same way, because the two have to agree about what this string
        is for a `before` taken from one to be found by the other.
        """
        parts: list[str] = []
        previous: int | None = None
        for run in self.runs:
            if previous is not None and run.paragraph != previous:
                parts.append("\n")
            parts.append(run.text)
            previous = run.paragraph
        return "".join(parts)

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())

    @property
    def right(self) -> int | None:
        return None if self.x is None or self.cx is None else self.x + self.cx

    @property
    def bottom(self) -> int | None:
        return None if self.y is None or self.cy is None else self.y + self.cy

    def cell(self, row: int, col: int) -> str | None:
        """What a cell holds, or None if the table has no such cell."""
        return self.table_cells.get(f"r{row}/c{col}")

    def overlaps(self, other: ShapeInfo) -> bool:
        if None in (self.x, self.y, self.cx, self.cy, other.x, other.y, other.cx, other.cy):
            return False
        return not (
            self.right <= other.x
            or other.right <= self.x
            or self.bottom <= other.y
            or other.bottom <= self.y
        )


@dataclass
class SlideInfo:
    number: int
    part_name: str
    shapes: list[ShapeInfo] = field(default_factory=list)
    layout: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.shapes if s.has_text)

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def title(self) -> str | None:
        for s in self.shapes:
            if s.placeholder_type in {"title", "ctrTitle"} and s.has_text:
                return s.text.strip()
        return None

    def of_kind(self, kind: str) -> list[ShapeInfo]:
        return [s for s in self.shapes if s.kind == kind]


@dataclass
class DeckInfo:
    slide_width: int = 0
    slide_height: int = 0
    slides: list[SlideInfo] = field(default_factory=list)
    theme_fonts: dict[str, str] = field(default_factory=dict)

    @property
    def slide_count(self) -> int:
        return len(self.slides)

    def slide(self, number: int) -> SlideInfo | None:
        return next((s for s in self.slides if s.number == number), None)

    def all_shapes(self) -> Iterator[ShapeInfo]:
        for s in self.slides:
            yield from s.shapes


def inspect(pkg: Package | str) -> DeckInfo:
    """Read a package into a structural model."""
    pkg = pkg if isinstance(pkg, Package) else Package.open(pkg)
    deck = DeckInfo()

    if "ppt/presentation.xml" in pkg.parts:
        root = etree.fromstring(pkg.read("ppt/presentation.xml"))
        sz = root.find("p:sldSz", NS)
        if sz is not None:
            deck.slide_width = int(sz.get("cx", 0))
            deck.slide_height = int(sz.get("cy", 0))

    theme_part = next((n for n in pkg.parts if n.startswith("ppt/theme/theme")), None)
    if theme_part:
        troot = etree.fromstring(pkg.read(theme_part))
        for tag, key in (("majorFont", "major"), ("minorFont", "minor")):
            el = troot.find(f".//a:{tag}/a:latin", NS)
            if el is not None:
                deck.theme_fonts[key] = el.get("typeface", "")

    for part in pkg.slides():
        deck.slides.append(_read_slide(pkg, part.name, part.slide_number or 0))
    return deck


# ── slide parsing ────────────────────────────────────────────────────────────

def _related_part(pkg: Package, part_name: str, folder: str) -> str | None:
    """The part this one points at whose target lives in `folder`.

    Relationship targets are relative and may use either separator, so they are
    resolved by basename against the folder rather than by joining paths.
    """
    rels_name = part_name.replace(f"{_folder_of(part_name)}/", f"{_folder_of(part_name)}/_rels/") + ".rels"
    if rels_name not in pkg.parts:
        return None
    try:
        root = etree.fromstring(pkg.read(rels_name))
    except etree.XMLSyntaxError:
        return None
    for rel in root.iter(
        "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
    ):
        target = rel.get("Target", "").replace("\\", "/")
        if f"/{folder}/" not in f"/{target}" and not target.startswith(folder):
            continue
        candidate = f"ppt/{folder}/{target.split('/')[-1]}"
        if candidate in pkg.parts:
            return candidate
    return None


def _folder_of(part_name: str) -> str:
    return part_name.rsplit("/", 1)[0].rsplit("/", 1)[-1]


def _placeholder_geometry(pkg: Package, part_name: str) -> dict[str, tuple[int, int, int, int]]:
    """Placeholder positions declared by a layout or master, keyed by placeholder.

    Most placeholders on a real slide carry no `a:xfrm` of their own: they take
    position and size from the layout, and the layout from the master. Reading
    only the slide therefore reports `x=None` for the title of almost every
    professionally built deck.

    That was not merely a gap in what could be drawn. The quality gate skips any
    shape whose geometry is unknown, so **every inherited placeholder in every
    deck was exempt from the bounds and margin checks** -- and a title pushed off
    the canvas by its layout is exactly the arithmetic failure the gate exists
    to catch.

    Keyed by both `idx` and `type`, because a slide placeholder may name either.
    """
    found: dict[str, tuple[int, int, int, int]] = {}
    if part_name not in pkg.parts:
        return found
    try:
        root = etree.fromstring(pkg.read(part_name))
    except etree.XMLSyntaxError:
        return found

    tree = root.find(".//p:cSld/p:spTree", NS)
    if tree is None:
        return found

    for el in tree:
        ph = el.find(".//p:nvSpPr/p:nvPr/p:ph", NS)
        if ph is None:
            continue
        xfrm = el.find(".//a:xfrm", NS)
        if xfrm is None:
            continue
        off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
        if off is None or ext is None:
            continue
        box = (
            int(off.get("x", 0)), int(off.get("y", 0)),
            int(ext.get("cx", 0)), int(ext.get("cy", 0)),
        )
        for key in _placeholder_keys(ph.get("type"), ph.get("idx")):
            found.setdefault(key, box)
    return found


# `title` and `ctrTitle` are the same slot wearing two names: a slide's centred
# title inherits from a layout that calls it plain `title`, and matching them
# literally would leave every title deck-wide unresolved.
_TITLE_TYPES = {"title", "ctrTitle"}


def _placeholder_keys(ph_type: str | None, idx: str | None) -> list[str]:
    keys = []
    if idx is not None:
        keys.append(f"idx:{idx}")
    kind = ph_type or "body"
    keys.append(f"type:{kind}")
    if kind in _TITLE_TYPES:
        keys.append("type:title*")
    return keys


def _layout_for(pkg: Package, part_name: str) -> str | None:
    """Which slide layout this slide is built on.

    `SlideInfo.layout` was declared from the start and never populated, so it
    read as None on every slide of every deck -- a field that quietly answers
    "no information" to every question is worse than an absent one, because
    callers believe they checked.

    It is worth having: a slide pasted in from another deck usually carries a
    different layout, which makes this the cheapest signal there is for finding
    the parts of a deck that came from somewhere else.
    """
    rels_name = part_name.replace("slides/", "slides/_rels/") + ".rels"
    if rels_name not in pkg.parts:
        return None
    try:
        root = etree.fromstring(pkg.read(rels_name))
    except etree.XMLSyntaxError:
        return None

    for rel in root.iter(
        "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
    ):
        target = rel.get("Target", "")
        if "slideLayout" not in target:
            continue
        resolved = target.replace("\\", "/").split("/")[-1]
        layout_part = f"ppt/slideLayouts/{resolved}"
        if layout_part not in pkg.parts:
            return resolved
        # Prefer the layout's declared name over its filename: slideLayout7
        # says nothing, "Title and Content" says what the slide was built on.
        try:
            layout = etree.fromstring(pkg.read(layout_part))
        except etree.XMLSyntaxError:
            return resolved
        name = layout.find(".//p:cSld", NS)
        if name is not None and name.get("name"):
            return name.get("name")
        return resolved
    return None


def _read_slide(pkg: Package, part_name: str, number: int) -> SlideInfo:
    slide = SlideInfo(number=number, part_name=part_name)
    slide.layout = _layout_for(pkg, part_name)
    root = etree.fromstring(pkg.read(part_name))
    tree = root.find(".//p:cSld/p:spTree", NS)
    if tree is None:
        return slide
    links = _hyperlink_targets(pkg, part_name)
    for el in tree:
        info = _read_shape(el, links)
        if info is not None:
            slide.shapes.append(info)
    _inherit_geometry(pkg, part_name, slide)
    return slide


def _inherit_geometry(pkg: Package, part_name: str, slide: SlideInfo) -> None:
    """Fill in placeholder geometry the slide did not state itself.

    Resolution follows OOXML: the slide's own `a:xfrm` wins, then the layout's,
    then the master's. Only shapes that stated nothing are touched.

    Inherited values are marked. The distinction is load-bearing: the applier
    refuses to move a shape that has no position of its own, and a caller that
    could not tell the difference would propose a move, get it approved, and
    have it fail at apply time.
    """
    unresolved = [
        shape for shape in slide.shapes
        if shape.x is None and shape.placeholder_key is not None
    ]
    if not unresolved:
        return

    layout_part = _related_part(pkg, part_name, "slideLayouts")
    sources = []
    if layout_part:
        sources.append(_placeholder_geometry(pkg, layout_part))
        master_part = _related_part(pkg, layout_part, "slideMasters")
        if master_part:
            sources.append(_placeholder_geometry(pkg, master_part))

    for shape in unresolved:
        for source in sources:
            box = next(
                (source[key] for key in shape.placeholder_key if key in source), None
            )
            if box is None:
                continue
            shape.x, shape.y, shape.cx, shape.cy = box
            shape.geometry_inherited = True
            break


def _paragraph(para) -> ParagraphInfo:
    """Read one paragraph's properties, leaving anything inherited as None."""
    info = ParagraphInfo()
    pPr = para.find("a:pPr", NS)
    if pPr is None:
        return info

    level = pPr.get("lvl")
    if level and level.isdigit():
        info.level = int(level)
    info.alignment = pPr.get("algn")

    if pPr.find("a:buNone", NS) is not None:
        info.bullet = "none"
    else:
        char = pPr.find("a:buChar", NS)
        auto = pPr.find("a:buAutoNum", NS)
        if char is not None:
            info.bullet = f"char {char.get('char', '')}"
        elif auto is not None:
            info.bullet = f"auto {auto.get('type', '')}"

    spacing = pPr.find("a:lnSpc", NS)
    if spacing is not None:
        percent = spacing.find("a:spcPct", NS)
        points = spacing.find("a:spcPts", NS)
        if percent is not None and percent.get("val"):
            info.line_spacing = f"{int(percent.get('val')) / 1000:g}%"
        elif points is not None and points.get("val"):
            info.line_spacing = f"{int(points.get('val')) / 100:g}pt"
    return info


def _read_cells(rows) -> dict[str, str]:
    """Cell text keyed as the applier addresses it.

    Indices are zero-based to match `_set_table_cell`, which reads them straight
    off the row and cell lists. Getting that wrong would be silent: every read
    would be off by one row, and a `before` derived from it would never match.
    """
    cells: dict[str, str] = {}
    for row_index, row in enumerate(rows):
        for col_index, cell in enumerate(row.findall("a:tc", NS)):
            cells[f"r{row_index}/c{col_index}"] = "".join(
                t.text or "" for t in cell.findall(".//a:t", NS)
            )
    return cells


def _off(value: str | None, none_word: str) -> str | None:
    """An OOXML on/off enumeration, with every way of saying "off" reading None."""
    return None if value in (None, "", none_word) else value


def _hyperlink_targets(pkg: Package, part_name: str) -> dict[str, str]:
    """`rId` to what it points at, for this slide's relationships.

    An unresolvable id is kept as the id: knowing a run links *somewhere* and
    not knowing where is still worth more than not knowing it links at all.
    """
    rels_name = part_name.replace("slides/", "slides/_rels/") + ".rels"
    if rels_name not in pkg.parts:
        return {}
    try:
        root = etree.fromstring(pkg.read(rels_name))
    except etree.XMLSyntaxError:
        return {}
    return {
        rel.get("Id"): rel.get("Target", "")
        for rel in root.iter(
            "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
        )
        if rel.get("Id")
    }


def _read_shape(el, links: dict[str, str] | None = None) -> ShapeInfo | None:
    tag = etree.QName(el).localname
    kind = {
        "sp": "shape",
        "pic": "picture",
        "graphicFrame": "frame",
        "grpSp": "group",
        "cxnSp": "connector",
    }.get(tag)
    if kind is None:
        return None

    nv = el.find(f".//p:{'nvGrpSpPr' if tag == 'grpSp' else 'nvSpPr'}/p:cNvPr", NS)
    if nv is None:
        nv = el.find(".//p:cNvPr", NS)
    shape = ShapeInfo(
        id=(nv.get("id") if nv is not None else ""),
        name=(nv.get("name") if nv is not None else ""),
        kind=kind,
    )

    ph = el.find(".//p:nvSpPr/p:nvPr/p:ph", NS)
    if ph is not None:
        shape.placeholder_type = ph.get("type", "body")
        shape.kind = "placeholder"
        shape.placeholder_key = _placeholder_keys(ph.get("type"), ph.get("idx"))

    # A graphicFrame -- every native table and chart -- states its box as
    # `p:xfrm`, not `a:xfrm`. Looking only for the DrawingML form left the two
    # object types this product exists to preserve with no position at all: off
    # the canvas preview, and exempt from the gate's bounds check.
    xfrm = el.find(".//a:xfrm", NS)
    if xfrm is None:
        xfrm = el.find("./p:xfrm", NS)
    if xfrm is not None:
        off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
        if off is not None:
            shape.x, shape.y = int(off.get("x", 0)), int(off.get("y", 0))
        if ext is not None:
            shape.cx, shape.cy = int(ext.get("cx", 0)), int(ext.get("cy", 0))
        rot = xfrm.get("rot")
        if rot:
            shape.rotation_deg = int(rot) / 60000.0

    prst = el.find(".//a:prstGeom", NS)
    if prst is not None:
        shape.geometry = prst.get("prst")
    elif el.find(".//a:custGeom", NS) is not None:
        shape.geometry = "custom"

    if kind == "frame":
        if el.find(".//a:tbl", NS) is not None:
            shape.kind = "table"
            rows = el.findall(".//a:tr", NS)
            shape.table_rows = len(rows)
            shape.table_cols = len(rows[0].findall("a:tc", NS)) if rows else 0
            shape.table_cells = _read_cells(rows)
        elif el.find(".//*[@uri='http://schemas.openxmlformats.org/drawingml/2006/chart']") is not None:
            shape.kind = "chart"
        elif el.find(".//*[@uri='http://schemas.openxmlformats.org/drawingml/2006/diagram']") is not None:
            shape.kind = "smartart"

    if kind == "group":
        shape.child_count = sum(
            1 for c in el if etree.QName(c).localname in {"sp", "pic", "grpSp", "graphicFrame", "cxnSp"}
        )

    paragraphs = el.findall(".//a:p", NS)
    shape.paragraphs = [_paragraph(para) for para in paragraphs]
    for index, para in enumerate(paragraphs):
        for r in para.findall(".//a:r", NS):
            t = r.find("a:t", NS)
            if t is None or not t.text:
                continue
            rpr = r.find("a:rPr", NS)
            run = TextRun(text=t.text, paragraph=index)
            if rpr is not None:
                sz = rpr.get("sz")
                run.size_pt = int(sz) / 100.0 if sz else None
                run.bold = rpr.get("b") == "1"
                run.italic = rpr.get("i") == "1"
                latin = rpr.find("a:latin", NS)
                if latin is not None:
                    run.font = latin.get("typeface")
                clr = rpr.find(".//a:srgbClr", NS)
                if clr is not None:
                    run.color = clr.get("val")
                run.caps = _off(rpr.get("cap"), "none")
                run.underline = _off(rpr.get("u"), "none")
                run.strike = _off(rpr.get("strike"), "noStrike")
                baseline = rpr.get("baseline")
                if baseline and baseline.lstrip("-").isdigit() and int(baseline) != 0:
                    run.baseline = int(baseline)
                hlink = rpr.find("a:hlinkClick", NS)
                if hlink is not None:
                    rid = hlink.get(f"{{{NS['r']}}}id")
                    if rid:
                        run.link = (links or {}).get(rid, rid)
            shape.runs.append(run)

    return shape
