"""The third-party corpus is evidence, so it has to be the corpus we cited.

Almost every measured claim in this repository is a statement about these 26
files: 14 of 14 decks changing exactly one part, the compatibility matrix's
"17 decks measured", `$0.008` for the most expensive prompt, 250 audit findings
becoming 149. Swap one fixture, or truncate one during a download, and all of
those move without anything failing — the numbers would simply be about a
different corpus than the one the manifest names.

The manifest already records a SHA-256, a URL and a licence for every file, and
`scripts/fetch_fixtures.py --verify` already checks them. Nothing ran it. A
verifier nobody runs is a verifier that is not there, and the failure it exists
to catch is silent by construction.

Marked `fixtures` because the corpus is not committed: the binaries belong to
their upstream projects and are fetched, not vendored.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "tests" / "fixtures" / "manifest.json"
FIXTURES = REPO / "tests" / "fixtures" / "third-party"


def entries() -> list[dict]:
    if not MANIFEST.is_file():
        pytest.skip("no fixture manifest; run scripts/fetch_fixtures.py")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["fixtures"]


class TestTheManifestDescribesWhatIsOnDisk:
    @pytest.mark.fixtures
    def test_every_fixture_matches_its_recorded_hash(self):
        altered = []
        for entry in entries():
            path = FIXTURES / entry["name"]
            if not path.is_file():
                continue  # absence is the next test's business
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != entry["sha256"]:
                altered.append(f"{entry['name']}: {digest[:12]}… ≠ {entry['sha256'][:12]}…")
        assert not altered, (
            "these fixtures are not the files the manifest records, so every "
            "measurement taken against them describes a different corpus: "
            + "; ".join(altered)
        )

    @pytest.mark.fixtures
    def test_the_corpus_is_complete(self):
        missing = [e["name"] for e in entries() if not (FIXTURES / e["name"]).is_file()]
        assert not missing, (
            f"{len(missing)} fixture(s) are missing; measurements taken now "
            "would have a smaller denominator than the one published: "
            + ", ".join(missing)
        )

    @pytest.mark.fixtures
    def test_no_fixture_on_disk_is_unrecorded(self):
        """A file nobody can trace is not evidence, whatever it contains."""
        if not FIXTURES.is_dir():
            pytest.skip("no fixtures fetched")
        recorded = {e["name"] for e in entries()}
        strays = sorted(p.name for p in FIXTURES.glob("*.pptx") if p.name not in recorded)
        assert not strays, (
            "these decks are in the corpus directory with no provenance, "
            "licence or hash recorded: " + ", ".join(strays)
        )


class TestTheManifestItselfIsUsable:
    """These need no corpus — they check the record, not the files."""

    def test_every_entry_names_its_source_and_licence(self):
        """A fixture with no licence cannot be redistributed or relied on, and
        one with no URL cannot be re-fetched by anyone else."""
        for entry in entries():
            for field in ("name", "url", "licence", "project", "sha256", "bytes"):
                assert entry.get(field), f"{entry.get('name', '?')} has no {field}"

    def test_every_entry_says_why_it_is_here(self):
        """The corpus is chosen, not collected. A fixture nobody can say the
        purpose of is one nobody will notice has stopped serving it."""
        for entry in entries():
            assert entry.get("why"), f"{entry['name']} does not say what it is for"

    def test_hashes_are_full_length_sha256(self):
        for entry in entries():
            digest = entry["sha256"]
            assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), (
                f"{entry['name']} has a malformed hash: {digest!r}"
            )

    def test_names_are_unique(self):
        names = [e["name"] for e in entries()]
        assert len(names) == len(set(names))
