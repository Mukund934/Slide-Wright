"""In-place application of approved changes.

The property under test is narrowness: an edit must touch the target and
nothing else. Slide-level preservation is table stakes here — these tests
assert preservation *within* the edited slide, down to the character.
"""

from __future__ import annotations

import difflib
import zipfile

import pytest

from slide_wright.apply import ApplyError, apply_changes
from slide_wright.changeset import Change, ChangeSet, Op
from slide_wright.fidelity import compare
from slide_wright.inspect import inspect
from slide_wright.package import Package


def slide_body(path, part: str) -> str:
    raw = zipfile.ZipFile(path).read(part).decode("utf-8")
    return raw[raw.index("<p:sld"):]


def approved(deck, *changes: Change) -> ChangeSet:
    cs = ChangeSet(deck=str(deck))
    for c in changes:
        cs.add(c)
    cs.approve_all()
    return cs


@pytest.fixture
def table_shape(adversarial_deck):
    return next(s for s in inspect(adversarial_deck).all_shapes() if s.kind == "table")


class TestNarrowness:
    def test_a_cell_edit_changes_two_characters(self, adversarial_deck, table_shape, tmp_path):
        """The strongest form of the promise, measured rather than asserted."""
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table_shape.id}/r1/c1", before="9.4x", after="11.8x",
        ))
        apply_changes(adversarial_deck, cs, out)

        before = slide_body(adversarial_deck, "ppt/slides/slide3.xml")
        after = slide_body(out, "ppt/slides/slide3.xml")
        matcher = difflib.SequenceMatcher(None, before, after)
        edits = [op for op in matcher.get_opcodes() if op[0] != "equal"]
        preserved = sum(b.size for b in matcher.get_matching_blocks()) / len(before)

        assert len(edits) <= 3, f"expected a surgical edit, got {edits}"
        assert preserved > 0.99, f"only {preserved:.2%} of the edited slide preserved"

    def test_only_the_target_slide_part_is_touched(self, adversarial_deck, table_shape, tmp_path):
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table_shape.id}/r1/c1", before="9.4x", after="11.8x",
        ))
        result = apply_changes(adversarial_deck, cs, out)
        assert result.touched_parts == {"ppt/slides/slide3.xml"}
        assert [d.name for d in compare(adversarial_deck, out).changed] == [
            "ppt/slides/slide3.xml"
        ]

    def test_run_count_is_unchanged_by_a_text_edit(self, adversarial_deck, tmp_path):
        deck_info = inspect(adversarial_deck)
        title = deck_info.slide(1).shapes[0]
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TEXT, slide=1, target=title.id,
            before=title.text, after="Renamed Corpus",
        ))
        apply_changes(adversarial_deck, cs, out)
        rep = compare(adversarial_deck, out)
        assert rep.output_census.text_runs == rep.source_census.text_runs


def movable(deck, slide_number: int):
    """A plain shape with geometry of its own, on the given slide.

    Three things disqualify a shape here and each is a real engine rule, not a
    fixture convenience: a placeholder is positioned by its layout and the
    applier refuses to move it; a chart is read-only (ADR-0009); and SmartArt is
    read-only (ADR-0007). Picking "the first shape with an x" used to land on
    whichever of those came first and made the test assert the wrong refusal.
    """
    return next(
        s for s in inspect(deck).slide(slide_number).shapes
        if s.kind == "shape" and s.x is not None and not s.geometry_inherited
    )


