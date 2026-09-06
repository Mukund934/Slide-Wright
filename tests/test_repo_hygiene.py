"""Repository hygiene, enforced by the test suite.

These rules were previously checked by remembering to check them. That works
until it doesn't: a live API key shares a nine-character format prefix with a
placeholder I wrote into a test, and every secret scan afterwards fired on our
own file. A scanner that cries wolf gets ignored, which is how the real one
gets through.

So the rules run on every `pytest` now.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Shapes of real credentials. Deliberately not anchored to any one vendor.
SECRET_PATTERNS = [
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "Google API key"),
    (re.compile(r"sk-ant-[0-9A-Za-z_\-]{20,}"), "Anthropic API key"),
    (re.compile(r"sk-[0-9A-Za-z]{32,}"), "OpenAI-style API key"),
    (re.compile(r"gh[pousr]_[0-9A-Za-z]{30,}"), "GitHub token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}"), "Slack token"),
]

# A real Google AI Studio key is `AQ.` + ~50 chars of high-entropy base64.
# Placeholders are allowed; anything that looks like the real thing is not.
AQ_KEY = re.compile(r"AQ\.[A-Za-z0-9_\-]{40,}")
PLACEHOLDER = re.compile(r"NOTAREAL|FAKE|EXAMPLE|PLACEHOLDER|XXXX|0000", re.I)


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return out.stdout.split()


def read(rel: str) -> str:
    try:
        return (REPO / rel).read_text(encoding="utf-8", errors="ignore")
    except (OSError, IsADirectoryError):
        return ""


class TestNoSecretsTracked:
    def test_no_credential_shaped_strings_in_tracked_files(self):
        offenders = []
        for rel in tracked_files():
            if rel == "tests/test_repo_hygiene.py":
                continue  # this file necessarily contains the patterns
            content = read(rel)
            for pattern, label in SECRET_PATTERNS:
                if pattern.search(content):
                    offenders.append(f"{rel}: {label}")
        assert not offenders, "credential-shaped strings in tracked files: " + "; ".join(offenders)

    def test_no_high_entropy_google_ai_keys(self):
        offenders = []
        for rel in tracked_files():
            if rel == "tests/test_repo_hygiene.py":
                continue
            for match in AQ_KEY.finditer(read(rel)):
                if not PLACEHOLDER.search(match.group(0)):
                    offenders.append(f"{rel}: {match.group(0)[:12]}…")
        assert not offenders, "possible live AI Studio key: " + "; ".join(offenders)

    def test_env_file_is_not_tracked(self):
        assert ".env" not in tracked_files()

    def test_env_example_holds_no_values(self):
        for line in read(".env.example").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, _, value = line.partition("=")
            assert value.strip() == "", f"{name} has a value in .env.example"


class TestPrivateDocsNeverTracked:
    """The private directory is the project's strategy. It must never ship."""

    def test_private_directory_is_untracked(self):
        leaked = [f for f in tracked_files() if f.startswith("private/")]
        assert not leaked, f"private files are tracked: {leaked}"

    def test_private_is_ignored_by_git(self):
        result = subprocess.run(
            ["git", "check-ignore", "-q", "private/README.md"], cwd=REPO
        )
        assert result.returncode == 0, "private/ is not covered by .gitignore"

    def test_vendored_engine_is_untracked(self):
        leaked = [f for f in tracked_files() if f.startswith("vendor/ppt-master/")]
        assert not leaked, "the vendored upstream tree should not be committed"


class TestNoStrategyInPublicDocs:
    """Competitive positioning belongs in private/, not a public repository."""

    FORBIDDEN = ["Prezent", "$74M", "$400M", "beachhead"]

    def test_tracked_docs_carry_no_competitor_positioning(self):
        offenders = []
        for rel in tracked_files():
            if not (rel.endswith(".md") and (rel.startswith("docs/") or rel == "README.md")):
                continue
            content = read(rel)
            for term in self.FORBIDDEN:
                if term in content:
                    offenders.append(f"{rel}: {term!r}")
        assert not offenders, "competitive strategy in a public doc: " + "; ".join(offenders)


class TestCommitHygiene:
    """Authorship rules, checked rather than remembered."""

    def _log(self, fmt: str) -> str:
        return subprocess.run(
            ["git", "log", f"--format={fmt}"],
            cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout

    def test_every_commit_is_authored_by_mukund(self):
        authors = {a for a in self._log("%an").splitlines() if a}
        assert authors == {"Mukund Thakur"}, f"unexpected commit authors: {authors}"

    def test_no_ai_attribution_trailers(self):
        body = self._log("%B").lower()
        for marker in ("co-authored-by", "generated with", "anthropic", "claude"):
            assert marker not in body, f"AI attribution found in history: {marker!r}"


class TestSafetyRailsPresent:
    """ADR-0006: the unsafe export path must never quietly return."""

    def test_native_charts_and_tables_flag_is_forced(self):
        source = read("src/engine/slide_wright/engines/pptmaster.py")
        assert "--native-charts-and-tables" in source

    def test_smartart_edits_are_guarded(self):
        source = read("src/engine/slide_wright/apply.py")
        assert "guard_edit" in source
        assert "assert_preserved" in source
