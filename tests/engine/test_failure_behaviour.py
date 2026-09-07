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
from slide_wright.report import build


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


class TestPartsThatAppearAreNotIgnored:
    """The gate was blind in one direction: it only ever looked for *loss*.

    `deliverable` checked unrequested slide changes, removed parts, native loss
    and rasterisation — every one of them a question about what went missing. An
    output could come back carrying parts that were never in the source and the
    report said VERIFIED with no blocking reasons, because nothing asked.

    "Preserve everything else" is a claim about what the output *contains*, not
    only about what it kept.
    """

    def _with_extra(self, deck, out, extras):
        with zipfile.ZipFile(deck) as zin, zipfile.ZipFile(out, "w") as zout:
            for info in zin.infolist():
                zout.writestr(info.filename, zin.read(info.filename))
            for name, payload in extras.items():
                zout.writestr(name, payload)
        return build(compare(deck, out))

    def test_an_injected_macro_project_blocks_delivery(self, adversarial_deck, tmp_path):
        """The case that made this necessary.

        Nothing in this engine emits a `vbaProject.bin`. Its presence in an
        output means the package is not the document that went in, and handing
        that to someone who trusts the verdict is the worst failure this product
        has available to it.
        """
        report = self._with_extra(
            adversarial_deck, tmp_path / "macro.pptx", {"ppt/vbaProject.bin": b"\x00" * 64}
        )
        assert not report.deliverable
        assert any("run code" in r for r in report.blocking_reasons)
        # The reason has to name the part. "Something was added" is not a thing
        # a reviewer can act on.
        assert any("vbaProject.bin" in r for r in report.blocking_reasons)

    def test_the_check_is_on_the_part_name_not_its_spelling(self, adversarial_deck, tmp_path):
        """PowerPoint does not care about case here, so neither can this."""
        report = self._with_extra(
            adversarial_deck, tmp_path / "shouty.pptx", {"ppt/VBAProject.BIN": b"\x00" * 64}
        )
        assert not report.deliverable

    def test_a_benign_addition_is_surfaced_without_blocking(self, adversarial_deck, tmp_path):
        """Narrow on purpose.

        A round-trip can legitimately gain a media part. Blocking every addition
        would fire the refusal on the ordinary case until someone learned to
        click through it, and a refusal people click through protects nobody. So
        it is reported for a human to judge, and only executable parts stop the
        deck.
        """
        report = self._with_extra(
            adversarial_deck, tmp_path / "extra.pptx", {"ppt/media/added.png": b"\x89PNG\x00"}
        )
        assert report.deliverable
        assert any("added.png" in entry for entry in report.unrequested_part_changes)
        assert "(added)" in " ".join(report.unrequested_part_changes)

    def test_the_addition_reaches_the_rendered_report(self, adversarial_deck, tmp_path):
        """Surfaced in the data structure is not the same as surfaced to a human."""
        report = self._with_extra(
            adversarial_deck, tmp_path / "rendered.pptx", {"ppt/media/added.png": b"\x89PNG\x00"}
        )
        assert "added.png" in report.render()


class TestOneNamePerPart:
    """A duplicate entry does not weaken verification. It defeats it.

    A zip directory may name the same part twice. Python's `read()` returns the
    last entry; other readers take the first, and OPC does not say which wins
    because OPC does not permit the situation at all. So the engine can hash one
    copy while PowerPoint renders the other, and every number in the report is
    true of the copy it looked at.

    Measured before the check existed: payload first, genuine part last, and
    `compare` returned 100.00% byte-for-byte identical, zero changed parts, zero
    added parts, deliverable. Not a degraded verdict -- a perfect one.
    """

    def _duplicated(self, deck, out, name, payload, first: bool):
        with zipfile.ZipFile(deck) as zin, zipfile.ZipFile(out, "w") as zout:
            if first:
                zout.writestr(name, payload)
            for info in zin.infolist():
                zout.writestr(info.filename, zin.read(info.filename))
            if not first:
                zout.writestr(name, payload)
        return out

    def test_a_smuggled_first_copy_is_refused(self, adversarial_deck, tmp_path):
        """The direction that scored 100%: the genuine part is what gets hashed."""
        out = self._duplicated(
            adversarial_deck, tmp_path / "first.pptx",
            "ppt/slides/slide1.xml", b"<p:sld/>", first=True,
        )
        with pytest.raises(UnsafePackageError, match="duplicate part"):
            Package.open(out)

    def test_a_smuggled_last_copy_is_refused_too(self, adversarial_deck, tmp_path):
        """The other direction was already caught, but by hash and by accident.

        It read as an unrequested slide change, which is the right refusal for
        the wrong reason: it depended on which entry Python happened to return.
        """
        out = self._duplicated(
            adversarial_deck, tmp_path / "last.pptx",
            "ppt/slides/slide1.xml", b"<p:sld/>", first=False,
        )
        with pytest.raises(UnsafePackageError, match="duplicate part"):
            Package.open(out)

    def test_the_refusal_names_the_part(self, adversarial_deck, tmp_path):
        """A reviewer has to be told which part, not merely that there was one."""
        out = self._duplicated(
            adversarial_deck, tmp_path / "named.pptx",
            "ppt/presentation.xml", b"<p:presentation/>", first=True,
        )
        with pytest.raises(UnsafePackageError, match="ppt/presentation.xml"):
            Package.open(out)

    def test_an_ordinary_deck_still_opens(self, adversarial_deck):
        """The check runs on every open, so it has to be exactly this narrow."""
        assert Package.open(adversarial_deck).part_count > 0
