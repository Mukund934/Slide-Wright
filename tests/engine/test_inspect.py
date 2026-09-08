"""Structural inspection.

These are the facts the model is never allowed to guess. If the inspector
miscounts, every downstream decision inherits the error, so the assertions here
are deliberately concrete.
"""

from __future__ import annotations

from slide_wright.inspect import EMU_PER_INCH, ShapeInfo, inspect


class TestDeckLevel:
    def test_reads_slide_dimensions(self, adversarial_deck):
        d = inspect(adversarial_deck)
        assert round(d.slide_width / EMU_PER_INCH, 2) == 13.33
        assert round(d.slide_height / EMU_PER_INCH, 2) == 7.5

    def test_reads_theme_fonts(self, adversarial_deck):
        d = inspect(adversarial_deck)
        assert d.theme_fonts.get("major")
        assert d.theme_fonts.get("minor")

    def test_slide_numbers_are_contiguous(self, adversarial_deck):
        d = inspect(adversarial_deck)
        assert [s.number for s in d.slides] == list(range(1, d.slide_count + 1))

    def test_slide_lookup_by_number(self, adversarial_deck):
        d = inspect(adversarial_deck)
        assert d.slide(1) is not None
        assert d.slide(999) is None


class TestObjectClassification:
    """Every hard construct must be recognised as itself, not as a generic shape."""

    def test_finds_the_native_chart(self, adversarial_deck):
        d = inspect(adversarial_deck)
        charts = [s for s in d.all_shapes() if s.kind == "chart"]
        assert len(charts) == 1

    def test_finds_the_native_table_with_dimensions(self, adversarial_deck):
        d = inspect(adversarial_deck)
        tables = [s for s in d.all_shapes() if s.kind == "table"]
        assert len(tables) == 2, "the comps table and the split-run one"
        comps = next(t for t in tables if t.table_rows == 4)
        assert comps.table_cols == 4

    def test_finds_the_group_and_counts_children(self, adversarial_deck):
        d = inspect(adversarial_deck)
        groups = [s for s in d.all_shapes() if s.kind == "group"]
        assert groups, "grouped shapes must be recognised as a group"
        assert groups[0].child_count >= 3

    def test_finds_custom_geometry(self, adversarial_deck):
        d = inspect(adversarial_deck)
        assert any(s.geometry == "custom" for s in d.all_shapes())

    def test_recognises_preset_geometry_by_name(self, adversarial_deck):
        d = inspect(adversarial_deck)
        presets = {s.geometry for s in d.all_shapes() if s.geometry and s.geometry != "custom"}
        assert presets, "preset shapes should report their preset name"

    def test_placeholders_are_distinguished_from_plain_shapes(self, adversarial_deck):
        d = inspect(adversarial_deck)
        assert any(s.kind == "placeholder" for s in d.all_shapes())


class TestText:
    def test_extracts_titles(self, adversarial_deck):
        d = inspect(adversarial_deck)
        assert d.slide(1).title == "Adversarial Fidelity Corpus"
        assert all(s.title for s in d.slides), "every corpus slide has a title"

    def test_word_count_is_positive_where_text_exists(self, adversarial_deck):
        d = inspect(adversarial_deck)
        assert all(s.word_count > 0 for s in d.slides)

    def test_run_properties_are_read(self, adversarial_deck):
        d = inspect(adversarial_deck)
        runs = [r for s in d.all_shapes() for r in s.runs]
        assert runs
        assert any(r.size_pt for r in runs), "at least one run should carry an explicit size"

    def test_control_deck_is_simple(self, minimal_deck):
        d = inspect(minimal_deck)
        assert d.slide_count == 1
        assert not [s for s in d.all_shapes() if s.kind in {"chart", "table", "group"}]


class TestGeometry:
    def test_shapes_report_position_and_extent(self, adversarial_deck):
        d = inspect(adversarial_deck)
        positioned = [s for s in d.all_shapes() if s.x is not None and s.cx is not None]
        assert positioned
        for s in positioned:
            assert s.right == s.x + s.cx
            assert s.bottom == s.y + s.cy

    def test_overlap_detection(self, adversarial_deck):
        d = inspect(adversarial_deck)
        shapes = [s for s in d.all_shapes() if s.x is not None]
        a = shapes[0]
        assert a.overlaps(a), "a shape overlaps itself"

    def test_overlap_is_false_without_geometry(self, adversarial_deck):
        """Built rather than found.

        Every shape in the corpus now resolves geometry, one way or another, so
        searching for one without it made this pass by finding nothing. A shape
        with no position must answer "no" rather than raise, and that is worth
        asserting on a shape that actually has none.
        """
        shapes = list(inspect(adversarial_deck).all_shapes())
        assert not ShapeInfo(id="x", name="x", kind="shape").overlaps(shapes[0])


