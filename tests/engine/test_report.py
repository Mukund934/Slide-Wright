"""Change report: attribution and the fail-closed delivery gate.

The report is the mechanism that makes the promise checkable. If it can be
made to say "verified" when something unrequested moved, the promise is void.
"""

from __future__ import annotations

import shutil
import zipfile

from slide_wright.fidelity import compare
from slide_wright.package import Package
from slide_wright.report import RequestedChange, build


def _edit_slide(src, dst, slide_name: str, old: bytes, new: bytes):
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == slide_name:
                data = data.replace(old, new)
            zout.writestr(info.filename, data)
    return dst


class TestAttribution:
    def test_requested_change_is_attributed_and_deliverable(self, adversarial_deck, tmp_path):
        src = Package.open(adversarial_deck)
        slide1 = src.slides()[0].name
        out = _edit_slide(adversarial_deck, tmp_path / "o.pptx", slide1,
                          b"Adversarial", b"Reworked")
        rep = build(compare(adversarial_deck, out),
                    [RequestedChange(slide=1, description="retitle", target="title")])
        assert rep.deliverable
        assert rep.unrequested_slide_changes == []
        assert "VERIFIED" in rep.render()

    def test_unrequested_slide_change_blocks_delivery(self, adversarial_deck, tmp_path):
        src = Package.open(adversarial_deck)
        slide1 = src.slides()[0].name
        out = _edit_slide(adversarial_deck, tmp_path / "o.pptx", slide1,
                          b"Adversarial", b"Reworked")
        # The user asked about slide 4; slide 1 moved.
        rep = build(compare(adversarial_deck, out),
                    [RequestedChange(slide=4, description="tighten spacing")])
        assert not rep.deliverable
        assert rep.unrequested_slide_changes == [1]
        rendered = rep.render()
        assert "BLOCKED" in rendered
        assert "unrequested changes on slide(s) 1" in rendered

    def test_no_changes_at_all_is_deliverable(self, adversarial_deck, tmp_path):
        copy = tmp_path / "copy.pptx"
        shutil.copy(adversarial_deck, copy)
        rep = build(compare(adversarial_deck, copy))
        assert rep.deliverable
        assert rep.fidelity.fidelity_score == 100.0


class TestFailClosed:
    """Every integrity failure must block delivery, not merely warn."""

    def test_removed_part_blocks_delivery(self, adversarial_deck, tmp_path):
        out = tmp_path / "short.pptx"
        with zipfile.ZipFile(adversarial_deck) as zin, zipfile.ZipFile(out, "w") as zout:
            for info in zin.infolist():
                if info.filename.startswith("ppt/notesSlides/"):
                    continue
                zout.writestr(info.filename, zin.read(info.filename))
        rep = build(compare(adversarial_deck, out))
        assert not rep.deliverable
        assert any("removed" in r for r in rep.blocking_reasons)

    def test_native_loss_blocks_delivery(self, adversarial_deck, tmp_path):
        out = tmp_path / "nochart.pptx"
        with zipfile.ZipFile(adversarial_deck) as zin, zipfile.ZipFile(out, "w") as zout:
            for info in zin.infolist():
                if info.filename.startswith("ppt/charts/"):
                    continue
                zout.writestr(info.filename, zin.read(info.filename))
        rep = build(compare(adversarial_deck, out))
        assert not rep.deliverable
        assert any("native object loss" in r for r in rep.blocking_reasons)

    def test_rasterisation_blocks_delivery(self, adversarial_deck, tmp_path):
        copy = tmp_path / "c.pptx"
        shutil.copy(adversarial_deck, copy)
        rep = build(compare(adversarial_deck, copy))
        rep.fidelity.output_census.tables = 0
        rep.fidelity.output_census.pictures = rep.fidelity.source_census.pictures + 3
        assert not rep.deliverable
        assert any("rasterised" in r for r in rep.blocking_reasons)

    def test_blocked_report_never_says_verified(self, adversarial_deck, tmp_path):
        out = tmp_path / "nochart.pptx"
        with zipfile.ZipFile(adversarial_deck) as zin, zipfile.ZipFile(out, "w") as zout:
            for info in zin.infolist():
                if info.filename.startswith("ppt/charts/"):
                    continue
                zout.writestr(info.filename, zin.read(info.filename))
        rendered = build(compare(adversarial_deck, out)).render()
        assert "VERIFIED" not in rendered
        assert "BLOCKED" in rendered


class TestRendering:
    def test_report_states_the_preservation_claim(self, adversarial_deck, tmp_path):
        copy = tmp_path / "c.pptx"
        shutil.copy(adversarial_deck, copy)
        rendered = build(compare(adversarial_deck, copy)).render()
        assert "byte-for-byte identical" in rendered
        assert "CHANGE REPORT" in rendered
        assert "Integrity" in rendered

    def test_integrity_lines_flag_loss(self, adversarial_deck, tmp_path):
        copy = tmp_path / "c.pptx"
        shutil.copy(adversarial_deck, copy)
        rep = build(compare(adversarial_deck, copy))
        rep.fidelity.output_census.chart_parts = 0
        assert "LOSS" in rep.render()


