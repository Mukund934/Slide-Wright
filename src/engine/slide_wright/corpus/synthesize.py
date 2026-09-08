"""Build adversarial test decks deterministically.

Real complex decks are confidential, and public ones are inconsistent. So the
corpus needs a reproducible half: decks that deliberately contain the constructs
that break naive engines, generated the same way on every machine.

What this can build: native charts backed by real embedded workbooks, grouped
shapes, native tables, custom geometry, multiple masters and layouts, images,
hyperlinks, speaker notes.

What it cannot build: SmartArt. python-pptx has no API for `ppt/diagrams/`
parts, and hand-rolling the four correlated XML parts a diagram needs would test
our own forgery rather than PowerPoint's real output. SmartArt coverage
therefore has to come from a genuine deck — tracked as an explicit corpus gap
rather than quietly skipped.
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.dml.color import RGBColor
from pptx.util import Emu, Inches, Pt

BRAND = RGBColor(0x0B, 0x5D, 0x3B)
MUTED = RGBColor(0x6B, 0x72, 0x80)


def build_adversarial(out_path: str | Path) -> Path:
    """A deck containing every hard construct we can legitimately generate."""
    out_path = Path(out_path)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)

    _slide_title(prs)
    _slide_native_chart(prs)          # ppt/charts + ppt/embeddings
    _slide_table(prs)                 # <a:tbl>
    _slide_grouped_shapes(prs)        # <p:grpSp>
    _slide_custom_geometry(prs)       # <a:custGeom>
    _slide_hyperlinks_and_notes(prs)  # <a:hlinkClick> + notesSlide
    _slide_split_runs(prs)            # one sentence, several <a:r>

    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))
    return out_path


# ── slides ───────────────────────────────────────────────────────────────────

def _source_note(slide, text: str) -> None:
    """Attribute a data slide.

    A chart or table without a source line is a finding in our own audit, so
    the reference deck should not commit the error it is used to detect.
    """
    box = slide.shapes.add_textbox(Inches(1), Inches(6.8), Inches(8), Inches(0.4))
    box.text_frame.text = text
    box.text_frame.paragraphs[0].font.size = Pt(11)
    box.text_frame.paragraphs[0].font.color.rgb = MUTED


def _slide_title(prs: Presentation) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "Adversarial Fidelity Corpus"
    s.placeholders[1].text = "Every construct that breaks a naive engine"


def _slide_native_chart(prs: Presentation) -> None:
    """A real PowerPoint chart: its own part, plus an embedded xlsx workbook.

    This is the construct ADR-0006 is about. If an edit turns this into a
    picture, the numbers on the slide become an image of last quarter's numbers.
    """
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Revenue by quarter (native chart)"

    data = CategoryChartData()
    data.categories = ["Q1", "Q2", "Q3", "Q4"]
    data.add_series("FY25", (3.1, 3.6, 3.9, 4.2))
    data.add_series("FY26", (4.4, 5.1, 5.8, 6.6))

    s.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Inches(1), Inches(1.8), Inches(11), Inches(4.8),
        data,
    )
    _source_note(s, "Source: management accounts, FY26 (unaudited)")


def _slide_table(prs: Presentation) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Trading comparables (native table)"

    rows, cols = 4, 4
    tbl = s.shapes.add_table(
        rows, cols, Inches(1), Inches(1.9), Inches(11), Inches(2.6)
    ).table
    values = [
        ["Company", "EV/EBITDA", "Margin", "Growth"],
        ["Alpha Corp", "9.4x", "22.1%", "18%"],
        ["Beta Industries", "11.2x", "19.8%", "12%"],
        ["Gamma Holdings", "8.7x", "24.5%", "21%"],
    ]
    for r, row in enumerate(values):
        for c, val in enumerate(row):
            cell = tbl.cell(r, c)
            cell.text = val
            para = cell.text_frame.paragraphs[0]
            para.font.size = Pt(14)
            if r == 0:
                para.font.bold = True
    _source_note(s, "Source: broker comps, 30 June 2026")


def _slide_split_runs(prs: Presentation) -> None:
    """Text a reader sees as one thing and OOXML stores as several.

    This is the construct that hid four defects through five hardening passes,
    and it is the most ordinary thing on this deck: a sentence with a figure
    emphasised in it, and a table cell whose number is not one run.

    Every fidelity and narrowness test swept over a corpus where a paragraph was
    a run and a cell was a run, so "the edit touched only the target" was being
    measured at a granularity where it could not fail. Replacing two words at the
    front of a paragraph unbolded the figure behind it; refreshing a cell wrote
    1,987 beside the 34 left over from 1,234; a change addressed at one run
    resized the whole shape. None of it was visible without this slide.

    Run boundaries land inside a value for entirely mundane reasons -- part of a
    number gets emphasised, a spell-check language boundary falls mid-token, text
    is pasted in two pieces -- so a deck without them is not a simpler deck. It
    is a deck that has not been asked the question.
    """
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Split runs (one value, several runs)"

    frame = s.shapes.add_textbox(
        Inches(1), Inches(1.9), Inches(11), Inches(1)
    ).text_frame
    for text, bold in (("Revenue grew ", False), ("15%", True), (" in FY25", False)):
        run = frame.paragraphs[0].add_run()
        run.text = text
        run.font.bold = bold
        run.font.size = Pt(20)

    tbl = s.shapes.add_table(
        2, 2, Inches(1), Inches(3.2), Inches(6), Inches(1.4)
    ).table
    tbl.cell(0, 0).text = "Metric"
    tbl.cell(0, 1).text = "FY25"
    tbl.cell(1, 0).text = "Revenue"
    cell = tbl.cell(1, 1).text_frame.paragraphs[0]
    cell.text = ""
    for text, bold in (("1,2", False), ("34", True)):
        run = cell.add_run()
        run.text = text
        run.font.bold = bold
        run.font.size = Pt(14)

    _source_note(s, "Source: synthetic, for run-boundary coverage")


def _slide_grouped_shapes(prs: Presentation) -> None:
    """Nested groups. Naive engines flatten these or lose the nesting."""
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Process flow (grouped shapes)"

    shapes = s.shapes
    members = []
    for i in range(3):
        left = Inches(1 + i * 4)
        box = shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, left, Inches(2.6), Inches(3), Inches(1.4)
        )
        box.fill.solid()
        box.fill.fore_color.rgb = BRAND
        box.line.color.rgb = BRAND
        box.text_frame.text = f"Stage {i + 1}"
        box.text_frame.paragraphs[0].font.size = Pt(18)
        members.append(box)

        if i < 2:
            arrow = shapes.add_shape(
                MSO_SHAPE.RIGHT_ARROW,
                Inches(4.05 + i * 4), Inches(3.05), Inches(0.85), Inches(0.5),
            )
            arrow.fill.solid()
            arrow.fill.fore_color.rgb = MUTED
            members.append(arrow)

    # python-pptx exposes grouping via the shape tree; group everything we made.
    if hasattr(shapes, "add_group_shape"):
        group = shapes.add_group_shape(members)
        group.name = "ProcessFlowGroup"


def _slide_custom_geometry(prs: Presentation) -> None:
    """Freeform paths become <a:custGeom>, not a preset shape."""
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Trend (custom geometry)"

    builder = s.shapes.build_freeform(Emu(Inches(1.5).emu), Emu(Inches(5.5).emu))
    points = [
        (Inches(3.5).emu, Inches(4.6).emu),
        (Inches(5.5).emu, Inches(5.0).emu),
        (Inches(7.5).emu, Inches(3.4).emu),
        (Inches(9.5).emu, Inches(3.8).emu),
        (Inches(11.5).emu, Inches(2.4).emu),
    ]
    builder.add_line_segments([(Emu(x), Emu(y)) for x, y in points], close=False)
    shape = builder.convert_to_shape()
    shape.line.color.rgb = BRAND
    shape.line.width = Pt(3)


def _slide_hyperlinks_and_notes(prs: Presentation) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Sources and notes"

    box = s.shapes.add_textbox(Inches(1), Inches(2.2), Inches(9), Inches(1.2))
    tf = box.text_frame
    run = tf.paragraphs[0].add_run()
    run.text = "Methodology reference"
    run.hyperlink.address = "https://example.com/methodology"
    run.font.size = Pt(18)

    footnote = s.shapes.add_textbox(Inches(1), Inches(6.3), Inches(9), Inches(0.5))
    footnote.text_frame.text = "Source: internal MIS, unaudited"
    footnote.text_frame.paragraphs[0].font.size = Pt(12)
    footnote.text_frame.paragraphs[0].font.color.rgb = MUTED

    s.notes_slide.notes_text_frame.text = (
        "Speaker notes exist so notesSlide parts are present in the package. "
        "An engine that drops these loses the presenter's script."
    )


def build_minimal(out_path: str | Path) -> Path:
    """A deliberately trivial deck — the control.

    If an engine passes only on this, it has proven nothing. Its difficulty
    score should be 0.
    """
    out_path = Path(out_path)
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "Minimal control deck"
    s.placeholders[1].text = "Text and placeholders only"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))
    return out_path


# ── the untidy deck ──────────────────────────────────────────────────────────
#
# Near-misses, in EMU. 0.02in is the alignment tolerance, so these must land
# inside it and above the 1/3600in noise floor: a difference smaller than that
# is rounding, and snapping it would churn the file for no visible gain.
NUDGE = Emu(9144)      # 0.01in — half the tolerance
TINY_NUDGE = Emu(4572)  # 0.005in — a quarter of it


def build_untidy(out_path: str | Path) -> Path:
    """A deck assembled from several sources, carrying what that leaves behind.

    The corpus had no deck that needed tidying, which meant every test of the
    wedge — *change every pixel of formatting, change not one word or number,
    and prove it* — either skipped or ran against a real third-party fixture
    that not every machine has. A test that skips is a test that is not run.

    Two defects, both the ordinary kind rather than a contrivance:

      · **Hardcoded typefaces, spelled inconsistently.** Text pasted from
        another deck brings its font as an explicit override, and different
        people type the same font differently. Found on a real deck: 177 runs in
        "Century Gothic" and 89 in "Century gothic".
      · **Edges that nearly agree.** Boxes dragged into place and then nudged
        with the arrow keys end up within a hair of a line they were meant to
        share. Every offset here is inside the alignment tolerance, so a
        correction is real and none of it is visible to the eye.

    Every slide also carries words and numbers, because the guarantee under test
    is that tidying leaves both untouched. A deck with nothing to preserve
    cannot demonstrate preservation.
    """
    out_path = Path(out_path)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)

    _untidy_pasted_slide(prs)
    _untidy_nearly_aligned(prs)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))
    return out_path


def _untidy_pasted_slide(prs: Presentation) -> None:
    """Text carrying an explicit typeface, typed two ways."""
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Q3 performance"

    for index, (typeface, text) in enumerate(
        [
            ("Century Gothic", "Revenue reached 42.7m, up 18% on the quarter"),
            ("Century gothic", "Margin held at 22.1% against a 21.4% plan"),
            ("Century Gothic", "Headcount closed at 312, against 300 budgeted"),
        ]
    ):
        box = s.shapes.add_textbox(
            Inches(1), Inches(2 + index * 0.9), Inches(11), Inches(0.7)
        )
        run = box.text_frame.paragraphs[0].add_run()
        run.text = text
        run.font.name = typeface   # explicit override — the thing conformance corrects
        run.font.size = Pt(16)
        run.font.color.rgb = MUTED

    _source_note(s, "Source: management accounts, Q3 2026")


def _untidy_nearly_aligned(prs: Presentation) -> None:
    """Boxes that share an edge, except for the ones that nearly do.

    Three left edges agree exactly and two miss by less than the tolerance, so
    the majority establishes the line and the strays snap onto it. That shape
    matters: the aligner only ever moves something *onto* a line others already
    sit on, so a deck where every edge is unique has nothing to correct.
    """
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Workstreams"

    left = Inches(1.5)
    offsets = [0, 0, 0, NUDGE, TINY_NUDGE]
    labels = [
        "Discovery — 4 weeks, 2 FTE",
        "Build — 11 weeks, 5 FTE",
        "Pilot — 6 weeks, 3 FTE",
        "Rollout — 9 weeks, 4 FTE",
        "Review — 2 weeks, 1 FTE",
    ]
    for index, (offset, label) in enumerate(zip(offsets, labels)):
        box = s.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Emu(left + offset), Inches(1.8 + index * 0.95),
            Inches(9), Inches(0.75),
        )
        box.fill.solid()
        box.fill.fore_color.rgb = BRAND
        frame = box.text_frame
        frame.text = label
        frame.paragraphs[0].font.size = Pt(14)

    _source_note(s, "Source: delivery plan, revision 7")