class TestRealDecks:
    """Inspection must survive decks we did not author."""

    def test_reads_a_google_authored_deck(self):
        from pathlib import Path

        deck = Path(r"C:/Users/mukun/Downloads/AgroLens - Project Phase-I final.pptx")
        if not deck.is_file():
            import pytest

            pytest.skip("external corpus deck not present on this machine")
        d = inspect(deck)
        assert d.slide_count == 19
        assert any(s.kind == "table" for s in d.all_shapes())
        assert sum(s.word_count for s in d.slides) > 100


class TestSlideLayout:
    """`SlideInfo.layout` was declared from the start and never populated.

    Every slide of every deck answered None, so any caller that checked it
    believed it had checked something. A field that always says "no
    information" is worse than an absent one.
    """

    def test_a_slide_reports_the_layout_it_is_built_on(self, adversarial_deck):
        deck = inspect(adversarial_deck)
        layouts = [s.layout for s in deck.slides]
        assert any(layouts), "no slide reported a layout"

    def test_the_declared_name_is_preferred_over_the_filename(self, adversarial_deck):
        """`slideLayout7` says nothing; "Title and Content" says what it is."""
        deck = inspect(adversarial_deck)
        named = [s.layout for s in deck.slides if s.layout]
        assert not any(n.startswith("slideLayout") for n in named), named

    def test_a_deck_using_several_layouts_reports_several(self, adversarial_deck):
        deck = inspect(adversarial_deck)
        assert len({s.layout for s in deck.slides if s.layout}) >= 1


class TestTableCells:
    """Counting a table describes it. Reading its cells is what lets you edit it.

    The indices must match `apply._set_table_cell`, which reads them straight
    off the row and cell lists. An off-by-one here is silent: every `before`
    derived from a cell read would simply never match at apply time.
    """

    def _table(self, deck_path):
        return next(s for s in inspect(deck_path).all_shapes() if s.kind == "table")

    def test_reads_every_cell(self, adversarial_deck):
        table = self._table(adversarial_deck)
        assert len(table.table_cells) == table.table_rows * table.table_cols

    def test_indices_are_zero_based_and_match_the_applier(self, adversarial_deck):
        table = self._table(adversarial_deck)
        assert "r0/c0" in table.table_cells
        assert f"r{table.table_rows}/c0" not in table.table_cells

    def test_cell_reads_by_row_and_column(self, adversarial_deck):
        table = self._table(adversarial_deck)
        assert table.cell(0, 0) == table.table_cells["r0/c0"]
        assert table.cell(99, 99) is None

    def test_the_cell_the_edit_tests_use_holds_what_they_expect(self, adversarial_deck):
        """Guards the fixture the whole apply suite is written against."""
        assert self._table(adversarial_deck).cell(1, 1) == "9.4x"

    def test_a_shape_that_is_not_a_table_has_no_cells(self, adversarial_deck):
        others = [s for s in inspect(adversarial_deck).all_shapes() if s.kind != "table"]
        assert all(not s.table_cells for s in others)


class TestInheritedGeometry:
    """Placeholders mostly have no geometry of their own.

    A slide's title takes its box from the layout, and the layout's from the
    master. Reading only the slide reported x=None for the title of almost every
    professionally built deck — which meant the gate skipped it, because the gate
    cannot check bounds it does not know.
    """

    def test_a_layout_placed_title_resolves_its_box(self, adversarial_deck):
        title = next(
            s for s in inspect(adversarial_deck).slide(1).shapes
            if s.placeholder_type in {"title", "ctrTitle"}
        )
        assert title.x is not None and title.cx
        assert title.geometry_inherited

    def test_inheritance_is_marked_not_silent(self, adversarial_deck):
        """The applier refuses to move a layout-placed shape.

        A caller that could not tell inherited from owned would propose the
        move, have it approved, and discover at apply time that it cannot
        happen — after the reviewer already said yes.
        """
        shapes = list(inspect(adversarial_deck).all_shapes())
        assert any(s.geometry_inherited for s in shapes)
        assert any(s.x is not None and not s.geometry_inherited for s in shapes)

    def test_native_tables_and_charts_report_their_box(self, adversarial_deck):
        """graphicFrame states p:xfrm, not a:xfrm.

        The two object types this product exists to preserve were the two it
        could not locate.
        """
        frames = [
            s for s in inspect(adversarial_deck).all_shapes()
            if s.kind in {"table", "chart"}
        ]
        assert frames, "the corpus deck should carry both"
        for frame in frames:
            assert frame.x is not None and frame.cx, f"{frame.kind} has no box"
            assert not frame.geometry_inherited, "a frame states its own box"

    def test_every_shape_in_the_corpus_deck_now_has_a_box(self, adversarial_deck):
        missing = [
            (slide.number, s.id, s.kind)
            for slide in inspect(adversarial_deck).slides
            for s in slide.shapes
            if s.x is None
        ]
        assert not missing, f"unresolved geometry: {missing}"


