"""Fidelity comparison: the arithmetic the product promise reduces to.

These tests build packages by hand rather than running the engine, so they are
fast and deterministic. The engine-driven equivalents live in
test_benchmark_gates.py behind the `engine` marker.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from slide_wright.fidelity import NativeObjectCensus, compare
from slide_wright.package import Package


def _rewrite(src: Path, dst: Path, edits: dict[str, bytes | None]) -> Path:
    """Copy a package, applying per-part replacements. None deletes the part."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            if info.filename in edits:
                new = edits[info.filename]
                if new is None:
                    continue
                zout.writestr(info.filename, new)
            else:
                zout.writestr(info, zin.read(info.filename))
    return dst


class TestIdentical:
    def test_a_copy_scores_one_hundred(self, adversarial_deck, tmp_path):
        copy = tmp_path / "copy.pptx"
        shutil.copy(adversarial_deck, copy)
        rep = compare(adversarial_deck, copy)
        assert rep.fidelity_score == 100.0
        assert not rep.changed and not rep.removed and not rep.added
        assert rep.structurally_intact

    def test_recompression_alone_still_scores_one_hundred(self, adversarial_deck, tmp_path):
        # Part *content* is what matters, not the zip container's framing.
        out = _rewrite(adversarial_deck, tmp_path / "recompressed.pptx", {})
        assert compare(adversarial_deck, out).fidelity_score == 100.0


class TestChangeDetection:
    def test_single_changed_part_is_isolated(self, adversarial_deck, tmp_path):
        src = Package.open(adversarial_deck)
        target = src.slides()[0].name
        out = _rewrite(
            adversarial_deck, tmp_path / "edited.pptx",
            {target: src.read(target).replace(b"Adversarial", b"Rewritten")},
        )
        rep = compare(adversarial_deck, out)
        assert [d.name for d in rep.changed] == [target]
        assert rep.changed_slide_numbers == [1]
        assert not rep.removed
        assert rep.fidelity_score < 100.0

    def test_removed_part_is_flagged_and_breaks_integrity(self, adversarial_deck, tmp_path):
        src = Package.open(adversarial_deck)
        victim = src.slides()[-1].name
        out = _rewrite(adversarial_deck, tmp_path / "truncated.pptx", {victim: None})
        rep = compare(adversarial_deck, out)
        assert [d.name for d in rep.removed] == [victim]
        assert not rep.structurally_intact

    def test_added_part_is_flagged(self, adversarial_deck, tmp_path):
        out = tmp_path / "extra.pptx"
        shutil.copy(adversarial_deck, out)
        with zipfile.ZipFile(out, "a") as z:
            z.writestr("ppt/media/injected.png", b"\x89PNG\r\n\x1a\n")
        rep = compare(adversarial_deck, out)
        assert any(d.name.endswith("injected.png") for d in rep.added)


class TestNativeObjectCensus:
    def test_counts_hard_constructs_in_the_corpus_deck(self, adversarial_deck):
        c = NativeObjectCensus.of(Package.open(adversarial_deck))
        assert c.tables >= 1, "corpus deck must contain a native table"
        assert c.chart_parts >= 1, "corpus deck must contain a native chart part"
        assert c.embeddings >= 1, "native chart implies an embedded workbook"
        assert c.text_runs > 0

    def test_control_deck_has_no_hard_constructs(self, minimal_deck):
        c = NativeObjectCensus.of(Package.open(minimal_deck))
        assert c.tables == 0 and c.chart_parts == 0 and c.embeddings == 0

    def test_losses_are_reported_directionally(self):
        before = NativeObjectCensus(tables=6, chart_parts=4, media_parts=18)
        after = NativeObjectCensus(tables=5, chart_parts=4, media_parts=18)
        assert after.losses_against(before) == ["tables: 6 -> 5"]

    def test_gaining_objects_is_not_a_loss(self):
        before = NativeObjectCensus(tables=6, chart_parts=4, media_parts=18)
        gained = NativeObjectCensus(tables=7, chart_parts=4, media_parts=18)
        assert gained.losses_against(before) == []

    def test_every_native_field_is_checked_not_just_tables(self):
        before = NativeObjectCensus(tables=6, chart_parts=4, media_parts=18)
        stripped = NativeObjectCensus(tables=6)  # chart parts and media dropped
        assert stripped.losses_against(before) == [
            "chart_parts: 4 -> 0",
            "media_parts: 18 -> 0",
        ]


class TestRasterisationDetection:
    """ADR-0006: native table -> picture, edits silently discarded.

    This is the failure mode that would put a picture of last quarter's numbers
    in front of a board. It must be detectable from counts alone.
    """

    def test_detects_the_adr_0006_signature(self):
        before = NativeObjectCensus(tables=1, pictures=0)
        after = NativeObjectCensus(tables=0, pictures=1)
        assert after.rasterisation_suspected(before)

    def test_gaining_a_picture_alone_is_not_rasterisation(self):
        before = NativeObjectCensus(tables=1, pictures=0)
        after = NativeObjectCensus(tables=1, pictures=1)
        assert not after.rasterisation_suspected(before)

    def test_losing_a_table_without_a_new_picture_is_still_a_loss(self):
        before = NativeObjectCensus(tables=1, pictures=0)
        after = NativeObjectCensus(tables=0, pictures=0)
        assert not after.rasterisation_suspected(before)
        assert after.losses_against(before) == ["tables: 1 -> 0"]


class TestReport:
    def test_summary_mentions_loss_when_present(self, adversarial_deck, tmp_path):
        copy = tmp_path / "c.pptx"
        shutil.copy(adversarial_deck, copy)
        rep = compare(adversarial_deck, copy)
        rep.output_census = NativeObjectCensus(tables=0, pictures=5)
        rep.source_census = NativeObjectCensus(tables=1, pictures=0)
        text = rep.summary()
        assert "NATIVE OBJECT LOSS" in text
        assert "RASTERISATION SUSPECTED" in text

    def test_empty_comparison_does_not_divide_by_zero(self, tmp_path):
        empty = tmp_path / "empty.pptx"
        with zipfile.ZipFile(empty, "w") as z:
            z.writestr("[Content_Types].xml", "<Types/>")
        rep = compare(empty, empty)
        assert rep.fidelity_score in (0.0, 100.0)
