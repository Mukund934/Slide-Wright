"""Native charts, and why their numbers are read-only.

A chart in a `.pptx` is two representations of the same numbers:

```
ppt/charts/chart1.xml                    the cached series -- what is drawn
ppt/embeddings/Microsoft_Excel_*.xlsx    the authored workbook -- what "Edit Data" opens
ppt/charts/_rels/chart1.xml.rels         the link between them
```

This is the same hazard as SmartArt (ADR-0007) wearing different clothes. Change
the cached values and the picture updates while the workbook still holds the old
numbers; change the workbook alone and nothing visible happens until someone
refreshes. Either way the file quietly disagrees with itself, and the disagreement
surfaces in the worst possible place -- when a reviewer clicks "Edit Data" in
front of the people the deck was made for.

Editing a chart's numbers is exactly the operation where being quietly wrong is
most expensive, so this module makes the refusal explicit rather than incidental.

**What was already true, and is now checked:** editing anything else leaves charts
untouched. Measured on a 350-part deck carrying 29 charts and 29 linked
workbooks -- one text edit changed one slide part and left every chart, every
workbook and every link byte-identical.

What this does *not* do is stop the numbers being read. `values()` returns the
cached series so that an audit can compare a chart against a slide's prose, which
is a genuinely useful thing to know and carries no risk of corrupting anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from slide_wright.package import Package

C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {
    "c": C_NS,
    "r": REL_NS,
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}

CHART_PART = re.compile(r"^ppt/charts/chart(\d+)\.xml$")
SLIDE_PART = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


class ChartUnsupported(Exception):
    """A chart edit was refused. Better than a deck that disagrees with itself."""


@dataclass
class Chart:
    """One native chart on one slide, with its data link."""

    slide: int
    shape_id: str
    part: str                       # ppt/charts/chartN.xml
    workbook: str | None = None     # ppt/embeddings/*.xlsx, only if the part exists
    series_count: int = 0
    value_count: int = 0
    malformed: bool = False
    dangling_link: bool = False     # a relationship pointing at a missing part
    parts: list[str] = field(default_factory=list)

    @property
    def has_data_link(self) -> bool:
        """Whether "Edit Data" would actually find a workbook to open.

        Deliberately requires the part to be present, not merely referenced. A
        relationship pointing at a workbook that is gone leaves the picture
        intact and the data unreachable, which is the exact failure this class
        exists to catch -- so a dangling reference must not read as a link.
        """
        return self.workbook is not None


def _parse(pkg: Package, part: str):
    try:
        return etree.fromstring(pkg.read(part))
    except etree.XMLSyntaxError as exc:
        raise ChartUnsupported(f"{part} is not parseable XML: {exc}") from exc


def find_all(pkg: Package | str) -> list[Chart]:
    """Every native chart in the package, matched to its slide and workbook.

    Tolerant of damage: a chart part that will not parse is reported with
    `malformed=True` rather than raising, because a corrupted chart is
    information the caller needs, not an error that should hide it.
    """
    pkg = pkg if isinstance(pkg, Package) else Package.open(pkg)
    charts: list[Chart] = []

    for name in sorted(pkg.parts):
        match = SLIDE_PART.match(name)
        if not match:
            continue
        slide_number = int(match.group(1))
        rels = _read_rels(pkg, f"ppt/slides/_rels/slide{slide_number}.xml.rels")

        try:
            root = etree.fromstring(pkg.read(name))
        except etree.XMLSyntaxError:
            continue

        for frame in root.iter(f"{{{NS['p']}}}graphicFrame"):
            ref = frame.find(f".//{{{C_NS}}}chart")
            if ref is None:
                continue
            rel_id = ref.get(f"{{{REL_NS}}}id")
            part = _resolve(rels.get(rel_id, "")) if rel_id else ""
            if not part or part not in pkg.parts:
                continue

            cnv = frame.find(f".//{{{NS['p']}}}cNvPr")
            charts.append(_describe(pkg, slide_number,
                                    cnv.get("id", "") if cnv is not None else "",
                                    part))
    return charts


def _describe(pkg: Package, slide: int, shape_id: str, part: str) -> Chart:
    chart = Chart(slide=slide, shape_id=shape_id, part=part, parts=[part])

    chart_rels = f"ppt/charts/_rels/{part.rsplit('/', 1)[1]}.rels"
    if chart_rels in pkg.parts:
        chart.parts.append(chart_rels)
        for target in _read_rels(pkg, chart_rels).values():
            resolved = _resolve(target)
            if "embeddings" not in resolved:
                continue
            if resolved in pkg.parts:
                chart.workbook = resolved
                chart.parts.append(resolved)
            else:
                chart.dangling_link = True

    try:
        root = _parse(pkg, part)
    except ChartUnsupported:
        chart.malformed = True
        return chart

    chart.series_count = len(root.findall(f".//{{{C_NS}}}ser"))
    chart.value_count = len(root.findall(f".//{{{C_NS}}}v"))
    return chart


def values(pkg: Package | str, part: str) -> list[str]:
    """The cached values a chart draws, in document order.

    Read-only, and useful: an audit can ask whether a slide's prose agrees with
    the chart beside it without any risk of changing either.
    """
    pkg = pkg if isinstance(pkg, Package) else Package.open(pkg)
    root = _parse(pkg, part)
    return [v.text or "" for v in root.findall(f".//{{{C_NS}}}v")]


def census(pkg: Package | str) -> dict:
    """Counts that must not fall through an edit."""
    pkg = pkg if isinstance(pkg, Package) else Package.open(pkg)
    charts = find_all(pkg)
    return {
        "charts": len(charts),
        "chart_parts": len([n for n in pkg.parts if CHART_PART.match(n)]),
        "workbooks": len([c for c in charts if c.has_data_link]),
        "series": sum(c.series_count for c in charts),
        "values": sum(c.value_count for c in charts),
        "malformed": any(c.malformed for c in charts),
        "dangling_links": sum(1 for c in charts if c.dangling_link),
    }


def assert_preserved(source: Package | str, output: Package | str) -> None:
    """Every chart, its workbook and its data link must survive an edit.

    A chart that loses its workbook still *looks* right -- the cached picture is
    intact -- and only fails when someone tries to edit the data. That is a
    silent loss, so it is checked rather than assumed.
    """
    before, after = census(source), census(output)
    for key in ("charts", "chart_parts", "workbooks", "series", "values"):
        if after[key] < before[key]:
            raise ChartUnsupported(
                f"chart integrity lost: {key} went from {before[key]} to "
                f"{after[key]}. Refusing to deliver this deck."
            )
    if after["dangling_links"] > before["dangling_links"]:
        raise ChartUnsupported(
            f"chart data link broken: {after['dangling_links']} chart(s) now "
            "reference a workbook that is not in the package. The picture "
            "still renders, so this would only surface when someone opens "
            "'Edit Data'. Refusing to deliver this deck."
        )


def guard_edit(pkg: Package | str, slide: int, shape_id: str) -> None:
    """Refuse an edit targeting a chart, and say why.

    Without this the refusal still happens, but for the wrong reason and with a
    misleading message: the applier looks for the text on the *slide*, does not
    find it, and reports that the shape does not contain it. The value is in the
    deck -- it is in the chart part -- so that message sends a reviewer looking
    for something that is demonstrably there.
    """
    target = shape_id.split("/")[0]
    for chart in find_all(pkg):
        if chart.slide == slide and chart.shape_id == target:
            link = (f" linked to {chart.workbook.rsplit('/', 1)[1]}"
                    if chart.workbook else " with no data link")
            raise ChartUnsupported(
                f"shape {target} on slide {slide} is a native chart "
                f"({chart.series_count} series, {chart.value_count} values"
                f"{link}). Editing chart data is not supported: the cached "
                "series and the embedded workbook must change together, and a "
                "deck whose picture disagrees with its own 'Edit Data' is worse "
                "than one that was never edited. Change it in PowerPoint, or "
                "edit the surrounding text instead."
            )


def _read_rels(pkg: Package, rels_name: str) -> dict[str, str]:
    if rels_name not in pkg.parts:
        return {}
    try:
        root = etree.fromstring(pkg.read(rels_name))
    except etree.XMLSyntaxError:
        return {}
    return {
        rel.get("Id", ""): rel.get("Target", "")
        for rel in root.iter("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship")
    }


def _resolve(target: str) -> str:
    """Turn a relationship target into a package part name."""
    cleaned = target.replace("\\", "/")
    while cleaned.startswith("../"):
        cleaned = cleaned[3:]
    if cleaned.startswith("/"):
        return cleaned[1:]
    return cleaned if cleaned.startswith("ppt/") else f"ppt/{cleaned}"
