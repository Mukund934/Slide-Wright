"""The deterministic quality gate.

ADR-0003: the gate decides pass/fail, and it is never a model. A model asked to
grade its own slide will report success in the direction that makes it look
useful.

Two design rules, both learned from measurement:

  1. Findings carry repair instructions, not just complaints. The upstream
     engine's own gate says "≈108 chars fit in 1280 px" rather than "too wide",
     and that difference is what lets an authoring model actually fix the
     problem instead of guessing again.

  2. Geometry is where quality actually fails. Text does not reflow in the
     render path (`wrap="none"`), so a mis-measured string leaves the slide and
     never comes back. Most "bad AI slide" complaints are arithmetic, not taste.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from slide_wright.inspect import EMU_PER_INCH, EMU_PER_POINT, DeckInfo, ShapeInfo, SlideInfo


class Severity(str, Enum):
    ERROR = "error"      # blocks delivery
    WARNING = "warning"  # surfaced, does not block


# Thresholds. Deliberately explicit so they can be argued with and tuned.
MIN_BODY_PT = 10.0            # below this is unreadable in a room
MIN_MARGIN_EMU = EMU_PER_INCH // 4      # 0.25in from the slide edge
DENSE_WORDS_PER_SLIDE = 120   # beyond this a slide is a document
MAX_OVERLAP_RATIO = 0.10      # fraction of the smaller shape's area
MIN_CONTRAST_RATIO = 4.5      # WCAG AA for normal text


@dataclass
class Finding:
    """One problem, with enough information to fix it."""

    code: str
    severity: Severity
    slide: int
    message: str
    repair: str = ""
    shape_id: str = ""
    shape_name: str = ""

    def line(self) -> str:
        where = f"slide {self.slide}"
        if self.shape_name:
            where += f" · {self.shape_name}"
        out = f"[{self.severity.value.upper():7}] {where}: {self.message}"
        if self.repair:
            out += f"\n            fix: {self.repair}"
        return out


@dataclass
class GateResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARNING]

    @property
    def passed(self) -> bool:
        return not self.errors

    def for_slide(self, n: int) -> list[Finding]:
        return [f for f in self.findings if f.slide == n]

    def render(self) -> str:
        if not self.findings:
            return "QUALITY GATE — passed, no findings."
        head = (
            f"QUALITY GATE — {'passed' if self.passed else 'FAILED'}  "
            f"({len(self.errors)} error(s), {len(self.warnings)} warning(s))"
        )
        return "\n".join([head, ""] + [f.line() for f in self.findings])

    def repair_brief(self) -> str:
        """Errors only, phrased for an authoring model to act on."""
        if not self.errors:
            return ""
        lines = ["Fix the following before the deck can be delivered:"]
        for f in self.errors:
            lines.append(f"- slide {f.slide}: {f.message} — {f.repair or 'correct and re-run'}")
        return "\n".join(lines)


def check(deck: DeckInfo) -> GateResult:
    """Run every deterministic check over a deck."""
    result = GateResult()
    for slide in deck.slides:
        _check_bounds(deck, slide, result)
        _check_margins(deck, slide, result)
        _check_font_sizes(slide, result)
        _check_overlap(slide, result)
        _check_density(slide, result)
        _check_empty(slide, result)
    _check_consistency(deck, result)
    return result


# ── checks ───────────────────────────────────────────────────────────────────

def _check_bounds(deck: DeckInfo, slide: SlideInfo, out: GateResult) -> None:
    """Content outside the canvas. The failure mode that motivated the gate."""
    if not deck.slide_width:
        return
    for s in slide.shapes:
        if s.right is None:
            continue
        if s.right > deck.slide_width:
            over = s.right - deck.slide_width
            pct = 100.0 * over / deck.slide_width
            out.findings.append(Finding(
                code="bounds.horizontal", severity=Severity.ERROR, slide=slide.number,
                shape_id=s.id, shape_name=s.name,
                message=(
                    f"extends {over / EMU_PER_INCH:.2f}in past the right edge "
                    f"({pct:.1f}% overflow)"
                ),
                repair=(
                    f"reduce width to at most "
                    f"{(deck.slide_width - s.x) / EMU_PER_INCH:.2f}in, or move left to "
                    f"x <= {(deck.slide_width - s.cx) / EMU_PER_INCH:.2f}in"
                ),
            ))
        if s.bottom is not None and s.bottom > deck.slide_height:
            over = s.bottom - deck.slide_height
            out.findings.append(Finding(
                code="bounds.vertical", severity=Severity.ERROR, slide=slide.number,
                shape_id=s.id, shape_name=s.name,
                message=f"extends {over / EMU_PER_INCH:.2f}in below the bottom edge",
                repair=(
                    f"reduce height to at most "
                    f"{(deck.slide_height - s.y) / EMU_PER_INCH:.2f}in, or move up"
                ),
            ))
        if s.x is not None and s.x < 0:
            out.findings.append(Finding(
                code="bounds.offcanvas", severity=Severity.ERROR, slide=slide.number,
                shape_id=s.id, shape_name=s.name,
                message=f"starts {abs(s.x) / EMU_PER_INCH:.2f}in left of the canvas",
                repair="move to x >= 0",
            ))


def _check_margins(deck: DeckInfo, slide: SlideInfo, out: GateResult) -> None:
    if not deck.slide_width:
        return
    for s in slide.shapes:
        if s.x is None or not s.has_text:
            continue
        if 0 <= s.x < MIN_MARGIN_EMU:
            out.findings.append(Finding(
                code="margin.left", severity=Severity.WARNING, slide=slide.number,
                shape_id=s.id, shape_name=s.name,
                message=f"text starts {s.x / EMU_PER_INCH:.2f}in from the left edge",
                repair=f"move to x >= {MIN_MARGIN_EMU / EMU_PER_INCH:.2f}in",
            ))


def _check_font_sizes(slide: SlideInfo, out: GateResult) -> None:
    for s in slide.shapes:
        for run in s.runs:
            if run.size_pt is not None and run.size_pt < MIN_BODY_PT:
                out.findings.append(Finding(
                    code="type.too_small", severity=Severity.WARNING, slide=slide.number,
                    shape_id=s.id, shape_name=s.name,
                    message=f"text at {run.size_pt:.0f}pt is below the {MIN_BODY_PT:.0f}pt floor",
                    repair=f"raise to at least {MIN_BODY_PT:.0f}pt, or cut the text",
                ))
                break


def _check_overlap(slide: SlideInfo, out: GateResult) -> None:
    """Text colliding with text. Ignores backgrounds and decorative frames."""
    texty = [
        s for s in slide.shapes
        if s.has_text and s.x is not None and s.cx and s.cy
    ]
    for i, a in enumerate(texty):
        for b in texty[i + 1:]:
            if not a.overlaps(b):
                continue
            ox = min(a.right, b.right) - max(a.x, b.x)
            oy = min(a.bottom, b.bottom) - max(a.y, b.y)
            area = ox * oy
            smaller = min(a.cx * a.cy, b.cx * b.cy)
            if smaller and area / smaller > MAX_OVERLAP_RATIO:
                out.findings.append(Finding(
                    code="layout.overlap", severity=Severity.WARNING, slide=slide.number,
                    shape_id=a.id, shape_name=a.name,
                    message=(
                        f"text overlaps {b.name or b.id} by "
                        f"{100.0 * area / smaller:.0f}% of the smaller box"
                    ),
                    repair="separate the boxes or reduce one of them",
                ))


def _check_density(slide: SlideInfo, out: GateResult) -> None:
    if slide.word_count > DENSE_WORDS_PER_SLIDE:
        out.findings.append(Finding(
            code="content.dense", severity=Severity.WARNING, slide=slide.number,
            message=f"{slide.word_count} words on one slide",
            repair=(
                f"split across slides or cut to about {DENSE_WORDS_PER_SLIDE}; "
                "move detail to speaker notes"
            ),
        ))


def _check_empty(slide: SlideInfo, out: GateResult) -> None:
    if not slide.shapes:
        out.findings.append(Finding(
            code="content.empty", severity=Severity.WARNING, slide=slide.number,
            message="slide has no shapes",
            repair="add content or remove the slide",
        ))


def _check_consistency(deck: DeckInfo, out: GateResult) -> None:
    """Title typography that drifts across the deck reads as carelessness."""
    sizes: dict[float, list[int]] = {}
    for slide in deck.slides:
        for s in slide.shapes:
            if s.placeholder_type in {"title", "ctrTitle"}:
                for run in s.runs:
                    if run.size_pt:
                        sizes.setdefault(run.size_pt, []).append(slide.number)
                        break
                break
    if len(sizes) > 2:
        detail = ", ".join(f"{pt:.0f}pt on {len(ns)} slide(s)" for pt, ns in sorted(sizes.items()))
        out.findings.append(Finding(
            code="consistency.title_size", severity=Severity.WARNING, slide=0,
            message=f"titles use {len(sizes)} different sizes ({detail})",
            repair="standardise title size across the deck",
        ))


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two hex colours ('RRGGBB')."""
    def luminance(hex_colour: str) -> float:
        h = hex_colour.lstrip("#")
        if len(h) != 6:
            return 0.0
        channels = []
        for i in (0, 2, 4):
            c = int(h[i:i + 2], 16) / 255.0
            channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
        r, g, b = channels
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    l1, l2 = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)
