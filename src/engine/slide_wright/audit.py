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

from slide_wright.brand import is_theme_reference
from slide_wright.gate import GateResult, Severity, check
from slide_wright.inspect import EMU_PER_INCH, DeckInfo, SlideInfo

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


class Remedy(str, Enum):
    """What, if anything, can correct a finding without a human deciding.

    The distinction matters more than it looks. An audit that presents "no slide
    titles make a claim" and "nine runs hardcode a typeface the theme already
    sets" as equally actionable is teaching the reader that its findings are a
    list of complaints. One of those is a judgement about the argument the deck
    is making; the other is arithmetic with a verified fix.

    So the answer lives here, next to the rule that produced the finding, rather
    than in whatever surface happens to be displaying it. A rule added later
    without a remedy is recommendation-only by default, which is the safe way
    round.
    """

    NONE = ""                    # a person has to decide
    CONFORMANCE = "conformance"  # `tidy` / `brand --fix` — typefaces, per run
    ALIGNMENT = "alignment"      # `tidy` / `align --fix` — bounded edge snapping


@dataclass
class Observation:
    """Something true about the deck that its author would want to know."""

    area: Area
    slides: list[int]
    message: str
    suggestion: str = ""
    severity: Severity = Severity.WARNING
    remedy: Remedy = Remedy.NONE

    @property
    def is_automatable(self) -> bool:
        """Whether a deterministic pass can correct this, changing no content."""
        return self.remedy is not Remedy.NONE

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
    #: Words on the slides. Every density rule here is about how much an
    #: audience is asked to read on a page, so notes are counted apart rather
    #: than added in -- folding them together would make a deck with a thorough
    #: script look like a crowded deck.
    word_count: int = 0
    #: Words the presenter wrote underneath. Stated because leaving it out
    #: describes some decks wrongly: `nasa-bhutan-water` reads as 983 words
    #: and carries 2,708 more.
    notes_word_count: int = 0
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
        notes_word_count=sum(s.notes_word_count for s in deck.slides),
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
        _foreign_slides,
        _detached_from_the_template,
        _layout_outliers,
        _duplicated_layouts,
        _typeface_spellings,
        _near_miss_alignment,
        _unsourced_figures,
        _bare_numbers,
        _table_shape,
    ):
        rule(deck, result)
    return result


# ── rules ────────────────────────────────────────────────────────────────────

#: How long a line may be and still read as a heading rather than a sentence.
#: A stated threshold, not a measurement -- like the 10pt floor and the 0.02in
#: alignment tolerance, it has to come from somewhere and this is where.
HEADING_CHARS = 60


def _heading_of(slide) -> str | None:
    """The de facto title: the topmost short line on the slide, if there is one.

    Structural, not a judgement about wording. A shape qualifies when it has its
    own box, holds a single line of at most `HEADING_CHARS`, and nothing else
    with text sits above it. That is the thing a reader's eye lands on first and
    reads as the heading, whether or not PowerPoint calls it a title.
    """
    candidates = [
        s for s in slide.shapes
        if s.has_text and s.y is not None
        and "\n" not in s.text.strip() and len(s.text.strip()) <= HEADING_CHARS
    ]
    if not candidates:
        return None
    top = min(candidates, key=lambda s: s.y)
    others = [
        s for s in slide.shapes
        if s is not top and s.has_text and s.y is not None
    ]
    if any(s.y < top.y for s in others):
        return None
    if not any(s.y > top.y for s in others):
        return None
    return top.text.strip()