class TestOperations:
    def test_set_text_writes_the_new_value(self, adversarial_deck, tmp_path):
        d = inspect(adversarial_deck)
        title = d.slide(1).shapes[0]
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TEXT, slide=1, target=title.id,
            before=title.text, after="Renamed Corpus",
        ))
        apply_changes(adversarial_deck, cs, out)
        assert inspect(out).slide(1).title == "Renamed Corpus"

    def test_set_table_cell_writes_the_new_value(self, adversarial_deck, table_shape, tmp_path):
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table_shape.id}/r1/c1", before="9.4x", after="11.8x",
        ))
        apply_changes(adversarial_deck, cs, out)
        assert "11.8x" in inspect(out).slide(3).text
        assert "9.4x" not in inspect(out).slide(3).text

    def test_move_updates_offset(self, adversarial_deck, tmp_path):
        shape = movable(adversarial_deck, 6)
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.MOVE, slide=6, target=shape.id,
            before=(shape.x, shape.y), after=(shape.x + 100000, shape.y),
        ))
        apply_changes(adversarial_deck, cs, out)
        moved = next(s for s in inspect(out).slide(6).shapes if s.id == shape.id)
        assert moved.x == shape.x + 100000

    def test_resize_updates_extent(self, adversarial_deck, tmp_path):
        shape = movable(adversarial_deck, 6)
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.RESIZE, slide=6, target=shape.id,
            before=(shape.cx, shape.cy), after=(shape.cx // 2, shape.cy),
        ))
        apply_changes(adversarial_deck, cs, out)
        resized = next(s for s in inspect(out).slide(6).shapes if s.id == shape.id)
        assert resized.cx == shape.cx // 2


class TestRefusals:
    """Fail closed. Never approximate a change we cannot make precisely."""

    def test_unsupported_op_is_refused_not_approximated(self, adversarial_deck, tmp_path):
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.DELETE_SHAPE, slide=1, target="2",
        ))
        with pytest.raises(ApplyError, match="cannot perform"):
            apply_changes(adversarial_deck, cs, tmp_path / "o.pptx")

    def test_empty_change_set_is_refused(self, adversarial_deck, tmp_path):
        with pytest.raises(ApplyError, match="no approved changes"):
            apply_changes(adversarial_deck, ChangeSet(deck=str(adversarial_deck)),
                          tmp_path / "o.pptx")

    def test_rejected_changes_are_not_applied(self, adversarial_deck, tmp_path):
        d = inspect(adversarial_deck)
        title = d.slide(1).shapes[0]
        cs = ChangeSet(deck=str(adversarial_deck))
        cs.add(Change(id="c1", op=Op.SET_TEXT, slide=1, target=title.id,
                      before=title.text, after="Should Not Appear"))
        cs.add(Change(id="c2", op=Op.SET_TEXT, slide=1, target=title.id,
                      before=title.text, after="Should Appear"))
        cs.approve("c2")
        cs.reject("c1")
        out = tmp_path / "o.pptx"
        apply_changes(adversarial_deck, cs, out)
        assert inspect(out).slide(1).title == "Should Appear"

    def test_missing_slide_is_recorded_as_failure(self, adversarial_deck, tmp_path):
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TEXT, slide=999, target="1", before="x", after="y",
        ))
        result = apply_changes(adversarial_deck, cs, tmp_path / "o.pptx")
        assert not result.ok
        assert "not found" in result.failed[0][1]

    def test_missing_target_is_recorded_as_failure(self, adversarial_deck, tmp_path):
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TEXT, slide=1, target="99999", before="x", after="y",
        ))
        result = apply_changes(adversarial_deck, cs, tmp_path / "o.pptx")
        assert not result.ok


class TestSourceIsImmutable:
    def test_the_source_file_is_never_modified(self, adversarial_deck, table_shape, tmp_path):
        original = adversarial_deck.read_bytes()
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table_shape.id}/r1/c1", before="9.4x", after="11.8x",
        ))
        apply_changes(adversarial_deck, cs, tmp_path / "o.pptx")
        assert adversarial_deck.read_bytes() == original


class TestIntegrityPreserved:
    def test_native_objects_survive_an_edit(self, adversarial_deck, table_shape, tmp_path):
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table_shape.id}/r1/c1", before="9.4x", after="11.8x",
        ))
        apply_changes(adversarial_deck, cs, out)
        rep = compare(adversarial_deck, out)
        assert not rep.native_losses
        assert not rep.rasterisation_suspected
        assert rep.structurally_intact

    def test_output_is_a_valid_package(self, adversarial_deck, table_shape, tmp_path):
        out = tmp_path / "o.pptx"
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=3,
            target=f"{table_shape.id}/r1/c1", before="9.4x", after="11.8x",
        ))
        apply_changes(adversarial_deck, cs, out)
        assert Package.open(out).slide_count == Package.open(adversarial_deck).slide_count


