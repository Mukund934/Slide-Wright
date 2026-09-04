"""Brand conformance: does this deck actually follow the template?

Every large organisation has a deck template and almost nobody follows it. The
drift is invisible slide by slide and obvious in aggregate — a Calibri heading
in a Georgia deck, a hand-picked blue that is not the brand blue, a title at
28pt where every other title is 32.

A generate-first tool cannot help here: it authors into its own visual system
and exports outward, so it can only produce a deck that matches *its* idea of
the brand. Reading a real `.potx` and reporting where a real `.pptx` departs
from it is a different problem, and it is the one enterprises actually have.

The template is the authority. Deviations are reported against it, never
averaged with it.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from lxml import etree

from slide_wright.inspect import NS, DeckInfo
from slide_wright.package import Package

HEX = re.compile(r"^[0-9A-Fa-f]{6}$")


@dataclass
class BrandProfile:
    """The visual system a template declares."""

    source: str = ""
    major_font: str = ""
    minor_font: str = ""
    theme_colors: dict[str, str] = field(default_factory=dict)
    layout_names: list[str] = field(default_factory=list)

    @property
    def fonts(self) -> set[str]:
        return {f for f in (self.major_font, self.minor_font) if f}

    @property
    def palette(self) -> set[str]:
        return {c.upper() for c in self.theme_colors.values() if HEX.match(c)}

    def render(self) -> str:
        lines = [f"BRAND PROFILE — {self.source}", ""]
        lines.append(f"  fonts    heading {self.major_font or '?'} · body {self.minor_font or '?'}")
        if self.theme_colors:
            swatches = ", ".join(
                f"{name} #{value}" for name, value in sorted(self.theme_colors.items())
            )
            lines.append(f"  palette  {swatches}")
        if self.layout_names:
            lines.append(f"  layouts  {len(self.layout_names)}: "
                         + ", ".join(self.layout_names[:8]))
        return "\n".join(lines)


@dataclass
class Deviation:
    kind: str            # "font" | "colour" | "title-size"
    detail: str
    slides: list[int]
    suggestion: str = ""


@dataclass
class ConformanceReport:
    profile: BrandProfile
    deck: str = ""
    deviations: list[Deviation] = field(default_factory=list)
    checked_runs: int = 0

    @property
    def conforms(self) -> bool:
        return not self.deviations

    @property
    def score(self) -> float:
        """Share of text runs using only template fonts and colours."""
        if not self.checked_runs:
            return 100.0
        off = sum(len(d.slides) for d in self.deviations if d.kind in {"font", "colour"})
        return max(0.0, 100.0 * (1 - off / max(self.checked_runs, 1)))

    def render(self) -> str:
        lines = [f"BRAND CONFORMANCE — {self.deck}", ""]
        lines.append(f"  template: {self.profile.source}")
        lines.append(f"  fonts:    {self.profile.major_font} / {self.profile.minor_font}")
        lines.append("")
        if self.conforms:
            lines.append("  CONFORMS — no off-template fonts or colours found.")
            return "\n".join(lines)
        for d in self.deviations:
            where = (
                "deck-wide" if not d.slides
                else f"slide {', '.join(str(n) for n in d.slides[:6])}"
                + ("…" if len(d.slides) > 6 else "")
            )
            lines.append(f"  · [{d.kind}] {d.detail}")
            lines.append(f"        {where}")
            if d.suggestion:
                lines.append(f"        {d.suggestion}")
        lines += ["", f"  {len(self.deviations)} deviation(s) from the template"]
        return "\n".join(lines)


def read_profile(template: str | Package) -> BrandProfile:
    """Extract the visual system from a .potx or .pptx.

    Reads the theme part directly rather than any slide, because the theme is
    what the template *declares* — a slide may already have drifted from it.
    """
    pkg = template if isinstance(template, Package) else Package.open(template)
    profile = BrandProfile(source=pkg.path.name)

    theme_part = next((n for n in pkg.parts if n.startswith("ppt/theme/theme")), None)
    if theme_part:
        root = etree.fromstring(pkg.read(theme_part))
        for tag, attr in (("majorFont", "major_font"), ("minorFont", "minor_font")):
            el = root.find(f".//a:{tag}/a:latin", NS)
            if el is not None:
                setattr(profile, attr, el.get("typeface", ""))

        scheme = root.find(".//a:clrScheme", NS)
        if scheme is not None:
            for child in scheme:
                name = etree.QName(child).localname
                srgb = child.find("a:srgbClr", NS)
                if srgb is not None and srgb.get("val"):
                    profile.theme_colors[name] = srgb.get("val").upper()

    for name in sorted(n for n in pkg.parts if n.startswith("ppt/slideLayouts/slideLayout")):
        if not name.endswith(".xml"):
            continue
        root = etree.fromstring(pkg.read(name))
        el = root.find(".//p:cSld", NS)
        if el is not None and el.get("name"):
            profile.layout_names.append(el.get("name"))

    return profile


def check_conformance(deck: DeckInfo, profile: BrandProfile, name: str = "") -> ConformanceReport:
    """Report where a deck departs from its template."""
    report = ConformanceReport(profile=profile, deck=name or "deck")

    off_fonts: dict[str, list[int]] = {}
    off_colours: dict[str, list[int]] = {}
    title_sizes: dict[float, list[int]] = {}

    for slide in deck.slides:
        for shape in slide.shapes:
            is_title = shape.placeholder_type in {"title", "ctrTitle"}
            for run in shape.runs:
                report.checked_runs += 1
                if run.font and profile.fonts and run.font not in profile.fonts:
                    off_fonts.setdefault(run.font, []).append(slide.number)
                if run.color and profile.palette and run.color.upper() not in profile.palette:
                    off_colours.setdefault(run.color.upper(), []).append(slide.number)
                if is_title and run.size_pt:
                    title_sizes.setdefault(run.size_pt, []).append(slide.number)
                    break

    allowed = " or ".join(sorted(profile.fonts)) or "the template fonts"
    for font, slides in sorted(off_fonts.items(), key=lambda kv: -len(kv[1])):
        report.deviations.append(Deviation(
            "font", f"{font!r} is not a template typeface", sorted(set(slides)),
            f"use {allowed}",
        ))

    for colour, slides in sorted(off_colours.items(), key=lambda kv: -len(kv[1])):
        report.deviations.append(Deviation(
            "colour", f"#{colour} is not in the template palette", sorted(set(slides)),
            "use a theme colour so a template change carries through",
        ))

    if len(title_sizes) > 1:
        dominant = max(title_sizes.items(), key=lambda kv: len(kv[1]))[0]
        odd = sorted(
            n for size, slides in title_sizes.items() if size != dominant for n in slides
        )
        report.deviations.append(Deviation(
            "title-size",
            f"titles use {len(title_sizes)} sizes; {dominant:.0f}pt is the deck's norm",
            odd,
            f"set every title to {dominant:.0f}pt",
        ))

    return report


def compare_profiles(a: BrandProfile, b: BrandProfile) -> list[str]:
    """Differences between two templates, for a template migration."""
    notes = []
    if a.major_font != b.major_font:
        notes.append(f"heading font: {a.major_font!r} -> {b.major_font!r}")
    if a.minor_font != b.minor_font:
        notes.append(f"body font: {a.minor_font!r} -> {b.minor_font!r}")
    for key in sorted(set(a.theme_colors) | set(b.theme_colors)):
        before, after = a.theme_colors.get(key), b.theme_colors.get(key)
        if before != after:
            notes.append(f"{key}: #{before or '—'} -> #{after or '—'}")
    return notes


def dominant_fonts(deck: DeckInfo, top: int = 3) -> list[tuple[str, int]]:
    """The typefaces a deck actually uses, most common first."""
    counts = Counter(r.font for s in deck.all_shapes() for r in s.runs if r.font)
    return counts.most_common(top)