def _missing_titles(deck: DeckInfo, out: DeckAudit) -> None:
    """Two findings, because they are two problems with different answers.

    `SlideInfo.title` is a *title placeholder*, which is the strict and correct
    reading. Reporting every slide without one as "have no title" was true and
    unhelpful: measured across the 26 real fixtures, **36 of the 73 slides it
    named do have a heading** -- 24 of the 26 on nasa-bhutan-water, a deck whose
    every slide reads OBJECTIVES, METHODOLOGY, CONCLUSION at the top. Telling
    that author "every slide needs a title" sends them to write titles they can
    see on the screen.

    A slide with no heading at all needs one written. A slide whose heading is an
    ordinary text box has the words already and is missing something else: the
    outline pane, the accessibility tree and a template's title styling all read
    the placeholder, not the position. Neither is automatable -- there is no
    operation that promotes a text box to a placeholder, and choosing which box
    is the title is exactly the judgement this module refuses to make for you.
    """
    bare, unmarked = [], []
    for slide in deck.slides:
        if not slide.shapes or slide.title:
            continue
        (unmarked if _heading_of(slide) else bare).append(slide.number)

    if bare:
        out.observations.append(Observation(
            Area.NARRATIVE, bare,
            f"{len(bare)} slide(s) have no title",
            "a reader skimming the deck sees only titles; every slide needs one",
        ))
    if unmarked:
        out.observations.append(Observation(
            Area.NARRATIVE, unmarked,
            f"{len(unmarked)} slide(s) have a heading that is not a title placeholder",
            "the words are there; PowerPoint does not know they are the title, so "
            "the outline pane, screen readers and a template's title styling all "
            "miss them",
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
            remedy=Remedy.CONFORMANCE,
        ))


def _colour_sprawl(deck: DeckInfo, out: DeckAudit) -> None:
    colours = Counter(
        run.color for shape in deck.all_shapes() for run in shape.runs if run.color
    )
    if len(colours) > 6:
        # Deliberately no remedy. Correcting colour automatically was measured
        # and rejected: on both real decks every off-palette colour sits 10.6-63
        # ΔE from the nearest theme colour against a 2.3 just-noticeable
        # threshold, so there are no near misses to snap to. Position is dragged
        # and lands slightly off; colour is picked, and does not.
        out.observations.append(Observation(
            Area.CONSISTENCY, [],
            f"{len(colours)} explicit text colours are in use",
            "explicit colours override the theme; prefer theme colours so a "
            "template change carries through",
        ))


