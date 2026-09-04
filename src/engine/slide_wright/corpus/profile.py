"""Profile a deck for the constructs that make fidelity hard.

The R2 risk is that byte-fidelity holds on simple decks and fails on real ones.
"Real" is not a vibe — it is a specific list of OOXML constructs that a student
deck does not contain and a pitchbook does. This module measures that list, so a
corpus can be selected on evidence rather than on the filename.

A deck's `difficulty` is the count of hard constructs it contains. A corpus of
difficulty-0 decks proves nothing, which is exactly the trap this exists to
avoid.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from slide_wright.package import (
    CHART_PART,
    DIAGRAM_PART,
    EMBEDDING_PART,
    LAYOUT_PART,
    MASTER_PART,
    MEDIA_PART,
    NOTES_PART,
    Package,
    UnsafePackageError,
)

# Constructs that survive naively in a simple deck and break naive engines.
# Each is weighted 1; the sum is the difficulty score.
HARD_CONSTRUCTS = (
    "smartart",
    "native_charts",
    "embedded_workbooks",
    "grouped_shapes",
    "tables",
    "custom_geometry",
    "ole_objects",
    "hyperlinks",
    "notes",
    "multiple_masters",
)


@dataclass
class DeckProfile:
    """What a deck contains, in the terms that decide whether fidelity is hard."""

    path: str
    name: str
    ok: bool = True
    error: str = ""

    parts: int = 0
    slides: int = 0
    size_bytes: int = 0

    # structural
    layouts: int = 0
    masters: int = 0
    media: int = 0
    notes: int = 0

    # hard constructs
    smartart: int = 0
    native_charts: int = 0
    embedded_workbooks: int = 0
    grouped_shapes: int = 0
    tables: int = 0
    custom_geometry: int = 0
    ole_objects: int = 0
    hyperlinks: int = 0

    # shape census
    shapes: int = 0
    pictures: int = 0
    text_runs: int = 0

    authoring_tool: str = ""

    @property
    def multiple_masters(self) -> int:
        return 1 if self.masters > 1 else 0

    @property
    def difficulty(self) -> int:
        """Count of distinct hard constructs present. 0 means it proves nothing."""
        return sum(1 for c in HARD_CONSTRUCTS if getattr(self, c, 0))

    @property
    def present_constructs(self) -> list[str]:
        return [c for c in HARD_CONSTRUCTS if getattr(self, c, 0)]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["difficulty"] = self.difficulty
        d["present_constructs"] = self.present_constructs
        return d


def profile(path: str | Path) -> DeckProfile:
    """Profile one deck. Never raises for a bad deck — records the error instead."""
    path = Path(path)
    prof = DeckProfile(path=str(path), name=path.name)

    try:
        pkg = Package.open(path)
    except UnsafePackageError as exc:
        prof.ok = False
        prof.error = str(exc)
        return prof

    prof.parts = pkg.part_count
    prof.slides = pkg.slide_count
    prof.size_bytes = sum(p.size for p in pkg.parts.values())
    prof.layouts = len(pkg.matching(LAYOUT_PART))
    prof.masters = len(pkg.matching(MASTER_PART))
    prof.media = len(pkg.matching(MEDIA_PART))
    prof.notes = len(pkg.matching(NOTES_PART))

    # Part-level evidence: these constructs have their own parts, so they can be
    # counted without parsing any XML.
    prof.native_charts = len(pkg.matching(CHART_PART))
    prof.embedded_workbooks = len(pkg.matching(EMBEDDING_PART))
    prof.smartart = len([p for p in pkg.matching(DIAGRAM_PART) if p.name.endswith(".xml")])

    # Slide-level evidence: needs the XML.
    slide_xml = "".join(
        pkg.read_text(p.name) for p in pkg.slides()
    )
    prof.shapes = slide_xml.count("<p:sp>")
    prof.pictures = slide_xml.count("<p:pic>")
    prof.grouped_shapes = slide_xml.count("<p:grpSp>")
    prof.tables = slide_xml.count("<a:tbl>")
    prof.custom_geometry = slide_xml.count("<a:custGeom>")
    prof.ole_objects = slide_xml.count("<p:oleObj")
    prof.hyperlinks = slide_xml.count("<a:hlinkClick")
    prof.text_runs = len(re.findall(r"<a:t>", slide_xml))

    if "docProps/app.xml" in pkg.parts:
        app = pkg.read_text("docProps/app.xml")
        m = re.search(r"<Application>([^<]*)</Application>", app)
        if m:
            prof.authoring_tool = m.group(1)

    return prof


def profile_many(paths) -> list[DeckProfile]:
    """Profile many decks, hardest first."""
    return sorted(
        (profile(p) for p in paths),
        key=lambda pr: (pr.difficulty, pr.slides),
        reverse=True,
    )


def format_table(profiles: list[DeckProfile]) -> str:
    """A compact scan of a corpus, for humans deciding what to test against."""
    head = (
        f"{'deck':<44} {'sl':>3} {'pt':>4} {'diff':>4}  "
        f"{'smart':>5} {'chart':>5} {'group':>5} {'tbl':>4} {'geom':>4} {'ole':>3}"
    )
    lines = [head, "-" * len(head)]
    for p in profiles:
        if not p.ok:
            lines.append(f"{p.name[:44]:<44} {'ERR':>3}  {p.error[:50]}")
            continue
        lines.append(
            f"{p.name[:44]:<44} {p.slides:>3} {p.parts:>4} {p.difficulty:>4}  "
            f"{p.smartart:>5} {p.native_charts:>5} {p.grouped_shapes:>5} "
            f"{p.tables:>4} {p.custom_geometry:>4} {p.ole_objects:>3}"
        )
    return "\n".join(lines)