class TestFailuresExplainThemselves:
    """A refusal is only useful if it says what to do differently.

    `_apply_one` returned a single boolean, so "the shape is not on this slide"
    and "the shape is here but the edit does not apply to it" produced the same
    message: "target not found on slide". That sends a reader hunting for a
    shape id that is in fact present, and it fired on the common case of a run
    whose font size is inherited from the layout.
    """

    def _why(self, deck, change, tmp_path) -> str:
        cs = approved(deck, change)
        result = apply_changes(deck, cs, tmp_path / "out.pptx")
        assert result.failed, "expected this change to fail"
        return result.failed[0][1]

    def test_a_missing_shape_says_so_and_names_the_slide(self, adversarial_deck, tmp_path):
        why = self._why(adversarial_deck, Change(
            id="c1", op=Op.SET_TEXT, slide=1, target="999",
            before="x", after="y"), tmp_path)
        assert "no shape with id '999'" in why and "slide 1" in why

    def test_wrong_before_text_names_the_text_not_the_shape(self, adversarial_deck, tmp_path):
        shape = next(s for s in inspect(adversarial_deck).slides[0].shapes if s.has_text)
        why = self._why(adversarial_deck, Change(
            id="c1", op=Op.SET_TEXT, slide=1, target=shape.id,
            before="text that is not in this deck", after="y"), tmp_path)
        assert "does not contain the text" in why
        assert "not found" not in why, "the shape was found; saying otherwise misleads"

    def test_an_inherited_font_size_is_explained_not_denied(self, adversarial_deck, tmp_path):
        """The commonest real case: a run with no explicit formatting override."""
        shape = movable(adversarial_deck, 2)
        why = self._why(adversarial_deck, Change(
            id="c1", op=Op.SET_FONT_SIZE, slide=2, target=shape.id,
            before=None, after=14), tmp_path)
        assert "inherited from the layout" in why
        assert "not found" not in why

    def test_a_layout_placed_shape_explains_why_it_cannot_move(
        self, adversarial_deck, tmp_path
    ):
        # Layout-placed is now visible as such rather than inferred from a
        # missing x: inspect resolves inherited geometry, so the shape has
        # coordinates and still has no position of its own.
        shape = next(s for s in inspect(adversarial_deck).slides[0].shapes
                     if s.geometry_inherited)
        why = self._why(adversarial_deck, Change(
            id="c1", op=Op.MOVE, slide=1, target=shape.id,
            before=(0, 0), after=(100, 100)), tmp_path)
        assert "placed by the layout" in why
        assert "not found" not in why


class TestRunAddressingMatchesInspect:
    """`<shape>/run/<index>` must mean the same run to both halves of the system.

    The index in a target is produced by reading the deck through `inspect`,
    which skips runs carrying no text. The applier used to enumerate every run,
    so a shape with two empty runs made inspect see fifteen where the applier
    saw seventeen — and a change addressed at run 13 was written to a different
    run entirely, while reporting success.

    Every check passed: content untouched, no native lost, fidelity fine. Only
    running the pass twice caught it, because the run that should have changed
    never did and kept being proposed again.
    """

    def _shape_element(self, deck, slide_part: str, shape_id: str):
        from lxml import etree

        from slide_wright.apply import NS
        from slide_wright.package import Package

        root = etree.fromstring(Package.open(deck).read(slide_part))
        for el in root.iter():
            if etree.QName(el).localname == "cNvPr" and el.get("id") == shape_id:
                return el.getparent().getparent()
        return None

    def test_the_applier_enumerates_what_inspect_enumerates(self, adversarial_deck):
        from slide_wright.apply import _addressable_runs

        deck = inspect(adversarial_deck)
        for slide in deck.slides:
            for shape in slide.shapes:
                if not shape.runs:
                    continue
                element = self._shape_element(adversarial_deck, slide.part_name, shape.id)
                if element is None:
                    continue
                assert len(_addressable_runs(element)) == len(shape.runs), (
                    f"slide {slide.number} shape {shape.id}: inspect sees "
                    f"{len(shape.runs)} runs, the applier sees "
                    f"{len(_addressable_runs(element))}"
                )

    def test_an_empty_run_is_not_addressable(self):
        from lxml import etree

        from slide_wright.apply import _addressable_runs

        xml = (
            '<p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
            ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:p>'
            '<a:r><a:rPr/><a:t>first</a:t></a:r>'
            '<a:r><a:rPr/><a:t></a:t></a:r>'
            '<a:r><a:rPr/><a:t>second</a:t></a:r>'
            '</a:p></p:sp>'
        )
        runs = _addressable_runs(etree.fromstring(xml))
        assert len(runs) == 2, "an empty run must not consume an index"
        assert runs[1].find(
            "{http://schemas.openxmlformats.org/drawingml/2006/main}t"
        ).text == "second"


