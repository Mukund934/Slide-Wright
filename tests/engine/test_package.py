"""Package reader: hashing, selectors, and the hostile-archive guards.

The guards matter more than they look. `ingest` is the only component that
touches untrusted bytes, so it is the product's hostile boundary.
"""

from __future__ import annotations

import shutil
import zipfile

import pytest

from slide_wright.package import (
    MAX_PARTS,
    Package,
    UnsafePackageError,
)


class TestOpen:
    def test_reads_parts_and_slides(self, adversarial_deck):
        pkg = Package.open(adversarial_deck)
        assert pkg.slide_count == 6
        assert pkg.part_count > 40
        assert "[Content_Types].xml" in pkg.parts

    def test_slides_are_ordered_numerically(self, adversarial_deck):
        # slide10 must not sort before slide2, which is what string ordering does.
        numbers = [s.slide_number for s in Package.open(adversarial_deck).slides()]
        assert numbers == sorted(numbers)

    def test_hashes_are_stable_across_opens(self, adversarial_deck):
        a = Package.open(adversarial_deck)
        b = Package.open(adversarial_deck)
        assert {n: p.sha256 for n, p in a.parts.items()} == {
            n: p.sha256 for n, p in b.parts.items()
        }

    def test_does_not_mutate_the_source(self, adversarial_deck):
        before = adversarial_deck.read_bytes()
        pkg = Package.open(adversarial_deck)
        pkg.read(pkg.slides()[0].name)
        assert adversarial_deck.read_bytes() == before


class TestSafetyGuards:
    def test_rejects_missing_file(self, tmp_path):
        with pytest.raises(UnsafePackageError, match="not a file"):
            Package.open(tmp_path / "nope.pptx")

    def test_rejects_non_zip(self, tmp_path):
        bad = tmp_path / "fake.pptx"
        bad.write_text("this is not a zip archive")
        with pytest.raises(UnsafePackageError, match="not a valid OPC package"):
            Package.open(bad)

    def test_rejects_macro_enabled(self, tmp_path, adversarial_deck):
        macro = tmp_path / "deck.pptm"
        macro.write_bytes(adversarial_deck.read_bytes())
        with pytest.raises(UnsafePackageError, match="macro-enabled"):
            Package.open(macro)

    def test_rejects_zip_without_content_types(self, tmp_path):
        z = tmp_path / "bare.pptx"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("ppt/slides/slide1.xml", "<xml/>")
        with pytest.raises(UnsafePackageError, match="Content_Types"):
            Package.open(z)

    def test_rejects_path_traversal(self, tmp_path):
        z = tmp_path / "evil.pptx"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("[Content_Types].xml", "<Types/>")
            f.writestr("../../escaped.xml", "<pwned/>")
        with pytest.raises(UnsafePackageError, match="unsafe part path"):
            Package.open(z)

    def test_rejects_absolute_part_path(self, tmp_path):
        z = tmp_path / "abs.pptx"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("[Content_Types].xml", "<Types/>")
            f.writestr("/etc/passwd", "root")
        with pytest.raises(UnsafePackageError, match="unsafe part path"):
            Package.open(z)

    def test_rejects_zip_bomb(self, tmp_path):
        z = tmp_path / "bomb.pptx"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
            f.writestr("[Content_Types].xml", "<Types/>")
            f.writestr("bomb.bin", b"\0" * (8 * 1024 * 1024))
        with pytest.raises(UnsafePackageError, match="compression ratio"):
            Package.open(z)

    def test_rejects_too_many_parts(self, tmp_path):
        z = tmp_path / "many.pptx"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("[Content_Types].xml", "<Types/>")
            for i in range(MAX_PARTS + 1):
                f.writestr(f"p/{i}.xml", "<x/>")
        with pytest.raises(UnsafePackageError, match="too many parts"):
            Package.open(z)


class TestTheArchiveIsOpenedOnce:
    """Every read used to open the file again.

    Opening a zip means parsing its whole central directory, so on a 340-part
    deck that is 340 entries re-read per part read. Measured: `inspect` on a
    55 MB deck did 358 reads and spent 0.81 of its 0.96 seconds doing nothing
    but re-reading the same directory. `diff` spent 1.61 of 1.91.

    After: 0.16s and 0.31s. The whole test suite went from 51s to 28s.
    """

    def test_reading_many_parts_opens_the_file_once(self, adversarial_deck, monkeypatch):
        opened = []
        real = zipfile.ZipFile

        def counting(*args, **kwargs):
            opened.append(args[0] if args else None)
            return real(*args, **kwargs)

        pkg = Package.open(adversarial_deck)
        monkeypatch.setattr(zipfile, "ZipFile", counting)
        for name in list(pkg.parts)[:12]:
            pkg.read(name)
        assert len(opened) <= 1, f"opened the archive {len(opened)} times for 12 reads"

    def test_closing_releases_the_file(self, adversarial_deck, tmp_path):
        """A handle held past the last use makes the file undeletable on
        Windows, which turns a performance fix into an rmtree failure."""
        copy = tmp_path / "deck.pptx"
        shutil.copy(adversarial_deck, copy)
        pkg = Package.open(copy)
        pkg.read(next(iter(pkg.parts)))
        pkg.close()
        copy.unlink()  # raises PermissionError on Windows if the handle is held
        assert not copy.exists()

    def test_closing_twice_is_fine(self, adversarial_deck):
        pkg = Package.open(adversarial_deck)
        pkg.read(next(iter(pkg.parts)))
        pkg.close()
        pkg.close()

    def test_it_reopens_if_read_again(self, adversarial_deck):
        """Closing is a release, not an invalidation."""
        pkg = Package.open(adversarial_deck)
        name = next(iter(pkg.parts))
        first = pkg.read(name)
        pkg.close()
        assert pkg.read(name) == first

    def test_it_works_as_a_context_manager(self, adversarial_deck, tmp_path):
        copy = tmp_path / "deck.pptx"
        shutil.copy(adversarial_deck, copy)
        with Package.open(copy) as pkg:
            assert pkg.read(next(iter(pkg.parts)))
        copy.unlink()
        assert not copy.exists()
