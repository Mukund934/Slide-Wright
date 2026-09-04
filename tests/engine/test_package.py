"""Package reader: hashing, selectors, and the hostile-archive guards.

The guards matter more than they look. `ingest` is the only component that
touches untrusted bytes, so it is the product's hostile boundary.
"""

from __future__ import annotations

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
