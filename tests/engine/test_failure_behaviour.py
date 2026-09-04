"""Case D — failure behaviour.

The product's third principle is "no silent corruption": if something cannot be
safely preserved, fail closed. A tool that reports success on a damaged deck is
worse than one that refuses, because the damage reaches a board meeting.

These tests assert that broken input is *refused*, not quietly processed.
"""

from __future__ import annotations

import shutil
import zipfile

import pytest

from slide_wright.package import Package, UnsafePackageError
from slide_wright.fidelity import compare


class TestMalformedInputIsRefused:
    def test_truncated_file_is_refused(self, adversarial_deck, tmp_path):
        broken = tmp_path / "truncated.pptx"
        data = adversarial_deck.read_bytes()
        broken.write_bytes(data[: len(data) // 2])
        with pytest.raises(UnsafePackageError):
            Package.open(broken)

    def test_empty_file_is_refused(self, tmp_path):
        empty = tmp_path / "empty.pptx"
        empty.write_bytes(b"")
        with pytest.raises(UnsafePackageError):
            Package.open(empty)

    def test_directory_is_refused(self, tmp_path):
        with pytest.raises(UnsafePackageError, match="not a file"):
            Package.open(tmp_path)

    def test_renamed_non_deck_is_refused(self, tmp_path):
        # A .docx renamed to .pptx is a valid zip but not a presentation.
        fake = tmp_path / "actually_text.pptx"
        with zipfile.ZipFile(fake, "w") as z:
            z.writestr("word/document.xml", "<w:document/>")
        with pytest.raises(UnsafePackageError, match="Content_Types"):
            Package.open(fake)


class TestCorruptionIsVisibleNotSilent:
    """If a part is damaged, comparison must surface it rather than average it away."""

    def test_damaged_slide_is_reported_as_changed(self, adversarial_deck, tmp_path):
        out = tmp_path / "damaged.pptx"
        src = Package.open(adversarial_deck)
        target = src.slides()[0].name
        with zipfile.ZipFile(adversarial_deck) as zin, zipfile.ZipFile(out, "w") as zout:
            for info in zin.infolist():
                payload = b"<corrupt/>" if info.filename == target else zin.read(info.filename)
                zout.writestr(info.filename, payload)
        rep = compare(adversarial_deck, out)
        assert target in [d.name for d in rep.changed]
        assert rep.fidelity_score < 100.0

    def test_stripped_chart_part_is_reported_as_loss(self, adversarial_deck, tmp_path):
        """The ADR-0006 shape of failure, detected structurally."""
        out = tmp_path / "no_charts.pptx"
        with zipfile.ZipFile(adversarial_deck) as zin, zipfile.ZipFile(out, "w") as zout:
            for info in zin.infolist():
                if info.filename.startswith("ppt/charts/"):
                    continue
                zout.writestr(info.filename, zin.read(info.filename))
        rep = compare(adversarial_deck, out)
        assert rep.native_losses, "dropping chart parts must be reported as a loss"
        assert any("chart_parts" in loss for loss in rep.native_losses)
        assert not rep.structurally_intact

    def test_wholesale_rasterisation_is_caught(self, adversarial_deck, tmp_path):
        """Replacing native content with images must never read as success."""
        out = tmp_path / "flattened.pptx"
        shutil.copy(adversarial_deck, out)
        rep = compare(adversarial_deck, out)
        # Simulate the engine having flattened everything to pictures.
        rep.output_census.tables = 0
        rep.output_census.chart_parts = 0
        rep.output_census.pictures = rep.source_census.pictures + 6
        assert rep.rasterisation_suspected
        assert not rep.structurally_intact
