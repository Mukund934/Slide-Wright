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

    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))
    return out_path


# ── slides ───────────────────────────────────────────────────────────────────

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
