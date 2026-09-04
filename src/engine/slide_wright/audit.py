"""Deck-level audit: what is wrong with this deck, and what to do about it.

The quality gate (`gate.py`) answers "may this be delivered?" — a per-slide,
pass/fail question about a deck we just edited. The audit answers a different
one: "what should change?", asked of a deck nobody has touched yet.

That makes it the first thing a new user runs, and the first thing that has to
be right. So it is deterministic for the same reason the gate is: a model that
invents a criticism is worse than no criticism at all, because the user cannot
tell which findings to trust.

Everything here is computed from structure. Nothing is a matter of taste.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from slide_wright.gate import GateResult, Severity, check
from slide_wright.inspect import DeckInfo, SlideInfo

# A number with a unit is self-describing; a bare one is not. Getting this
# rule *precise* matters more than getting it complete: telling someone their
# slide title "Slide 1" contains an unlabelled figure destroys their trust in
# every other finding.
#
# So a candidate must look like a measurement — two or more digits, or a
# decimal — and years, ordinals and enumeration labels are excluded.
MEASUREMENT = re.compile(r"(?<![\w.$£€₹%])(\d[\d,]*\.\d+|\d{2,}[\d,]*)(?![\w%°])")
YEAR = re.compile(r"^(?:19|20)\d{2}$")
UNIT_HINT = re.compile(
    r"[%$£€₹]"
    r"|\b(?:x|bn|mn|m|k|cr|pts?|bps|USD|INR|EUR|GBP|CAGR)\b"
    r"|\b(?:per ?cent|percent|percentage|million|billion|thousand|trillion"
    r"|lakh|crore|rupees?|dollars?|euros?|pounds?|points?|basis points"
    r"|days?|weeks?|months?|years?|hours?|minutes?|seconds?"
    r"|users?|customers?|people|employees|units?)\b",
    re.I,
)
# Labels where a number is an identifier, not a quantity.
ENUMERATION = re.compile(
    r"\b(?:slide|page|figure|fig|table|exhibit|appendix|chapter|section|step|"
    r"phase|part|q|quarter|fy|version|v)\s*\d+\b",
    re.I,
)
SOURCE_HINT = re.compile(r"\b(?:source|sources|per|according to|basis|note)\b[:\s]", re.I)

# Titles that describe a container rather than making a claim.
WEAK_TITLE = re.compile(
    r"^(?:overview|introduction|agenda|background|summary|conclusion|results|"
    r"analysis|data|details|information|update|next steps|thank you|questions?)\b",
    re.I,
)


class Area(str, Enum):
    STRUCTURE = "structure"
    NARRATIVE = "narrative"
    CONSISTENCY = "consistency"
    EVIDENCE = "evidence"
    LAYOUT = "layout"
    ACCESSIBILITY = "accessibility"


@dataclass
class Observation:
    """Something true about the deck that its author would want to know."""

    area: Area
    slides: list[int]
    message: str
    suggestion: str = ""
    severity: Severity = Severity.WARNING

    @property
    def where(self) -> str:
        if not self.slides:
            return "deck"
        if len(self.slides) <= 4:
            return "slide " + ", ".join(str(n) for n in self.slides)
        return f"{len(self.slides)} slides ({self.slides[0]}–{self.slides[-1]})"


@dataclass
class DeckAudit:
    deck: str
    slide_count: int = 0
    word_count: int = 0
    observations: list[Observation] = field(default_factory=list)
    gate: GateResult | None = None

    def by_area(self, area: Area) -> list[Observation]:
        return [o for o in self.observations if o.area is area]

    @property
    def errors(self) -> list[Observation]:
        return [o for o in self.observations if o.severity is Severity.ERROR]

    @property
    def words_per_slide(self) -> float:
        return self.word_count / self.slide_count if self.slide_count else 0.0

    def render(self) -> str:
        lines = [
            f"DECK AUDIT — {self.deck}",
            "",
            f"  {self.slide_count} slides · {self.word_count} words · "
            f"{self.words_per_slide:.0f} words per slide",
        ]
        if self.gate is not None:
            lines.append(
                f"  quality gate: {len(self.gate.errors)} error(s), "
                f"{len(self.gate.warnings)} warning(s)"
            )
        if not self.observations:
            lines += ["", "  No structural issues found."]
            return "\n".join(lines)

        for area in Area:
            found = self.by_area(area)
            if not found:
                continue
            lines += ["", f"  {area.value.upper()}"]
            for obs in found:
                mark = "!" if obs.severity is Severity.ERROR else "·"
                lines.append(f"    {mark} {obs.where}: {obs.message}")
                if obs.suggestion:
                    lines.append(f"        {obs.suggestion}")

        lines += ["", f"  {len(self.observations)} observation(s)"]
        return "\n".join(lines)


def audit(deck: DeckInfo, name: str = "") -> DeckAudit:
    """Audit a deck structurally. Every finding is computed, none is opinion."""
    result = DeckAudit(
        deck=name or "deck",
        slide_count=deck.slide_count,
        word_count=sum(s.word_count for s in deck.slides),
        gate=check(deck),
    )
    for rule in (
        _missing_titles,
        _duplicate_titles,
        _weak_titles,
        _density_outliers,
        _empty_and_image_only,
        _font_sprawl,
        _colour_sprawl,
        _unsourced_figures,
        _bare_numbers,
        _table_shape,
    ):
        rule(deck, result)
    return result


# ── rules ────────────────────────────────────────────────────────────────────

def _missing_titles(deck: DeckInfo, out: DeckAudit) -> None:
    missing = [s.number for s in deck.slides if not s.title and s.shapes]
    if missing:
        out.observations.append(Observation(
            Area.NARRATIVE, missing,
            f"{len(missing)} slide(s) have no title",
            "a reader skimming the deck sees only titles; every slide needs one",
        ))


def _duplicate_titles(deck: DeckInfo, out: DeckAudit) -> None:
    titles = [(s.number, s.title.strip().lower()) for s in deck.slides if s.title]
    counts = Counter(t for _, t in titles)
    for title, n in counts.items():
        if n < 2:
            continue
        slides = [num for num, t in titles if t == title]
        out.observations.append(Observation(
            Area.NARRATIVE, slides,
            f"{n} slides share the title {title!r}",
            "give each slide a title that states what *this* slide says",
        ))


def _weak_titles(deck: DeckInfo, out: DeckAudit) -> None:
    """Container titles ('Overview') name a topic; action titles make a claim."""
    weak = [s.number for s in deck.slides if s.title and WEAK_TITLE.match(s.title.strip())]
    if len(weak) >= 3:
        out.observations.append(Observation(
            Area.NARRATIVE, weak,
            f"{len(weak)} titles name a topic rather than state a finding",
            'replace "Results" with the result — a reader should get the argument '
            "from the titles alone",
        ))


def _density_outliers(deck: DeckInfo, out: DeckAudit) -> None:
    counts = [s.word_count for s in deck.slides if s.word_count]
    if len(counts) < 4:
        return
    mean = sum(counts) / len(counts)
    heavy = [s.number for s in deck.slides if s.word_count > max(2.5 * mean, 100)]
    if heavy:
        out.observations.append(Observation(
            Area.LAYOUT, heavy,
            f"{len(heavy)} slide(s) carry far more text than the deck average "
            f"({mean:.0f} words)",
            "split them, or move the detail into speaker notes",
        ))


def _empty_and_image_only(deck: DeckInfo, out: DeckAudit) -> None:
    image_only = [
        s.number for s in deck.slides
        if s.shapes and s.word_count == 0
        and any(sh.kind == "picture" for sh in s.shapes)
    ]
    if image_only:
        out.observations.append(Observation(
            Area.ACCESSIBILITY, image_only,
            f"{len(image_only)} slide(s) contain only images and no text",
            "text in an image cannot be searched, translated, read aloud, or edited",
        ))


def _font_sprawl(deck: DeckInfo, out: DeckAudit) -> None:
    fonts = Counter(
        run.font for shape in deck.all_shapes() for run in shape.runs if run.font
    )
    if len(fonts) > 3:
        listed = ", ".join(f"{f} ({n})" for f, n in fonts.most_common(6))
        out.observations.append(Observation(
            Area.CONSISTENCY, [],
            f"{len(fonts)} typefaces are in use: {listed}",
            "a deck reads as considered when it uses two, occasionally three",
        ))


def _colour_sprawl(deck: DeckInfo, out: DeckAudit) -> None:
    colours = Counter(
        run.color for shape in deck.all_shapes() for run in shape.runs if run.color
    )
    if len(colours) > 6:
        out.observations.append(Observation(
            Area.CONSISTENCY, [],
            f"{len(colours)} explicit text colours are in use",
            "explicit colours override the theme; prefer theme colours so a "
            "template change carries through",
        ))


def _unsourced_figures(deck: DeckInfo, out: DeckAudit) -> None:
    """Slides carrying data but no attribution.

    The failure this guards against is the expensive one: a number on a slide
    that nobody can trace three weeks later.
    """
    unsourced = []
    for slide in deck.slides:
        has_data = any(sh.kind in {"table", "chart"} for sh in slide.shapes)
        if has_data and not SOURCE_HINT.search(slide.text):
            unsourced.append(slide.number)
    if unsourced:
        out.observations.append(Observation(
            Area.EVIDENCE, unsourced,
            f"{len(unsourced)} slide(s) present a table or chart with no source line",
            "add a source note; an unattributed figure cannot be checked later",
        ))


def _bare_numbers(deck: DeckInfo, out: DeckAudit) -> None:
    """Body text presenting a measurement with no unit.

    Titles are excluded: a title makes a claim, and "Revenue grew 38 percent"
    is not an unlabelled figure. Years and enumeration labels are excluded
    because they are identifiers rather than quantities.
    """
    bare = []
    for slide in deck.slides:
        for shape in slide.shapes:
            if shape.kind in {"table", "chart"} or not shape.has_text:
                continue
            if shape.placeholder_type in {"title", "ctrTitle", "subTitle"}:
                continue
            text = shape.text
            if UNIT_HINT.search(text):
                continue
            candidates = [
                m.group(1) for m in MEASUREMENT.finditer(ENUMERATION.sub("", text))
            ]
            if any(not YEAR.match(c.replace(",", "")) for c in candidates):
                bare.append(slide.number)
                break
    if len(bare) >= 3:
        out.observations.append(Observation(
            Area.EVIDENCE, sorted(set(bare)),
            f"{len(set(bare))} slide(s) show numbers with no unit or currency",
            "state the unit next to the figure; readers should not have to infer it",
        ))


def _table_shape(deck: DeckInfo, out: DeckAudit) -> None:
    wide = [
        s.number for s in deck.slides
        for sh in s.shapes
        if sh.kind == "table" and sh.table_cols > 7
    ]
    if wide:
        out.observations.append(Observation(
            Area.LAYOUT, sorted(set(wide)),
            f"{len(set(wide))} table(s) have more than seven columns",
            "a table that wide is usually read as a chart; consider one",
        ))