class TestMovingIsNotEditing:
    """The content guards were refusing an operation they have no argument against.

    A chart and a diagram each keep the same information twice — cached series
    against an embedded workbook, a diagram model against its drawing cache —
    and both guards exist because an edit that updates one copy and not the
    other produces a deck whose picture disagrees with its own data. That is
    the whole justification, and it is about content.

    Neither cache records where on the slide the frame sits. Refusing a move on
    those grounds cost more than it sounds: the alignment pass emits `MOVE` and
    nothing else, so one chart anywhere in a deck made `tidy` refuse the entire
    change set. Which is the demo.
    """

    CHART = (2, "3")   # slide, shape id — a native chart in the fixture
    TABLE = (3, "3")   # a native table

    def _move(self, deck, slide, target, tmp_path, name="moved.pptx"):
        pkg_shape = next(s for s in inspect(deck).slides[slide - 1].shapes
                         if s.id == target)
        cs = approved(deck, Change(
            id="m1", op=Op.MOVE, slide=slide, target=target,
            before=(pkg_shape.x, pkg_shape.y),
            after=(pkg_shape.x + 12700, pkg_shape.y),
        ))
        return apply_changes(deck, cs, tmp_path / name), pkg_shape

    def test_a_chart_frame_can_be_moved(self, adversarial_deck, tmp_path):
        result, _ = self._move(adversarial_deck, *self.CHART, tmp_path)
        assert result.ok, result.failed

    def test_the_move_actually_lands(self, adversarial_deck, tmp_path):
        """A graphicFrame states its box as `p:xfrm`, not `a:xfrm`.

        Looking only for the DrawingML form did not raise. It returned "shape 3
        has no position of its own; it is placed by the layout" — untrue of a
        graphicFrame, and it sends a reviewer looking for a layout that does not
        place it.
        """
        result, before = self._move(adversarial_deck, *self.CHART, tmp_path)
        after = next(s for s in inspect(result.output).slides[1].shapes if s.id == "3")
        assert after.x == before.x + 12700
        assert after.y == before.y

    def test_a_table_frame_can_be_moved_too(self, adversarial_deck, tmp_path):
        result, _ = self._move(adversarial_deck, *self.TABLE, tmp_path, "table.pptx")
        assert result.ok, result.failed

    def test_moving_a_chart_leaves_the_chart_part_untouched(self, adversarial_deck, tmp_path):
        """The reason the guard has no argument here, asserted rather than argued."""
        result, _ = self._move(adversarial_deck, *self.CHART, tmp_path, "parts.pptx")
        rep = compare(adversarial_deck, result.output)
        assert not [d.name for d in rep.changed if d.name.startswith("ppt/charts/")]
        assert not [d.name for d in rep.changed if d.name.startswith("ppt/embeddings/")]

    def test_editing_a_chart_is_still_refused(self, adversarial_deck, tmp_path):
        """The scoping must not become a way around the guard."""
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_FONT, slide=self.CHART[0], target=self.CHART[1],
            before="Arial", after="Calibri",
        ))
        with pytest.raises(ApplyError, match="native chart"):
            apply_changes(adversarial_deck, cs, tmp_path / "nope.pptx")


