"""Compare two packages and say exactly what changed.

This is the module the product promise reduces to. "We changed only what you
asked" is not a claim about intent; it is a claim about which parts of the
output differ from the source, and that is arithmetic.

Deliberately dumb: it knows nothing about slides, shapes or intent. It reports
differences. Deciding whether a difference was *permitted* is `verify`'s job,
because that requires knowing what was requested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from slide_wright.package import (
    CHART_PART,
    DIAGRAM_PART,
    EMBEDDING_PART,
    MEDIA_PART,
    Package,
)


@dataclass(frozen=True)
class PartDelta:
    """One part's fate between source and output."""

    name: str
    status: str  # "identical" | "changed" | "removed" | "added"
    source_sha: str | None = None
    output_sha: str | None = None

    @property
    def slide_number(self) -> int | None:
        """The slide this part *is*. Only ever the body."""
        m = re.match(r"^ppt/slides/slide(\d+)\.xml$", self.name)
        return int(m.group(1)) if m else None

    @property
    def slide_owner(self) -> int | None:
        """The slide this part belongs to: its body, or its relationships.

        A slide's rels are not a separate document. They are where its pictures,
        charts and hyperlinks are; empty that file and the slide renders as a
        page of broken frames while the slide part itself stays byte-identical.

        Attributed here rather than checked separately, because attribution is
        what the whole report is built on. Left as an ordinary non-slide part it
        was listed under "other parts changed (review)" -- alongside a chart
        workbook that moved because the user asked it to -- and did not block.
        """
        if (n := self.slide_number) is not None:
            return n
        m = re.match(r"^ppt/slides/_rels/slide(\d+)\.xml\.rels$", self.name)
        return int(m.group(1)) if m else None


@dataclass
class NativeObjectCensus:
    """Counts of the objects whose loss would be silent and expensive.

    ADR-0006: exporting with the wrong flags turns native tables into pictures
    and discards the edits, while reporting success. A count is how that is
    caught.
    """

    tables: int = 0
    chart_parts: int = 0
    diagram_parts: int = 0
    media_parts: int = 0
    embeddings: int = 0
    pictures: int = 0
    text_runs: int = 0
    shapes: int = 0

    @classmethod
    def of(cls, pkg: Package) -> NativeObjectCensus:
        slide_xml = "".join(pkg.read_text(p.name) for p in pkg.slides())
        return cls(
            tables=slide_xml.count("<a:tbl>"),
            chart_parts=len(pkg.matching(CHART_PART)),
            diagram_parts=len([p for p in pkg.matching(DIAGRAM_PART) if p.name.endswith(".xml")]),
            media_parts=len(pkg.matching(MEDIA_PART)),
            embeddings=len(pkg.matching(EMBEDDING_PART)),
            pictures=slide_xml.count("<p:pic>"),
            text_runs=len(re.findall(r"<a:t>", slide_xml)),
            shapes=slide_xml.count("<p:sp>"),
        )

    def losses_against(self, source: NativeObjectCensus) -> list[str]:
        """Which native objects the output has fewer of than the source.

        Pictures are excluded: gaining a picture is suspicious (rasterisation),
        losing one is caught by media_parts. Text runs are reported separately
        because an edit legitimately changes their content but rarely their count.
        """
        losses = []
        for field_name in ("tables", "chart_parts", "diagram_parts", "media_parts", "embeddings"):
            before = getattr(source, field_name)
            after = getattr(self, field_name)
            if after < before:
                losses.append(f"{field_name}: {before} -> {after}")
        return losses

    def rasterisation_suspected(self, source: NativeObjectCensus) -> bool:
        """More pictures and fewer native objects is the signature of ADR-0006."""
        gained_pictures = self.pictures > source.pictures
        lost_native = bool(self.losses_against(source))
        return gained_pictures and lost_native


