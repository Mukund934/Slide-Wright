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


def history_patch() -> str:
    """Every line ever added or removed, across all branches.

    Shallow clones make this vacuous, which is why CI checks out full history
    for the hygiene job specifically.
    """
    out = subprocess.run(
        ["git", "log", "--all", "-p"],
        cwd=REPO, capture_output=True, text=True, errors="replace",
    )
    return out.stdout


class TestHistoryIsClean:
    """The repository is public, so its history is public too.

    Every check above inspects the current tree. A credential committed and
    then deleted in a later commit passes all of them and is still one
    `git log -p` away from anyone who clones us.
    """

    def test_no_credentials_anywhere_in_history(self):
        patch = history_patch()
        if not patch:
            pytest.skip("no git history available")
        offenders = []
        for pattern, label in SECRET_PATTERNS:
            for match in set(pattern.findall(patch)):
                offenders.append(f"{label}: {match[:8]}…")
        assert not offenders, (
            "credential-shaped strings in git history — history cannot be "
            "cleaned by a later commit, so the key must be treated as "
            "compromised and rotated: " + "; ".join(offenders)
        )

    def test_no_live_ai_studio_key_in_history(self):
        patch = history_patch()
        if not patch:
            pytest.skip("no git history available")
        offenders = [
            m.group(0)[:12] + "…"
            for m in AQ_KEY.finditer(patch)
            if not PLACEHOLDER.search(m.group(0))
        ]
        assert not offenders, "possible live key in history: " + "; ".join(offenders)

    def test_private_docs_never_entered_history(self):
        """Not merely untracked now — never committed at any point."""
        out = subprocess.run(
            ["git", "log", "--all", "--pretty=format:", "--name-only"],
            cwd=REPO, capture_output=True, text=True, check=True,
        )
        leaked = sorted({
            line for line in out.stdout.splitlines()
            if line.startswith("private/")
        })
        assert not leaked, f"private files exist in history: {leaked}"

    def test_the_vendored_engine_never_entered_history(self):
        out = subprocess.run(
            ["git", "log", "--all", "--pretty=format:", "--name-only"],
            cwd=REPO, capture_output=True, text=True, check=True,
        )
        leaked = {
            line for line in out.stdout.splitlines()
            if line.startswith("vendor/ppt-master/")
        }
        assert not leaked, f"{len(leaked)} vendored files are in history"


class TestNoStrategyInPublicDocs:
    """Commercial strategy belongs in private/, not a public repository.

    This is a denylist, not a guarantee. It catches the vocabulary of market
    positioning, which is cheap to check and hard to write by accident; it
    cannot catch positioning expressed in ordinary words. Two such lines sat
    in `docs/guides/testing-strategy.md` for days -- one arguing a metric was
    "worth owning publicly", one about what competitors starting later could
    not buy -- and this test passed the whole time. Treat a green run as
    "no known strategy vocabulary", not as "no strategy".

    Naming tools we benchmark against is deliberately *allowed*. A public,
    reproducible benchmark has to say what it compares against; that is
    engineering, not positioning.
    """

    # Strategy vocabulary that has no reason to appear in engineering prose.
    FORBIDDEN = [
        r"Prezent",
        r"\$\d+(?:\.\d+)?[MB]\b",          # funding rounds and market sizes
        r"beachhead",
        r"go-to-market",
        r"\bTAM\b",
        r"total addressable market",
        r"\bICP\b",
        r"\bmoat\b",
        r"pricing power",
        r"worth owning publicly",
        r"competitors starting later",
    ]

    def test_tracked_docs_carry_no_strategy_vocabulary(self):
        offenders = []
        for rel in tracked_files():
            if not (rel.endswith(".md") and (rel.startswith("docs/") or rel == "README.md")):
                continue
            content = read(rel)
            for pattern in self.FORBIDDEN:
                found = re.search(pattern, content, re.IGNORECASE)
                if found:
                    offenders.append(f"{rel}: {found.group(0)!r}")
        assert not offenders, "commercial strategy in a public doc: " + "; ".join(offenders)


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