class TestTheCostOfAnApplyDoesNotGrowWithTheChangeCount:
    """The guards used to run per change, and each one scanned the package.

    Every slide, every rels file, every chart part — the same scan, once for
    every change in the set. Profiled on a 272-change tidy of a 26-slide deck:
    **32,778 reads of the archive, and 102 of the 103 seconds it took**. A
    1,199-change tidy on a 41-slide deck took 7 minutes 48 seconds, and the cost
    *per change* was rising, because the scan is the same size however many
    changes there are.

    Hoisted out of the loop: 103s → 1.5s on the first, 468s → 3.9s on the
    second. The whole test suite halved.

    The number is not what this test protects — machines differ. It protects the
    shape: the work is done once, so putting the scan back inside the loop fails
    here rather than being noticed a year later on somebody's 300-slide deck.
    """

    def _tidy_changes(self, deck, count):
        info = inspect(deck)
        shape = next(s for sl in info.slides for s in sl.shapes
                     if s.kind not in ("table", "chart") and s.runs
                     and any(r.text.strip() for r in s.runs))
        slide = next(sl.number for sl in info.slides for s in sl.shapes if s is shape)
        run = next(r for r in shape.runs if r.text.strip())
        cs = ChangeSet(deck=str(deck))
        # The same edit repeated: only the first can apply, and the rest fail
        # honestly. What is being counted is the guard work, which happens
        # before any of them are written.
        for i in range(count):
            cs.add(Change(id=f"c{i}", op=Op.SET_TEXT, slide=slide,
                          target=shape.id, before=run.text, after=run.text + "."))
        cs.approve_all()
        return cs

    def _reads_during(self, deck, changeset, out, monkeypatch):
        from slide_wright.package import Package

        calls = []
        original = Package.read
        monkeypatch.setattr(
            Package, "read",
            lambda self, name: (calls.append(name), original(self, name))[1],
        )
        try:
            apply_changes(deck, changeset, out)
        except Exception:
            pass  # a refusal still did the guard work, which is what is counted
        return len(calls)

    def test_ten_times_the_changes_is_not_ten_times_the_work(
        self, adversarial_deck, tmp_path, monkeypatch
    ):
        few = self._reads_during(
            adversarial_deck, self._tidy_changes(adversarial_deck, 2),
            tmp_path / "few.pptx", monkeypatch)
        many = self._reads_during(
            adversarial_deck, self._tidy_changes(adversarial_deck, 20),
            tmp_path / "many.pptx", monkeypatch)

        assert many < few * 2, (
            f"{few} reads for 2 changes and {many} for 20 — the package is "
            "being rescanned per change again"
        )

    def test_the_refusal_still_happens_and_still_explains_itself(
        self, adversarial_deck, tmp_path
    ):
        """The lookup is cheap; the message is not. It must still be produced
        for the one change that is actually refused."""
        deck = inspect(adversarial_deck)
        chart = next((s.number, x) for s in deck.slides for x in s.shapes
                     if x.kind == "chart")
        cs = approved(adversarial_deck, Change(
            id="c1", op=Op.SET_FONT, slide=chart[0], target=chart[1].id,
            before="Arial", after="Calibri",
        ))
        with pytest.raises(ApplyError, match="native chart"):
            apply_changes(adversarial_deck, cs, tmp_path / "no.pptx")


