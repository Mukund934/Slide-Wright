"""Read a .pptx as what it physically is: an OPC package of parts.

Everything Slide-Wright promises reduces to a claim about parts. "We changed
only slide 12" means every other part came back byte-for-byte. So the lowest
layer of the engine deals in parts and hashes, not in slides and shapes.

Nothing here interprets content. Interpretation is `inspect`; this module only
reports what is in the package and what its bytes hash to.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# Caps applied before extraction. A .pptx is a zip, and a zip from an untrusted
# source is a hostile input: see docs/architecture/05-security.md.
MAX_PARTS = 10_000
MAX_UNCOMPRESSED_BYTES = 2 * 1024**3  # 2 GiB
MAX_COMPRESSION_RATIO = 200  # a legitimate deck never approaches this

SLIDE_PART = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
LAYOUT_PART = re.compile(r"^ppt/slideLayouts/slideLayout\d+\.xml$")
MASTER_PART = re.compile(r"^ppt/slideMasters/slideMaster\d+\.xml$")
CHART_PART = re.compile(r"^ppt/charts/chart\d+\.xml$")
DIAGRAM_PART = re.compile(r"^ppt/diagrams/")
MEDIA_PART = re.compile(r"^ppt/media/")
EMBEDDING_PART = re.compile(r"^ppt/embeddings/")
NOTES_PART = re.compile(r"^ppt/notesSlides/notesSlide\d+\.xml$")


class UnsafePackageError(Exception):
    """The file is not safe to open. Raised before any extraction happens."""


@dataclass(frozen=True)
class Part:
    """One part of the OPC package."""

    name: str
    sha256: str
    size: int
    compressed_size: int

    @property
    def is_slide(self) -> bool:
        return bool(SLIDE_PART.match(self.name))

    @property
    def slide_number(self) -> int | None:
        m = SLIDE_PART.match(self.name)
        return int(m.group(1)) if m else None


@dataclass
class Package:
    """An immutable view of a .pptx package.

    Never holds a write handle. The source file is evidence and is never
    mutated in place: see docs/architecture/02-safe-editing.md.
    """

    path: Path
    parts: dict[str, Part] = field(default_factory=dict)

    @classmethod
    def open(cls, path: str | Path) -> Package:
        path = Path(path)
        _assert_safe(path)
        pkg = cls(path=path)
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                digest = hashlib.sha256(z.read(info.filename)).hexdigest()
                pkg.parts[info.filename] = Part(
                    name=info.filename,
                    sha256=digest,
                    size=info.file_size,
                    compressed_size=info.compress_size,
                )
        return pkg

    # ── selectors ────────────────────────────────────────────────────────────

    def names(self) -> set[str]:
        return set(self.parts)

    def slides(self) -> list[Part]:
        return sorted(
            (p for p in self.parts.values() if p.is_slide),
            key=lambda p: p.slide_number or 0,
        )

    def matching(self, pattern: re.Pattern[str]) -> list[Part]:
        return [p for p in self.parts.values() if pattern.match(p.name)]

    def read(self, name: str) -> bytes:
        with zipfile.ZipFile(self.path) as z:
            return z.read(name)

    def read_text(self, name: str) -> str:
        return self.read(name).decode("utf-8", errors="replace")

    # ── summary ──────────────────────────────────────────────────────────────

    @property
    def slide_count(self) -> int:
        return len(self.slides())

    @property
    def part_count(self) -> int:
        return len(self.parts)

    def __repr__(self) -> str:
        return (
            f"Package({self.path.name!r}, "
            f"parts={self.part_count}, slides={self.slide_count})"
        )


def _assert_safe(path: Path) -> None:
    """Reject hostile archives before extracting anything.

    Every check here runs against the zip *directory*, which is cheap to read.
    Nothing is decompressed until the file has passed.
    """
    if not path.is_file():
        raise UnsafePackageError(f"not a file: {path}")

    if path.suffix.lower() == ".pptm":
        raise UnsafePackageError(
            "macro-enabled presentations (.pptm) are not accepted"
        )

    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
    except zipfile.BadZipFile as exc:
        raise UnsafePackageError(f"not a valid OPC package: {exc}") from exc

    if len(infos) > MAX_PARTS:
        raise UnsafePackageError(f"too many parts: {len(infos)} > {MAX_PARTS}")

    total_uncompressed = 0
    for info in infos:
        name = info.filename
        # Path traversal: a part must stay inside the package.
        if name.startswith("/") or ".." in Path(name).parts:
            raise UnsafePackageError(f"unsafe part path: {name!r}")

        total_uncompressed += info.file_size
        if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise UnsafePackageError("uncompressed size exceeds limit")

        # Zip bomb: a single part expanding far beyond its compressed size.
        if info.compress_size > 0:
            ratio = info.file_size / info.compress_size
            if ratio > MAX_COMPRESSION_RATIO and info.file_size > 1024**2:
                raise UnsafePackageError(
                    f"compression ratio {ratio:.0f}:1 on {name!r} exceeds limit"
                )

    if not any(i.filename == "[Content_Types].xml" for i in infos):
        raise UnsafePackageError("missing [Content_Types].xml; not an OPC package")