class TestSessionWorkspacesAreIgnored:
    """A session workspace holds copies of the user's deck. It must never be committable.

    This existed as a `.gitignore` line and was wrong: the pattern read
    `.slidewright-tmp/` while `Session.open` writes `.slidewright/`. For the
    whole life of the session code those folders -- which by construction
    contain the confidential file the user opened -- were not ignored at all,
    and the mistake was invisible because nobody had run a session inside the
    repository.

    So the two are asserted to agree rather than assumed to.
    """

    def workspace_directory_name(self) -> str:
        """The folder name `Session.open` builds, read from the source."""
        source = read("src/engine/slide_wright/session.py")
        match = re.search(r'f"\.(\w[\w-]*)/\{deck\.stem\}"', source)
        assert match, "session.py no longer builds its workspace path the expected way"
        return f".{match.group(1)}/"

    def test_the_ignore_pattern_matches_what_the_engine_creates(self):
        assert self.workspace_directory_name() in read(".gitignore").splitlines()

    def test_git_actually_ignores_a_workspace_path(self):
        """Reading the file is not the same as git agreeing with it."""
        candidate = f"{self.workspace_directory_name()}deck/v000-original.pptx"
        result = subprocess.run(
            ["git", "check-ignore", "-q", candidate],
            cwd=REPO, capture_output=True, text=True,
        )
        assert result.returncode == 0, f"git would track {candidate}"

    def test_no_workspace_file_is_tracked(self):
        tracked = [f for f in tracked_files() if ".slidewright" in f]
        assert not tracked, f"session workspace files are in the repository: {tracked}"


TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".css", ".json", ".md", ".html", ".yml"}


def test_no_source_file_is_secretly_binary():
    r"""A control byte in a text file makes git treat the whole file as binary.

    No diff, no blame, no review — the file stops being readable by the tools
    this project is reviewed with, and nothing warns you. Two client files
    acquired one the same way: an escape sequence meant for the TypeScript
    source (a null escape, used as a grouping-key separator) was interpreted by the
    script that wrote the file, so the byte itself landed there instead of the
    six characters. It works perfectly at runtime, which is why it survived
    review and a full test run.
    """
    forbidden = bytes([0])
    offenders = []
    for path in (REPO / "src").rglob("*"):
        if not path.is_file() or "node_modules" in path.parts:
            continue
        if path.suffix not in TEXT_SUFFIXES:
            continue
        raw = path.read_bytes()
        if forbidden in raw:
            offenders.append(
                f"{path.relative_to(REPO)} at byte {raw.index(forbidden)}"
            )
    assert not offenders, (
        "these text files hold a null byte, so git treats them as binary: "
        + "; ".join(offenders)
    )


class TestVersionsAgree:
    """The version a package declares and the version it reports.

    Each distribution states its version twice -- in `pyproject.toml`, which is
    what the wheel is named after, and in `__init__.py`, which is what
    `/api/health` reports and what a bug report will quote. Nothing made them
    the same number.

    A release where those disagree is not a cosmetic problem: the user installs
    0.2.0 and the app tells them it is 0.1.0, so every report against it names
    the wrong code. And the two distributions are versioned together on purpose
    -- the API imports the engine directly (ADR-0010), and a mismatched pair has
    never been tested.
    """

    PACKAGES = {
        "engine": (
            REPO / "src" / "engine" / "pyproject.toml",
            REPO / "src" / "engine" / "slide_wright" / "__init__.py",
        ),
        "api": (
            REPO / "src" / "product" / "api" / "pyproject.toml",
            REPO / "src" / "product" / "api" / "slide_wright_api" / "__init__.py",
        ),
    }

    @staticmethod
    def _declared(pyproject: Path) -> str:
        match = re.search(
            r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M
        )
        assert match, f"no version in {pyproject}"
        return match.group(1)

    @staticmethod
    def _reported(init: Path) -> str:
        match = re.search(
            r'^__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"), re.M
        )
        assert match, f"no __version__ in {init}"
        return match.group(1)

    @pytest.mark.parametrize("name", sorted(PACKAGES))
    def test_the_package_reports_the_version_it_declares(self, name: str) -> None:
        pyproject, init = self.PACKAGES[name]
        declared, reported = self._declared(pyproject), self._reported(init)
        assert declared == reported, (
            f"{name}: pyproject.toml says {declared} and __init__.py says "
            f"{reported}. A user installing {declared} would be told they are "
            f"running {reported}."
        )

    def test_both_distributions_are_released_together(self) -> None:
        engine = self._declared(self.PACKAGES["engine"][0])
        api = self._declared(self.PACKAGES["api"][0])
        assert engine == api, (
            f"engine is {engine} and the API is {api}. They ship as a pair "
            "because the API imports the engine directly (ADR-0010); a "
            "mismatched pair has never been tested."
        )
