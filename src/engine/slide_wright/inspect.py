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
    child_count: int = 0

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())

    @property
    def right(self) -> int | None:
        return None if self.x is None or self.cx is None else self.x + self.cx

    @property
    def bottom(self) -> int | None:
        return None if self.y is None or self.cy is None else self.y + self.cy

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

def _read_slide(pkg: Package, part_name: str, number: int) -> SlideInfo:
    slide = SlideInfo(number=number, part_name=part_name)
    root = etree.fromstring(pkg.read(part_name))
    tree = root.find(".//p:cSld/p:spTree", NS)
    if tree is None:
        return slide
    for el in tree:
        info = _read_shape(el)
        if info is not None:
            slide.shapes.append(info)
    return slide


def _read_shape(el) -> ShapeInfo | None:
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

    xfrm = el.find(".//a:xfrm", NS)
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
        elif el.find(".//*[@uri='http://schemas.openxmlformats.org/drawingml/2006/chart']") is not None:
            shape.kind = "chart"
        elif el.find(".//*[@uri='http://schemas.openxmlformats.org/drawingml/2006/diagram']") is not None:
            shape.kind = "smartart"

    if kind == "group":
        shape.child_count = sum(
            1 for c in el if etree.QName(c).localname in {"sp", "pic", "grpSp", "graphicFrame", "cxnSp"}
        )

    for r in el.findall(".//a:r", NS):
        t = r.find("a:t", NS)
        if t is None or not t.text:
            continue
        rpr = r.find("a:rPr", NS)
        run = TextRun(text=t.text)
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
        shape.runs.append(run)

    return shape
