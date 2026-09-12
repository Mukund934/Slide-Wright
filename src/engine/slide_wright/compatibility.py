"""What this engine has actually been measured against, construct by construct.

The question a practitioner asks first is "does it handle *my* deck", and their
deck has whatever it has: video, equations, ink, a 3D model somebody dropped in.
The honest answer is not a promise. It is a measurement, or the word UNKNOWN.

So this module does not describe the engine. It *runs* it over whatever decks
are on this machine, and reports three things per construct:

  · **PRESERVED** — a deck containing it was edited and the parts carrying it
    came back byte-for-byte identical. Measured.
  · **REFUSED** — the engine declines to edit it, on purpose. A recorded
    decision, named against the code that enforces it.
  · **UNKNOWN** — no deck available here contains it, so nothing was measured.

UNKNOWN is the point of the whole file. Every table like this drifts the same
way: a row nobody has evidence for gets filled in with what the author believes,
because a blank looks like an oversight and a tick looks finished. A tick that
means "we think so" is indistinguishable from one that means "we measured it",
and that makes every other row in the table worth less.

There is therefore no way to write a verdict here by hand. A construct is
PRESERVED because a fixture contained it and survived an edit, or it is UNKNOWN.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from slide_wright.fidelity import compare
from slide_wright.package import Package


@dataclass(frozen=True)
class Construct:
    """One OOXML feature, and how to tell whether a deck contains it."""

    name: str
    #: Matched against the concatenated slide XML.
    marker: str = ""
    #: Matched against part names.
    part: str = ""
    #: Set when the engine refuses to edit it, naming where that is enforced.
    refused_because: str = ""
    note: str = ""

    def present_in(self, pkg: Package, slide_xml: str) -> bool:
        if self.part and any(re.search(self.part, name) for name in pkg.parts):
            return True
        return bool(self.marker) and self.marker in slide_xml


# Ordered roughly by how often a real deck has one. Markers are OOXML element
# names, because that is what a deck actually contains — a friendlier vocabulary
# here would be a second place for the truth to drift.
CONSTRUCTS = (
    Construct("Text runs", marker="<a:t>"),
    Construct("Shapes", marker="<p:sp>"),
    Construct("Pictures", marker="<p:pic>"),
    Construct("Groups", marker="<p:grpSp>"),
    Construct("Connectors", marker="<p:cxnSp>"),
    Construct("Native tables", marker="<a:tbl>"),
    Construct("Placeholders", marker="<p:ph "),
    Construct("Hyperlinks", marker="<a:hlinkClick"),
    Construct("Speaker notes", part=r"^ppt/notesSlides/"),
    Construct(
        "Charts",
        part=r"^ppt/charts/chart\d+\.xml$",
        refused_because="charts.py (guard_edit) — ADR-0009",
        note="read, counted and preserved; editing the data is refused",
    ),
    Construct(
        "SmartArt",
        part=r"^ppt/diagrams/",
        refused_because="smartart.py (guard_edit) — ADR-0007",
        note="read, counted and preserved; editing a diagram is refused",
    ),
    Construct("Embedded workbooks", part=r"^ppt/embeddings/"),
    Construct("Media files", part=r"^ppt/media/"),
    Construct("Video", marker="<p:videoFile"),
    Construct("Audio", marker="<p:audioFile"),
    Construct("OLE objects", marker="<p:oleObj"),
    Construct("Equations (OMML)", marker="<m:oMath"),
    Construct("Ink annotations", marker="<p14:ink"),
    Construct("3D models", marker="model3D"),
    Construct("Animation timing", marker="<p:timing>"),
    Construct("Slide transitions", marker="<p:transition"),
    Construct("Comments", part=r"^ppt/comments/comment\d+\.xml$"),
    Construct(
        "Modern comments",
        part=r"^ppt/comments/modernComment",
        note="the 2021 schema, a different part from the legacy one",
    ),
    Construct("Revision history", part=r"^ppt/changesInfos/"),
    Construct("Custom XML", part=r"^customXml/"),
    Construct(
        "Macros (VBA)",
        part=r"vbaProject\.bin$",
        refused_because="package.py (_assert_safe), report.py (executable_additions)",
        note=".pptm is refused on open, and a macro part appearing in an output "
             "blocks delivery",
    ),
)

PRESERVED = "PRESERVED"
REFUSED = "REFUSED"
UNKNOWN = "UNKNOWN"
DAMAGED = "DAMAGED"


@dataclass
class Row:
    construct: Construct
    decks_containing: int = 0
    decks_preserved: int = 0

    @property
    def verdict(self) -> str:
        """PRESERVED only if something was measured. Never by assertion."""
        if self.decks_containing == 0:
            return UNKNOWN
        return PRESERVED if self.decks_preserved == self.decks_containing else DAMAGED

    @property
    def editing(self) -> str:
        """Whether an edit targeting this construct is refused.

        "not refused" rather than "allowed", and the difference is the whole
        habit of this file. The engine has no guard against editing an OLE
        object; it also has no way to edit one. "Allowed" would read as support
        for something nobody has built, which is the same overclaim as a ticked
        row nobody measured — just spelled differently.

        A refusal is a decision in the code and is reported whether or not a
        deck here contains the construct. Unlike preservation it is not a
        measurement and does not need one, so the two columns answer different
        questions and are deliberately not merged.
        """
        if self.construct.refused_because:
            return REFUSED
        return "not refused" if self.decks_containing else UNKNOWN


@dataclass
class Matrix:
    rows: list[Row] = field(default_factory=list)
    decks: list[str] = field(default_factory=list)

    @property
    def unknown(self) -> list[Row]:
        return [r for r in self.rows if r.verdict == UNKNOWN]

    @property
    def measured(self) -> list[Row]:
        return [r for r in self.rows if r.verdict != UNKNOWN]

    def render(self) -> str:
        lines = [
            "| Construct | Present in | Preserved through an edit | Editing |",
            "| --- | --- | --- | --- |",
        ]
        for row in self.rows:
            where = (
                f"{row.decks_containing} of {len(self.decks)} decks"
                if row.decks_containing
                else "no deck measured here"
            )
            verdict = (
                row.verdict if row.verdict != UNKNOWN
                else "UNKNOWN — nothing to measure"
            )
            lines.append(
                f"| {row.construct.name} | {where} | {verdict} | {row.editing} |"
            )
        return "\n".join(lines)


def measure(pairs: list[tuple[Path, Path]]) -> Matrix:
    """Build the matrix from (source, edited output) pairs.

    The caller supplies the pairs, because producing one means running an edit
    and this module must not decide what an edit is. It only reads what two
    packages contain and whether the parts carrying each construct came back
    identical.
    """
    matrix = Matrix()
    rows = {c.name: Row(construct=c) for c in CONSTRUCTS}

    for source, output in pairs:
        src = Package.open(source)
        matrix.decks.append(Path(source).name)
        slide_xml = "".join(src.read_text(p.name) for p in src.slides())
        report = compare(src, Package.open(output))
        untouched = {d.name for d in report.identical}

        for construct in CONSTRUCTS:
            if not construct.present_in(src, slide_xml):
                continue
            row = rows[construct.name]
            row.decks_containing += 1
            if _survived(construct, src, untouched, report):
                row.decks_preserved += 1

    matrix.rows = [rows[c.name] for c in CONSTRUCTS]
    return matrix


def _survived(construct: Construct, src: Package, untouched: set[str], report) -> bool:
    """Whether the parts carrying this construct came back byte-for-byte.

    For a construct that lives in its own parts — a chart, a diagram, an
    embedded workbook — this is exact: those part names either are in the
    identical set or they are not.

    For one that lives inside slide XML the carrier is the slide, and an edit
    changes one of those on purpose. So the test there is the weaker but still
    true one: nothing was removed and no native object was lost. Weaker, and
    said out loud rather than hidden, because a matrix that overclaims is the
    failure this file exists to prevent.
    """
    if construct.part:
        carriers = [name for name in src.parts if re.search(construct.part, name)]
        return bool(carriers) and all(name in untouched for name in carriers)
    return not report.removed and not report.native_losses