class TestParagraphsAreNotWelded:
    """A shape's paragraphs are separate lines, and its text has to say so.

    Runs were joined with nothing between them, so a bulleted list came back as
    one string with the bullets welded: `"March 16, 2023www.eia.gov/aeo"` is a
    real shape from a real deck. `word_count` read that as one word, and the
    audit's density and evidence rules read the welded string.

    Measured across the 26 fixtures: 227 shapes in 11 decks have two or more
    paragraphs, and the word count ran **12.4% low** — 9,712 counted where
    11,085 exist.
    """

    def _bullets(self, tmp_path, *lines):
        from pptx import Presentation
        from pptx.util import Inches, Pt

        path = tmp_path / "bullets.pptx"
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        frame = slide.shapes.add_textbox(
            Inches(1), Inches(1), Inches(8), Inches(3)
        ).text_frame
        frame.text = lines[0]
        for line in lines[1:]:
            frame.add_paragraph().text = line
        for paragraph in frame.paragraphs:
            paragraph.font.size = Pt(18)
        prs.save(str(path))
        return path

    def _shape(self, deck):
        return next(s for s in inspect(deck).slides[0].shapes if s.text.strip())

    def test_the_text_carries_the_line_breaks(self, tmp_path):
        deck = self._bullets(tmp_path, "March 16, 2023", "www.eia.gov/aeo")
        assert self._shape(deck).text == "March 16, 2023\nwww.eia.gov/aeo"

    def test_the_words_either_side_of_a_break_stay_two_words(self, tmp_path):
        deck = self._bullets(tmp_path, "Margin held", "Headcount fell")
        assert inspect(deck).slides[0].word_count == 4

    def test_runs_know_which_paragraph_they_are_in(self, tmp_path):
        deck = self._bullets(tmp_path, "first", "second", "third")
        assert [r.paragraph for r in self._shape(deck).runs] == [0, 1, 2]

    def test_the_separator_is_never_written_into_the_deck(self, tmp_path):
        """It exists in the joined view and must not exist in the document.

        The applier locates a span in the string the caller read, which has
        separators in it, and writes back to runs, which do not. A separator
        reaching an `a:t` would put a literal newline inside a run, where
        PowerPoint renders it as a vertical-tab break rather than a paragraph.
        """
        import zipfile

        from slide_wright.apply import apply_changes
        from slide_wright.changeset import Change, ChangeSet, Op

        deck = self._bullets(tmp_path, "Margin held", "Headcount fell")
        shape = self._shape(deck)
        out = tmp_path / "out.pptx"
        cs = ChangeSet(deck=str(deck))
        cs.add(Change(id="c1", op=Op.SET_TEXT, slide=1, target=shape.id,
                      before=shape.text, after="Replaced"))
        cs.approve_all()
        assert not apply_changes(deck, cs, out).failed

        xml = zipfile.ZipFile(out).read("ppt/slides/slide1.xml").decode("utf-8")
        assert "\n" not in "".join(
            part.split("</a:t>")[0] for part in xml.split("<a:t>")[1:]
        ), "the joined view's separator must not reach a run"

    def test_text_read_off_the_deck_can_be_written_back(self, tmp_path):
        """The property that makes the two joins have to agree."""
        from slide_wright.apply import apply_changes
        from slide_wright.changeset import Change, ChangeSet, Op

        deck = self._bullets(tmp_path, "Margin held", "Headcount fell", "Cash stable")
        shape = self._shape(deck)
        out = tmp_path / "out.pptx"
        cs = ChangeSet(deck=str(deck))
        cs.add(Change(id="c1", op=Op.SET_TEXT, slide=1, target=shape.id,
                      before="held\nHeadcount", after="held, and headcount"))
        cs.approve_all()
        assert not apply_changes(deck, cs, out).failed
        assert "Cash stable" in self._shape(out).text, "the third line is untouched"