def _near_miss_alignment(deck: DeckInfo, out: DeckAudit) -> None:
    """Edges that nearly agree, which is worse than edges that plainly do not.

    This check existed as a capability and not as a finding. `layout.py` could
    detect and snap near-miss edges, `tidy` ran it, and the audit -- the command
    whose entire job is answering "what should change?" -- never mentioned it.
    On one real 625-shape deck the planner finds 128 shapes to nudge and the
    audit reported none of them, which made the audit quietly wrong about the
    largest category of defect it can actually do something about.

    Reported through the planner rather than reimplemented, so the number here
    is the number `tidy` would act on. A second implementation would eventually
    disagree with the first, and the disagreement would surface as a fix that
    does not match its own finding.
    """
    from slide_wright.layout import DEFAULT_TOLERANCE_EMU, plan_alignment

    plan = plan_alignment(deck, DEFAULT_TOLERANCE_EMU, out.deck)
    if plan.empty:
        return

    slides = sorted({change.slide for change in plan.changes})
    worst_in = plan.worst_shift_emu / EMU_PER_INCH
    out.observations.append(Observation(
        Area.LAYOUT, slides,
        f"{len(plan.changes)} shape(s) sit within "
        f"{plan.tolerance_emu / EMU_PER_INCH:.2f}in of an edge others share, "
        f"without matching it",
        f"the largest correction would be {worst_in:.3f}in; near-misses read as "
        f"sloppiness where a deliberate offset reads as a choice",
        remedy=Remedy.ALIGNMENT,
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


# ── slides that came from somewhere else ─────────────────────────────────────
#
# The pasted-together deck is the ordinary case, not the exception: people
# start from last quarter's file and pull slides in from three others. The
# deck-level checks above say "five typefaces are in use" and stop there, which
# tells you a deck is inconsistent without telling you where to look.
#
# These name the slides. A slide that carries explicit formatting in a deck
# whose slides otherwise inherit from the theme is the clearest tell there is,
# because inheriting is what a slide built in this deck's own template does.


def _foreign_slides(deck: DeckInfo, out: DeckAudit) -> None:
    """Slides carrying hardcoded fonts where the rest of the deck inherits."""
    explicit: dict[int, set[str]] = {}
    for slide in deck.slides:
        fonts = {
            run.font
            for shape in slide.shapes
            for run in shape.runs
            # A theme reference is not a hardcoded font -- it is the slide
            # deferring to the template, which is the opposite of foreign.
            if run.font and not run.font.startswith("+")
        }
        if fonts:
            explicit[slide.number] = fonts

    if not explicit or len(explicit) >= deck.slide_count:
        # Either nothing is hardcoded, or everything is. Neither identifies an
        # outlier, and calling a whole deck foreign to itself is not a finding.
        return

    share = len(explicit) / max(deck.slide_count, 1)
    if share > 0.5:
        return

    fonts = sorted({f for fs in explicit.values() for f in fs})
    out.observations.append(Observation(
        Area.CONSISTENCY,
        sorted(explicit),
        f"{len(explicit)} of {deck.slide_count} slides hardcode a typeface "
        f"({', '.join(fonts)}) where the rest inherit from the theme",
        "slides pasted in from another deck usually look like this; conforming "
        "them re-links their text to this deck's template",
        # The advice above already describes the conformance pass, and the pass
        # already fixes exactly this -- measured on tspptx-mixed.pptx, where the
        # planner had three corrections for this finding and the finding said a
        # person had to make them. A deck whose only automatable problem was
        # this one showed no way to fix it at all.
        remedy=Remedy.CONFORMANCE,
    ))


def _layout_outliers(deck: DeckInfo, out: DeckAudit) -> None:
    """Slides built on a layout almost nothing else in the deck uses."""
    layouts = Counter(s.layout for s in deck.slides if s.layout)
    if len(layouts) < 2 or sum(layouts.values()) < 4:
        return

    rare = {name for name, n in layouts.items() if n == 1}
    if not rare or len(rare) == len(layouts):
        # All-singletons means the deck simply uses many layouts, which is a
        # style, not a defect.
        return

    # The first and last slides are a title and a closing slide by convention,
    # so a layout used only there is expected rather than suspicious. Flagging
    # them was a real false positive on the corpus deck, and a user told
    # something wrong about their own deck stops believing the rest of the
    # report. Matching on layout *names* would catch these too and would break
    # on the first deck authored in another language -- one fixture reports
    # "Diapositive de titre".
    structural = {1, deck.slide_count}
    slides = sorted(
        s.number for s in deck.slides
        if s.layout in rare and s.number not in structural
    )
    if not slides:
        return
    if len(slides) > deck.slide_count / 3:
        return

    # Name only the layouts belonging to the slides actually reported. `rare`
    # can include a layout whose single slide was excluded as structural, and
    # listing it made the message describe slides that are not in the finding:
    # three layout names for two slides, on two different real decks.
    named = sorted({
        s.layout for s in deck.slides
        if s.number in set(slides) and s.layout
    })
    out.observations.append(Observation(
        Area.CONSISTENCY,
        slides,
        f"{len(slides)} slide(s) use a layout no other slide uses "
        f"({', '.join(named)})",
        "worth a look: a one-off layout is often a slide brought in from "
        "another deck, though it may equally be a deliberate divider",
    ))


def _typeface_spellings(deck: DeckInfo, out: DeckAudit) -> None:
    """The same typeface, typed more than one way.

    Found on a real deck: 177 runs in "Century Gothic" and 89 in "Century
    gothic". To a reader they are the same font; to the file they are two
    different strings, and to anyone counting deviations they look like two
    separate problems rather than one.

    It is also a strong tell that a deck was assembled by several people, which
    is the thing the checks around this one are trying to surface.
    """
    spellings: dict[str, set[str]] = {}
    for shape in deck.all_shapes():
        for run in shape.runs:
            if run.font and not run.font.startswith("+"):
                spellings.setdefault(run.font.strip().casefold(), set()).add(run.font)

    inconsistent = {k: v for k, v in spellings.items() if len(v) > 1}
    if not inconsistent:
        return

    detail = "; ".join(
        " / ".join(f"{name!r}" for name in sorted(variants))
        for variants in inconsistent.values()
    )
    out.observations.append(Observation(
        Area.CONSISTENCY, [],
        f"{len(inconsistent)} typeface(s) are spelled more than one way: {detail}",
        "the same font typed differently by different people; harmless to look "
        "at, but it doubles every count that groups by typeface",
        remedy=Remedy.CONFORMANCE,
    ))


def _detached_from_the_template(deck: DeckInfo, out: DeckAudit) -> None:
    """Most of the deck's text hardcodes a typeface instead of deferring to the theme.

    `_foreign_slides` names the odd slides out and deliberately goes quiet once
    more than half the deck hardcodes, because a deck that is consistently
    hardcoded is consistent -- it is not foreign to itself. That left the decks
    with the *most* template drift saying nothing at all: one fixture mixes
    three typefaces across 111 of its 149 runs and produced no typeface finding.

    So this picks up exactly where that check stops. The threshold is the same
    one, so between them the two partition rather than overlap or leave a gap.

    Measured across the corpus, the two populations are far apart: decks that
    defer to the theme sit at 0-14% hardcoded, and decks that do not sit at
    74-100%. Nothing lands near the boundary.

    The finding is deliberately factual rather than a judgement. Hardcoding is
    not wrong; it just means a later template change will not reach this text.
    """
    runs = [run for shape in deck.all_shapes() for run in shape.runs]
    if len(runs) < 20:
        return

    hardcoded = [
        run for run in runs
        if run.font and not is_theme_reference(run.font)
    ]
    share = len(hardcoded) / len(runs)
    if share <= 0.5:
        return

    fonts = sorted({run.font for run in hardcoded})
    listed = ", ".join(fonts[:4]) + (" …" if len(fonts) > 4 else "")
    out.observations.append(Observation(
        Area.CONSISTENCY, [],
        f"{len(hardcoded)} of {len(runs)} text runs ({share:.0%}) name a typeface "
        f"directly rather than deferring to the theme: {listed}",
        "not wrong in itself, but a later template change will not reach any of "
        "them; `brand --fix` re-links them without altering a word",
        remedy=Remedy.CONFORMANCE,
    ))


# PowerPoint renames a layout to "2_Something" when it has to add a second copy
# of a layout that already exists in the master. That happens when a slide
# arrives from another deck and brings its own layout with it.
DUPLICATED_LAYOUT = re.compile(r"^\d+_")


def _duplicated_layouts(deck: DeckInfo, out: DeckAudit) -> None:
    """Layouts PowerPoint itself marked as duplicates.

    Every other check here infers that a slide came from elsewhere -- it
    hardcodes a typeface, it uses a layout nothing else uses. This one does
    not infer anything: the numeric prefix is PowerPoint's own record that it
    had to keep two layouts of the same name, which is what happens when
    content is pasted in from another file.

    Because it is direct evidence rather than an inference, the first and last
    slides are *not* excluded here. They are excluded elsewhere because a
    unique title layout is expected by convention; a *duplicated* one is not
    explained by convention at all.
    """
    marked: dict[str, list[int]] = {}
    for slide in deck.slides:
        if slide.layout and DUPLICATED_LAYOUT.match(slide.layout):
            marked.setdefault(slide.layout, []).append(slide.number)
    if not marked:
        return

    slides = sorted(n for numbers in marked.values() for n in numbers)
    listed = ", ".join(f"{name!r}" for name in sorted(marked))
    out.observations.append(Observation(
        Area.CONSISTENCY,
        slides,
        f"{len(slides)} slide(s) use a duplicated layout ({listed})",
        "PowerPoint names a layout this way when it has to keep a second copy "
        "of one that already exists, which is what happens when a slide is "
        "brought in from another deck",
    ))