class TestUnrequestedChangesAreExplained:
    """A blocked deck must say what moved, not merely that something did.

    The report used to name the part: "unrequested changes on slide 3". The
    reviewer is at the point of deciding whether to ship a deck to an
    investment committee, and a filename is not something anyone can decide on.
    """

    def _report_with_unattributed_edit(self, adversarial_deck, tmp_path):
        """Edit a real value, then attribute the request to a different slide."""
        out = tmp_path / "out.pptx"
        _edit_slide(adversarial_deck, out, "ppt/slides/slide3.xml", b"9.4x", b"11.8x")
        return build(
            compare(adversarial_deck, out),
            [RequestedChange(slide=1, description="tighten the title")],
        )

    def test_the_report_is_blocked(self, adversarial_deck, tmp_path):
        report = self._report_with_unattributed_edit(adversarial_deck, tmp_path)
        assert not report.deliverable

    def test_it_names_the_value_that_changed(self, adversarial_deck, tmp_path):
        report = self._report_with_unattributed_edit(adversarial_deck, tmp_path)
        rendered = report.render()
        assert "Changes nobody asked for" in rendered
        assert "9.4" in rendered and "11.8" in rendered, rendered

    def test_explanations_are_scoped_to_the_unrequested_slides(
        self, adversarial_deck, tmp_path
    ):
        report = self._report_with_unattributed_edit(adversarial_deck, tmp_path)
        assert {d.slide for d in report.explain_unrequested()} == {3}

    def test_nothing_is_computed_when_everything_was_requested(
        self, adversarial_deck, tmp_path
    ):
        out = tmp_path / "out.pptx"
        _edit_slide(adversarial_deck, out, "ppt/slides/slide3.xml", b"9.4x", b"11.8x")
        report = build(
            compare(adversarial_deck, out),
            [RequestedChange(slide=3, description="update the multiple")],
        )
        assert report.deliverable
        assert report.explain_unrequested() == []

    def test_a_missing_file_does_not_turn_a_block_into_a_crash(
        self, adversarial_deck, tmp_path
    ):
        """Explaining is a courtesy; failing to explain must not raise."""
        report = self._report_with_unattributed_edit(adversarial_deck, tmp_path)
        (tmp_path / "out.pptx").unlink()
        assert report.explain_unrequested() == []
        assert not report.deliverable
        assert "Changes nobody asked for" in report.render()


class TestRepetitionIsCollapsed:
    """A hundred identical lines is where a reviewer stops reading.

    A conformance pass on a real deck produces one line per corrected run, all
    saying the same thing. Printing them all is not transparency — the one line
    that mattered ends up buried in the middle of it.
    """

    def _report(self, requested):
        from slide_wright.fidelity import FidelityReport

        return build(FidelityReport(source="a.pptx", output="b.pptx"), requested)

    def test_identical_changes_are_grouped_and_counted(self):
        requested = [
            RequestedChange(slide=n, description="set typeface Arial -> +mn-lt",
                            target=f"{n}/run/0")
            for n in (12, 14, 15, 17)
        ]
        rendered = self._report(requested).render()
        assert "4x on slides 12, 14, 15, 17" in rendered
        assert rendered.count("set typeface Arial -> +mn-lt") == 1

    def test_a_change_that_happens_once_is_printed_in_full(self):
        """That is the one worth looking at."""
        rendered = self._report([
            RequestedChange(slide=3, description="move (1, 2) -> (3, 4)", target="7"),
        ]).render()
        assert "slide 3 · 7 — move (1, 2) -> (3, 4)" in rendered
        assert "1x on" not in rendered

    def test_a_small_group_still_names_its_targets(self):
        """Below the threshold the individual targets are worth seeing."""
        requested = [
            RequestedChange(slide=1, description="set typeface Arial -> +mn-lt",
                            target=f"9/run/{i}")
            for i in range(3)
        ]
        rendered = self._report(requested).render()
        assert "9/run/0" in rendered and "9/run/2" in rendered

    def test_a_large_group_omits_the_individual_targets(self):
        requested = [
            RequestedChange(slide=1, description="set typeface Arial -> +mn-lt",
                            target=f"9/run/{i}")
            for i in range(40)
        ]
        rendered = self._report(requested).render()
        assert "40x on slides 1" in rendered
        assert "9/run/39" not in rendered

    def test_different_changes_stay_separate(self):
        rendered = self._report([
            RequestedChange(slide=1, description="set typeface Arial -> +mn-lt", target="a"),
            RequestedChange(slide=2, description="move (1, 2) -> (3, 4)", target="b"),
        ]).render()
        assert "set typeface" in rendered and "move (1, 2)" in rendered
