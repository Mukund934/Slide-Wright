"""Case D — failure behaviour.

The product's third principle is "no silent corruption": if something cannot be
safely preserved, fail closed. A tool that reports success on a damaged deck is
worse than one that refuses, because the damage reaches a board meeting.

These tests assert that broken input is *refused*, not quietly processed.
"""

from __future__ import annotations

import re
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


class TestWhatPartHashesCannotSee:
    """Two ways a deck changes while every part it contains stays identical.

    The fidelity score answers one question well — how many parts came back
    byte-for-byte — and it is easy to mistake that for the whole answer. It is
    not. A deck is its parts *and the structure that arranges them*, and the
    structure lives in files the score treats as ordinary members of the set.
    """

    def _edit(self, deck, out, name, transform):
        with zipfile.ZipFile(deck) as zin, zipfile.ZipFile(out, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                zout.writestr(info.filename,
                              transform(data) if info.filename == name else data)
        return build(compare(deck, out))

    # ── running order ────────────────────────────────────────────────────────

    def _swap_first_two(self, data: bytes) -> bytes:
        ids = re.findall(rb"<p:sldId [^>]*/>", data)
        assert len(ids) >= 2, "fixture needs at least two slides"
        return data.replace(ids[0] + ids[1], ids[1] + ids[0], 1)

    def test_reordering_the_deck_blocks_delivery(self, adversarial_deck, tmp_path):
        """Measured before the check: 99.56% identical, deliverable, no reasons.

        The running order is in `presentation.xml`, so a swap leaves every slide
        byte-for-byte intact. The only trace was `ppt/presentation.xml` under
        "other parts changed (review)" — where it sat next to a chart workbook
        that moved because the user asked it to.
        """
        report = self._edit(adversarial_deck, tmp_path / "swapped.pptx",
                            "ppt/presentation.xml", self._swap_first_two)
        assert report.fidelity.slide_order_changed
        assert not report.deliverable
        assert any("different order" in r for r in report.blocking_reasons)

    def test_the_score_is_still_honest_about_itself(self, adversarial_deck, tmp_path):
        """The block is not a claim that the parts changed. They did not.

        Worth asserting: the fix must not be to make the score lie in the other
        direction. One part changed, and the score says one part changed.
        """
        report = self._edit(adversarial_deck, tmp_path / "swapped2.pptx",
                            "ppt/presentation.xml", self._swap_first_two)
        f = report.fidelity
        assert [d.name for d in f.changed] == ["ppt/presentation.xml"]
        assert f.fidelity_score == pytest.approx(
            100.0 * (f.total_source_parts - 1) / f.total_source_parts
        )

    def test_an_untouched_copy_reports_no_reordering(self, adversarial_deck, tmp_path):
        out = tmp_path / "copy.pptx"
        shutil.copy(adversarial_deck, out)
        report = build(compare(adversarial_deck, out))
        assert not report.fidelity.slide_order_changed
        assert report.deliverable

    def test_an_unreadable_order_is_not_a_matching_order(self, adversarial_deck, tmp_path):
        """`None` and `[]` must not compare equal.

        Returning an empty list for a presentation part that cannot be parsed
        would make two broken decks agree with each other, which is the exact
        fail-open this check exists to remove.
        """
        report = self._edit(adversarial_deck, tmp_path / "broken.pptx",
                            "ppt/presentation.xml", lambda _: b"<not-xml")
        assert report.fidelity.output_order is None
        assert report.fidelity.source_order is not None
        assert report.fidelity.slide_order_changed

    # ── a slide's relationships ──────────────────────────────────────────────

    def test_emptying_a_slides_rels_is_a_change_to_that_slide(self, adversarial_deck, tmp_path):
        """Where a slide's pictures, charts and links actually live.

        Empty the file and the slide renders as a page of broken frames while
        the slide part stays byte-identical. It used to be listed as an ordinary
        non-slide part and did not block.
        """
        empty = (b"<?xml version='1.0'?><Relationships xmlns='http://schemas."
                 b"openxmlformats.org/package/2006/relationships'/>")
        report = self._edit(adversarial_deck, tmp_path / "norels.pptx",
                            "ppt/slides/_rels/slide2.xml.rels", lambda _: empty)
        assert 2 in report.fidelity.changed_slide_numbers
        assert report.unrequested_slide_changes == [2]
        assert not report.deliverable

    def test_it_is_blamed_on_the_slide_not_listed_as_a_stray_part(self, adversarial_deck, tmp_path):
        """Attribution, not merely detection.

        "ppt/slides/_rels/slide2.xml.rels changed" is not something a reviewer
        can act on. "unrequested changes on slide 2" is.
        """
        empty = (b"<?xml version='1.0'?><Relationships xmlns='http://schemas."
                 b"openxmlformats.org/package/2006/relationships'/>")
        report = self._edit(adversarial_deck, tmp_path / "blame.pptx",
                            "ppt/slides/_rels/slide2.xml.rels", lambda _: empty)
        assert not report.unrequested_part_changes

    def test_a_slide_still_counts_once(self, adversarial_deck, tmp_path):
        """Right for blame, wrong for arithmetic if it leaked into the count.

        A slide's rels are not a second slide. The rendered report says how many
        slides were left untouched, and attributing rels to a slide must not
        double the denominator.
        """
        out = tmp_path / "count.pptx"
        shutil.copy(adversarial_deck, out)
        report = build(compare(adversarial_deck, out))
        slides = len(Package.open(adversarial_deck).slides())
        assert f"{slides} slide(s) untouched" in report.render()