class TestTwoWritesAtOnceDoNotCollide:
    """The scratch file has to be unique per call, not per process.

    Keying it on the process id was enough for two processes and not for two
    threads, and two threads is the ordinary case — a server handling two
    requests. Three concurrent applies produced `PermissionError: the process
    cannot access the file because it is being used by another process`: one
    success and two raw 500s, with the work in them lost.
    """

    def test_concurrent_writes_to_different_outputs_all_succeed(
        self, adversarial_deck, tmp_path
    ):
        import threading

        from slide_wright.apply import _write_package

        results: list[tuple[int, str]] = []

        def write(i: int) -> None:
            try:
                _write_package(
                    adversarial_deck, tmp_path / f"out-{i}.pptx",
                    {"ppt/slides/slide1.xml": f"<p:sld n='{i}'/>".encode()},
                )
                results.append((i, "ok"))
            except Exception as exc:  # noqa: BLE001
                results.append((i, f"{type(exc).__name__}: {exc}"))

        threads = [threading.Thread(target=write, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)

        assert [r for _, r in results] == ["ok"] * 6, results

    def test_each_output_is_a_whole_package(self, adversarial_deck, tmp_path):
        import threading

        from slide_wright.apply import _write_package

        def write(i: int) -> None:
            _write_package(adversarial_deck, tmp_path / f"out-{i}.pptx", {})

        threads = [threading.Thread(target=write, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)

        expected = Package.open(adversarial_deck).part_count
        for i in range(6):
            assert Package.open(tmp_path / f"out-{i}.pptx").part_count == expected

    def test_nothing_is_left_behind(self, adversarial_deck, tmp_path):
        from slide_wright.apply import _write_package

        _write_package(adversarial_deck, tmp_path / "out.pptx", {})
        assert not list(tmp_path.glob("*.partial"))
        assert not list(tmp_path.glob(".*"))


class TestAnEditKeepsTheFormattingItDidNotAskAbout:
    """The promise, at run granularity.

    "Change what I asked, preserve everything else" is checkable inside a single
    paragraph, and that is where it was failing. Replacing two words at the
    front of a sentence used to write the whole joined string into the first run
    and blank the rest, so the bold on a figure three words later disappeared —
    applied, verified, and invisible to a content diff because the text was
    identical.
    """

    A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

    def _paragraph(self, *runs: tuple[str, str]):
        from lxml import etree

        body = "".join(
            f'<a:r><a:rPr {attrs}/><a:t>{text}</a:t></a:r>' for text, attrs in runs
        )
        return etree.fromstring(
            '<p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
            ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            f"<a:p>{body}</a:p></p:sp>"
        )

    def _runs(self, shape):
        return [
            (r.find(f"{self.A}t").text or "", r.find(f"{self.A}rPr").get("b"))
            for r in shape.findall(f".//{self.A}r")
        ]

    def test_a_bold_figure_survives_an_edit_to_the_words_before_it(self):
        from slide_wright.apply import _set_text

        shape = self._paragraph(
            ("Revenue grew ", 'b="0"'), ("15%", 'b="1"'), (" in FY25", 'b="0"')
        )
        assert _set_text(shape, "Revenue grew", "Revenue increased")
        assert self._runs(shape) == [
            ("Revenue increased ", "0"),
            ("15%", "1"),
            (" in FY25", "0"),
        ], "the bold run was neither emptied nor merged into its neighbour"

    def test_a_run_after_the_span_keeps_its_own_text(self):
        from slide_wright.apply import _set_text

        shape = self._paragraph(("As at ", 'b="0"'), ("Q3", 'b="1"'), (" close", 'b="0"'))
        assert _set_text(shape, "As at Q3", "As at Q4")
        assert self._runs(shape) == [("As at Q4", "0"), ("", "1"), (" close", "0")], (
            "only the runs the span covers may change"
        )

    def test_a_span_ending_mid_run_keeps_the_remainder(self):
        from slide_wright.apply import _set_text

        shape = self._paragraph(("Fees ", 'b="0"'), ("rose sharply", 'b="1"'))
        assert _set_text(shape, "Fees rose", "Fees fell")
        assert self._runs(shape) == [("Fees fell", "0"), (" sharply", "1")], (
            "the tail beyond the span keeps its own formatting"
        )

    def test_an_edit_spanning_two_bullets_leaves_the_third_alone(self, tmp_path):
        """The visible version: text collapsing out of the paragraphs it lived in."""
        from lxml import etree

        from slide_wright.apply import _set_text

        paras = "".join(
            f'<a:p><a:r><a:rPr/><a:t>{t}</a:t></a:r></a:p>'
            for t in ("Margin held at 42%", "Headcount fell to 310", "Cash runway 18 months")
        )
        shape = etree.fromstring(
            '<p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
            f' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">{paras}</p:sp>'
        )
        assert _set_text(shape, "42%Headcount fell", "42%Headcount rose")
        third = shape.findall(f"{self.A}p")[2]
        assert third.find(f".//{self.A}t").text == "Cash runway 18 months", (
            "a bullet the span never reached must still hold its own text"
        )

    def test_it_holds_through_save_and_reload(self, tmp_path):
        """End to end on a real package, because XML in memory is not a deck.

        Built here rather than taken from the corpus: the adversarial deck has
        no shape carrying two differently formatted runs in one paragraph, which
        is exactly the arrangement this defect needed. That absence is why every
        existing narrowness test passed while the promise was broken.
        """
        from pptx import Presentation
        from pptx.util import Inches, Pt

        src = tmp_path / "mixed.pptx"
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        frame = slide.shapes.add_textbox(
            Inches(1), Inches(1), Inches(8), Inches(2)
        ).text_frame
        for text, bold in (("Revenue grew ", False), ("15%", True), (" in FY25", False)):
            run = frame.paragraphs[0].add_run()
            run.text = text
            run.font.bold = bold
            run.font.size = Pt(18)
        prs.save(str(src))

        shape = next(s for s in inspect(src).slides[0].shapes if s.text.strip())
        out = tmp_path / "o.pptx"
        cs = approved(src, Change(
            id="c1", op=Op.SET_TEXT, slide=1, target=shape.id,
            before="Revenue grew", after="Revenue increased",
        ))
        result = apply_changes(src, cs, out)
        assert not result.failed, result.failed

        after = next(s for s in inspect(out).slides[0].shapes if s.text.strip())
        assert [(r.text, r.bold) for r in after.runs] == [
            ("Revenue increased ", False),
            ("15%", True),
            (" in FY25", False),
        ], "the emphasis on the figure must survive an edit that never named it"


class TestACellSplitAcrossRunsIsStillOneValue:
    """Where the figures live, and where a wrong one arrives with a citation.

    A cell's value is one thing to a reader and often several runs to OOXML:
    part of a number gets bolded, or a language boundary falls inside it, and
    "1,234" is stored as "1,2" + "34". Replacing it used to write the new value
    into the first run and leave the rest, so a refresh cited to a spreadsheet
    coordinate produced 1,98734 and reported it applied.
    """

    def _deck_with_a_split_cell(self, tmp_path, pieces=(("1,2", False), ("34", True))):
        from pptx import Presentation
        from pptx.util import Inches, Pt

        path = tmp_path / "split-cell.pptx"
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        table = slide.shapes.add_table(
            2, 2, Inches(1), Inches(1), Inches(6), Inches(1.5)
        ).table
        table.cell(0, 0).text = "Metric"
        table.cell(0, 1).text = "FY25"
        table.cell(1, 0).text = "Revenue"
        paragraph = table.cell(1, 1).text_frame.paragraphs[0]
        paragraph.text = ""
        for text, bold in pieces:
            run = paragraph.add_run()
            run.text = text
            run.font.bold = bold
            run.font.size = Pt(14)
        prs.save(str(path))
        return path

    def _table(self, deck):
        return next(s for s in inspect(deck).slides[0].shapes if s.kind == "table")

    def test_replacing_a_split_figure_leaves_one_figure(self, tmp_path):
        src = self._deck_with_a_split_cell(tmp_path)
        table = self._table(src)
        assert table.cell(1, 1) == "1,234"

        out = tmp_path / "o.pptx"
        cs = approved(src, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=1, target=f"{table.id}/r1/c1",
            before="1,234", after="1,987", citation="q3.xlsx!B2",
        ))
        result = apply_changes(src, cs, out)
        assert not result.failed, result.failed
        assert self._table(out).cell(1, 1) == "1,987", (
            "the old value's tail must not survive alongside the new one"
        )

    def test_a_wrong_before_is_refused_rather_than_written_anywhere(self, tmp_path):
        """The safety half: a stale `before` must not become a blind write."""
        src = self._deck_with_a_split_cell(tmp_path)
        table = self._table(src)
        out = tmp_path / "o.pptx"
        cs = approved(src, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=1, target=f"{table.id}/r1/c1",
            before="9,999", after="1,987",
        ))
        result = apply_changes(src, cs, out)
        assert len(result.failed) == 1
        assert "9,999" in str(result.failed[0][1]), "the refusal must name the value it looked for"
        assert self._table(out).cell(1, 1) == "1,234", "the cell must be untouched"

    def test_a_cell_with_no_before_is_set_outright(self, tmp_path):
        """`before=None` means "this cell now says X" — the whole cell is the target."""
        src = self._deck_with_a_split_cell(tmp_path)
        table = self._table(src)
        out = tmp_path / "o.pptx"
        cs = approved(src, Change(
            id="c1", op=Op.SET_TABLE_CELL, slide=1, target=f"{table.id}/r1/c1",
            before=None, after="n/a",
        ))
        assert not apply_changes(src, cs, out).failed
        assert self._table(out).cell(1, 1) == "n/a"