@dataclass
class FidelityReport:
    """The result of comparing an output against its source."""

    source: str
    output: str
    deltas: list[PartDelta] = field(default_factory=list)
    source_census: NativeObjectCensus = field(default_factory=NativeObjectCensus)
    output_census: NativeObjectCensus = field(default_factory=NativeObjectCensus)

    # The order slides are presented in, which no part hash can see. `None`
    # means it could not be read -- distinct from "read, and empty".
    source_order: list[str] | None = None
    output_order: list[str] | None = None

    # ── part accounting ──────────────────────────────────────────────────────

    @property
    def identical(self) -> list[PartDelta]:
        return [d for d in self.deltas if d.status == "identical"]

    @property
    def changed(self) -> list[PartDelta]:
        return [d for d in self.deltas if d.status == "changed"]

    @property
    def removed(self) -> list[PartDelta]:
        return [d for d in self.deltas if d.status == "removed"]

    @property
    def added(self) -> list[PartDelta]:
        return [d for d in self.deltas if d.status == "added"]

    @property
    def total_source_parts(self) -> int:
        return len(self.identical) + len(self.changed) + len(self.removed)

    @property
    def fidelity_score(self) -> float:
        """Percentage of source parts that came back byte-for-byte.

        The headline metric. A generate-first tool scores near zero here by
        construction, because it never read the source bytes.
        """
        if not self.total_source_parts:
            return 0.0
        return 100.0 * len(self.identical) / self.total_source_parts

    @property
    def changed_slide_numbers(self) -> list[int]:
        return sorted({d.slide_owner for d in self.changed if d.slide_owner is not None})

    # ── integrity ────────────────────────────────────────────────────────────

    @property
    def native_losses(self) -> list[str]:
        return self.output_census.losses_against(self.source_census)

    @property
    def rasterisation_suspected(self) -> bool:
        return self.output_census.rasterisation_suspected(self.source_census)

    @property
    def slide_order_changed(self) -> bool:
        """Whether the deck reads in a different order than it did.

        Every part can be byte-for-byte identical while the deck says something
        else, because the running order lives in `presentation.xml` rather than
        in any slide. Measured: swap the first two entries of the `sldIdLst` on
        a real deck and the report came back 99.56% identical, deliverable, with
        `ppt/presentation.xml` listed under "other parts changed (review)" --
        indistinguishable, to a reviewer, from a workbook that moved because a
        chart's numbers did.

        No operation in this engine reorders slides, so any difference here is
        unrequested by construction.
        """
        # `None != [...]` is the answer that is wanted when one side could not
        # be read: a deck that stops declaring an order has changed. Two
        # unreadable sides compare equal, which is a fact about the check rather
        # than a claim about the deck -- and a deck whose presentation part
        # cannot be parsed fails elsewhere, loudly.
        return self.source_order != self.output_order

    @property
    def structurally_intact(self) -> bool:
        """No parts vanished, no native objects lost, and the order preserved."""
        return not self.removed and not self.native_losses and not self.slide_order_changed

    def summary(self) -> str:
        lines = [
            f"source   {self.source}",
            f"output   {self.output}",
            "",
            f"fidelity {self.fidelity_score:.2f}%  "
            f"({len(self.identical)}/{self.total_source_parts} parts byte-identical)",
            f"changed  {len(self.changed)}   removed {len(self.removed)}   added {len(self.added)}",
        ]
        if self.changed:
            lines.append("")
            for d in self.changed:
                lines.append(f"  changed  {d.name}")
        for d in self.removed:
            lines.append(f"  REMOVED  {d.name}")
        for d in self.added:
            lines.append(f"  added    {d.name}")

        s, o = self.source_census, self.output_census
        lines += [
            "",
            "native objects        source -> output",
            f"  tables              {s.tables:>5} -> {o.tables}",
            f"  chart parts         {s.chart_parts:>5} -> {o.chart_parts}",
            f"  diagram (SmartArt)  {s.diagram_parts:>5} -> {o.diagram_parts}",
            f"  media               {s.media_parts:>5} -> {o.media_parts}",
            f"  embeddings          {s.embeddings:>5} -> {o.embeddings}",
            f"  pictures            {s.pictures:>5} -> {o.pictures}",
            f"  text runs           {s.text_runs:>5} -> {o.text_runs}",
        ]
        if self.native_losses:
            lines.append("")
            lines.append("  NATIVE OBJECT LOSS: " + "; ".join(self.native_losses))
        if self.rasterisation_suspected:
            lines.append("  RASTERISATION SUSPECTED (pictures up, native objects down)")
        return "\n".join(lines)


def compare(source: Package | str, output: Package | str) -> FidelityReport:
    """Compare an output package against its source."""
    src = source if isinstance(source, Package) else Package.open(source)
    out = output if isinstance(output, Package) else Package.open(output)

    deltas: list[PartDelta] = []
    for name, part in src.parts.items():
        other = out.parts.get(name)
        if other is None:
            deltas.append(PartDelta(name, "removed", part.sha256, None))
        elif other.sha256 == part.sha256:
            deltas.append(PartDelta(name, "identical", part.sha256, other.sha256))
        else:
            deltas.append(PartDelta(name, "changed", part.sha256, other.sha256))
    for name, part in out.parts.items():
        if name not in src.parts:
            deltas.append(PartDelta(name, "added", None, part.sha256))

    return FidelityReport(
        source=str(src.path),
        output=str(out.path),
        deltas=sorted(deltas, key=lambda d: d.name),
        source_census=NativeObjectCensus.of(src),
        output_census=NativeObjectCensus.of(out),
        source_order=slide_order(src),
        output_order=slide_order(out),
    )


PRESENTATION = "ppt/presentation.xml"
PRESENTATION_RELS = "ppt/_rels/presentation.xml.rels"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
P_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def slide_order(pkg: Package) -> list[str] | None:
    """The slide part names in the order the deck presents them.

    Returns `None` when it cannot be read, which is deliberately not the same
    as `[]`. An empty list is a claim -- "this deck presents no slides" -- and
    making an unreadable presentation part look like one would turn a broken
    deck into a matching pair of empty orders, which is exactly the fail-open
    this check exists to remove.
    """
    if PRESENTATION not in pkg.parts or PRESENTATION_RELS not in pkg.parts:
        return None
    try:
        pres = etree.fromstring(pkg.read(PRESENTATION))
        rels = etree.fromstring(pkg.read(PRESENTATION_RELS))
    except (etree.XMLSyntaxError, KeyError):
        return None

    targets = {
        rel.get("Id", ""): rel.get("Target", "")
        for rel in rels.iter(f"{PKG_REL_NS}Relationship")
    }
    order = []
    for sld_id in pres.iter(f"{P_NS}sldId"):
        target = targets.get(sld_id.get(f"{R_NS}id", ""))
        if target is None:
            # A slide the deck presents but whose part cannot be named. Not a
            # gap to paper over: the order is not knowable, so say so.
            return None
        order.append(_resolve_part(target))
    return order


def _resolve_part(target: str) -> str:
    cleaned = target.replace("\\", "/").lstrip("/")
    while cleaned.startswith("../"):
        cleaned = cleaned[3:]
    return cleaned if cleaned.startswith("ppt/") else f"ppt/{cleaned}"
