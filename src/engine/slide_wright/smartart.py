"""SmartArt: detect it, read it, and refuse to break it.

SmartArt is the hardest construct in a PowerPoint package and the one most
likely to be silently destroyed. A single diagram is **four correlated parts**
plus a drawing cache:

    ppt/diagrams/data1.xml     the authored model — nodes, text, relationships
    ppt/diagrams/layout1.xml   the layout algorithm
    ppt/diagrams/quickStyle1.xml
    ppt/diagrams/colors1.xml
    ppt/diagrams/drawing1.xml  a cached rendering PowerPoint may regenerate

The slide references them through a `<dgm:relIds>` element carrying four
relationship ids at once. Edit any one part out of step with the others and
PowerPoint either repairs the file, silently re-renders from a stale cache, or
flattens the diagram to a picture — and the user's org chart quietly becomes an
image of last quarter's org chart.

So this module's job is mostly to say **no, precisely**. It reads the structure
so we can prove a diagram survived a round-trip, and it refuses edits we cannot
guarantee. `SmartArtUnsupported` is a feature: an honest refusal beats a
plausible-looking corruption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from slide_wright.package import Package

DGM_NS = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "dgm": DGM_NS,
    "r": REL_NS,
}

DIAGRAM_PART = re.compile(r"^ppt/diagrams/(data|layout|quickStyle|colors|drawing)(\d+)\.xml$")
SLIDE_PART = re.compile(r"^ppt/slides/slide(\d+)\.xml$")

# The relationship-id attributes a slide uses to bind all four parts at once.
REL_ATTRS = ("dm", "lo", "qs", "cs")


class SmartArtUnsupported(Exception):
    """This diagram cannot be changed safely. Refuse rather than approximate."""


@dataclass
class DiagramNode:
    """One node of the authored model — a box in the org chart."""

    model_id: str
    text: str
    depth: int = 0

    def __repr__(self) -> str:
        return f"DiagramNode({self.text!r}, depth={self.depth})"


@dataclass
class SmartArt:
    """One SmartArt graphic, as it exists in the package."""

    slide: int
    shape_id: str
    shape_name: str = ""
    data_part: str = ""
    layout_part: str = ""
    style_part: str = ""
    colors_part: str = ""
    drawing_part: str = ""
    nodes: list[DiagramNode] = field(default_factory=list)
    malformed: bool = False   # the data part exists but could not be parsed

    @property
    def parts(self) -> list[str]:
        """Every package part this diagram depends on."""
        return [
            p for p in (self.data_part, self.layout_part, self.style_part,
                        self.colors_part, self.drawing_part) if p
        ]

    @property
    def text(self) -> list[str]:
        return [n.text for n in self.nodes if n.text.strip()]

    @property
    def complete(self) -> bool:
        """Usable: all four required parts present, and the model readable."""
        return not self.malformed and all(
            (self.data_part, self.layout_part, self.style_part, self.colors_part)
        )

    @property
    def has_drawing_cache(self) -> bool:
        """A cached rendering. If it goes stale, the slide shows the old diagram."""
        return bool(self.drawing_part)

    def describe(self) -> str:
        state = "complete" if self.complete else "INCOMPLETE"
        cache = ", cached drawing" if self.has_drawing_cache else ""
        return (
            f"SmartArt on slide {self.slide} ({self.shape_name or self.shape_id}): "
            f"{len(self.nodes)} node(s), {len(self.parts)} parts, {state}{cache}"
        )


def find_all(pkg: Package | str) -> list[SmartArt]:
    """Every SmartArt graphic in a package, with its parts and authored text."""
    pkg = pkg if isinstance(pkg, Package) else Package.open(pkg)
    diagrams: list[SmartArt] = []

    for part in pkg.slides():
        slide_no = part.slide_number or 0
        rels_name = f"ppt/slides/_rels/{part.name.split('/')[-1]}.rels"
        rels = _read_rels(pkg, rels_name)

        root = etree.fromstring(pkg.read(part.name))
        for frame in root.iter(f"{{{NS['p']}}}graphicFrame"):
            rel_ids = frame.find(f".//{{{DGM_NS}}}relIds")
            if rel_ids is None:
                continue

            nv = frame.find(".//p:cNvPr", NS)
            art = SmartArt(
                slide=slide_no,
                shape_id=(nv.get("id") if nv is not None else ""),
                shape_name=(nv.get("name") if nv is not None else ""),
            )
            for attr, field_name in zip(
                REL_ATTRS, ("data_part", "layout_part", "style_part", "colors_part")
            ):
                rid = rel_ids.get(f"{{{REL_NS}}}{attr}")
                if rid and rid in rels:
                    setattr(art, field_name, rels[rid])

            if art.data_part and art.data_part in pkg.parts:
                try:
                    art.nodes = read_nodes(pkg, art.data_part)
                except SmartArtUnsupported:
                    # Detection must survive a corrupted diagram so the damage
                    # can be *reported*. Raising here would turn "this file is
                    # broken" into an unhandled error far from the deck.
                    art.malformed = True
                art.drawing_part = _drawing_for(pkg, art.data_part)

            diagrams.append(art)

    return diagrams


def read_nodes(pkg: Package, data_part: str) -> list[DiagramNode]:
    """Read the authored text model from `data1.xml`.

    The authored text lives in `<dgm:pt>` points, not in the drawing cache. A
    tool that reads the cache is reading a screenshot of the truth.

    Runs are joined without a separator (they are one paragraph split by
    formatting) but paragraphs are joined with a newline. Without that, an org
    chart node reading "Manager" / "Second para" comes back as the single word
    "ManagerSecond para" — measured on LibreOffice's smartart-org-chart fixture.
    """
    root = _parse(pkg, data_part)
    nodes = []
    for pt in root.iter(f"{{{DGM_NS}}}pt"):
        # Only real content points carry text; presentation points do not.
        if pt.get("type") not in (None, "node", "asst"):
            continue
        paragraphs = [
            "".join(t.text or "" for t in para.iter(f"{{{NS['a']}}}t"))
            for para in pt.iter(f"{{{NS['a']}}}p")
        ]
        text = "\n".join(p for p in paragraphs if p).strip()
        if not text:
            continue
        nodes.append(DiagramNode(model_id=pt.get("modelId", ""), text=text))
    return nodes


def _parse(pkg: Package, part: str):
    """Parse a diagram part, treating malformed XML as damage rather than a crash.

    A corrupted `data1.xml` is exactly the outcome we exist to catch. Letting
    lxml raise here would surface it as an unhandled parser error somewhere far
    from the deck, instead of as "this diagram was damaged".
    """
    try:
        return etree.fromstring(pkg.read(part))
    except etree.XMLSyntaxError as exc:
        raise SmartArtUnsupported(
            f"diagram part {part} is not well-formed XML: {exc}"
        ) from None


def count_points(pkg: Package, data_part: str) -> int:
    """Every `<dgm:pt>` in a diagram, text-bearing or not.

    Structure and text are separate things. `poi-smartart.pptx` carries 26
    points and no text at all — a diagram of empty boxes. Verifying only text
    would let that whole diagram be destroyed without a single assertion
    firing, so structure is counted in its own right.
    """
    return len(list(_parse(pkg, data_part).iter(f"{{{DGM_NS}}}pt")))


def census(pkg: Package | str) -> dict:
    """Counts for verification — cheap enough to assert on every edit."""
    pkg = pkg if isinstance(pkg, Package) else Package.open(pkg)
    parts = [n for n in pkg.parts if DIAGRAM_PART.match(n)]
    diagrams = find_all(pkg)
    points, malformed = 0, 0
    for d in diagrams:
        if d.malformed:
            malformed += 1
            continue
        if not (d.data_part and d.data_part in pkg.parts):
            continue
        try:
            points += count_points(pkg, d.data_part)
        except SmartArtUnsupported:
            malformed += 1
    return {
        "diagrams": len(diagrams),
        "diagram_parts": len(parts),
        "nodes": sum(len(d.nodes) for d in diagrams),
        "points": points,
        "incomplete": sum(1 for d in diagrams if not d.complete),
        "malformed": malformed,
    }


def assert_preserved(source: Package | str, output: Package | str) -> None:
    """Raise unless every diagram survived intact.

    Called after any edit to a deck containing SmartArt. Compares the authored
    model, not the drawing cache — a diagram whose cache survived while its data
    was lost still counts as destroyed.
    """
    before, after = census(source), census(output)
    problems = []
    for key in ("diagrams", "diagram_parts", "nodes", "points"):
        if after[key] < before[key]:
            problems.append(f"{key}: {before[key]} -> {after[key]}")
    if after["incomplete"] > before["incomplete"]:
        problems.append(
            f"incomplete diagrams: {before['incomplete']} -> {after['incomplete']}"
        )
    if problems:
        raise SmartArtUnsupported(
            "SmartArt was damaged by this operation: " + "; ".join(problems)
        )


def guard_edit(pkg: Package | str, slide: int, shape_id: str) -> None:
    """Refuse an edit that targets a SmartArt graphic.

    We can read a diagram and prove it survived a round-trip. We cannot yet
    rewrite one safely: changing a node means editing `data1.xml` while keeping
    `drawing1.xml` consistent, and a stale cache renders the *old* diagram with
    no error anywhere. Until that is proven, this refuses.

    Raises `SmartArtUnsupported`, which the CLI surfaces as a refusal rather
    than a crash.
    """
    target = shape_id.split("/")[0]
    for art in find_all(pkg):
        if art.slide == slide and art.shape_id == target:
            raise SmartArtUnsupported(
                f"shape {target} on slide {slide} is a SmartArt graphic "
                f"({len(art.nodes)} nodes across {len(art.parts)} parts). "
                "Editing SmartArt is not supported: the authored model and the "
                "drawing cache must stay in step, and we cannot yet guarantee "
                "that. Edit it in PowerPoint, or ask for a change elsewhere."
            )


def _read_rels(pkg: Package, rels_name: str) -> dict[str, str]:
    """Map relationship ids to package part names for one slide."""
    if rels_name not in pkg.parts:
        return {}
    root = etree.fromstring(pkg.read(rels_name))
    out = {}
    for rel in root:
        rid, target = rel.get("Id"), rel.get("Target")
        if not rid or not target:
            continue
        out[rid] = _resolve(target)
    return out


def _resolve(target: str) -> str:
    """`../diagrams/data1.xml` -> `ppt/diagrams/data1.xml`."""
    target = target.lstrip("/")
    while target.startswith("../"):
        target = target[3:]
    return target if target.startswith("ppt/") else f"ppt/{target}"


def _drawing_for(pkg: Package, data_part: str) -> str:
    """Find the drawing cache paired with a data part, via its own rels."""
    name = data_part.split("/")[-1]
    rels_name = f"ppt/diagrams/_rels/{name}.rels"
    for target in _read_rels(pkg, rels_name).values():
        if "drawing" in target and target in pkg.parts:
            return target
    # Fall back to the conventional pairing: data1.xml -> drawing1.xml
    m = DIAGRAM_PART.match(data_part)
    if m:
        candidate = f"ppt/diagrams/drawing{m.group(2)}.xml"
        if candidate in pkg.parts:
            return candidate
    return ""
