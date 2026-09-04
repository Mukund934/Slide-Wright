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
