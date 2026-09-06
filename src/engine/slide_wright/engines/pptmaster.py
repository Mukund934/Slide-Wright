"""Adapter for the ppt-master round-trip engine.

ppt-master is vendored/tracked upstream (ADR-0001). This wraps its two CLIs so
the rest of Slide-Wright never shells out directly, and so the safety rails are
applied in exactly one place.

The rails are not optional:

  ADR-0006 — exporting without --native-charts-and-tables silently converts
  native tables to pictures and discards edits, while reporting success. This
  adapter always passes the flag, and there is deliberately no parameter to
  turn it off.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class EngineError(RuntimeError):
    """The engine refused or failed. Fail closed rather than guess."""


@dataclass
class EngineResult:
    ok: bool
    stdout: str
    stderr: str
    output_path: Path | None = None

    @property
    def passthrough_count(self) -> int:
        return _parse_summary(self.stdout, "passthrough")

    @property
    def rebuilt_count(self) -> int:
        return _parse_summary(self.stdout, "rebuilt")

    @property
    def patched_count(self) -> int:
        return _parse_summary(self.stdout, "patched")


def _parse_summary(text: str, key: str) -> int:
    """Read `key=N` out of the engine's round-trip summary line."""
    for token in text.replace("\n", " ").split():
        if token.startswith(f"{key}="):
            try:
                return int(token.split("=", 1)[1])
            except ValueError:
                return -1
    return -1


class PptMasterEngine:
    """Thin, safe wrapper around ppt-master's import/export scripts."""

    def __init__(self, scripts_dir: str | Path | None = None, timeout: int = 900):
        self.scripts_dir = Path(scripts_dir or _default_scripts_dir())
        self.timeout = timeout
        if not (self.scripts_dir / "pptx_to_svg.py").is_file():
            raise EngineError(
                f"ppt-master scripts not found at {self.scripts_dir}. "
                "Set SLIDE_WRIGHT_PPTMASTER to the skill's scripts directory."
            )

    # ── operations ───────────────────────────────────────────────────────────

    def ingest(self, deck: str | Path, workspace: str | Path) -> EngineResult:
        """Import a .pptx into a round-trip workspace (source preserved).

        Paths are resolved to absolute first. The engine runs with its own
        scripts directory as cwd, so a relative path handed straight through
        resolves against *that* directory and the file is silently "not found"
        somewhere the caller never looked.
        """
        return self._run([
            "pptx_to_svg.py",
            str(Path(deck).resolve()),
            "-o", str(Path(workspace).resolve()),
            "--roundtrip",
        ])

    def export(self, workspace: str | Path, out: str | Path) -> EngineResult:
        """Export a workspace back to .pptx.

        Always round-trip mode (so untouched slides pass through with their
        original XML) and always with native charts and tables (ADR-0006).
        """
        out = Path(out).resolve()
        res = self._run(
            [
                "svg_to_pptx.py",
                str(Path(workspace).resolve()),
                "-o",
                str(out),
                "--roundtrip",
                "--native-charts-and-tables",  # ADR-0006: never optional
            ]
        )
        res.output_path = out if out.is_file() else None
        if res.ok and res.output_path is None:
            raise EngineError("engine reported success but produced no output file")
        return res

    # ── internals ────────────────────────────────────────────────────────────

    def _run(self, args: list[str]) -> EngineResult:
        cmd = [sys.executable, str(self.scripts_dir / args[0]), *args[1:]]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(self.scripts_dir),
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise EngineError(f"engine timed out after {self.timeout}s") from exc

        return EngineResult(
            ok=proc.returncode == 0,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )


def _default_scripts_dir() -> Path:
    env = os.environ.get("SLIDE_WRIGHT_PPTMASTER")
    if env:
        return Path(env)
    # Development default: the researched checkout under trunk/.
    return (
        Path(__file__).resolve().parents[5]
        / "7BestRepos"
        / "ppt-master-main"
        / "ppt-master-main"
        / "skills"
        / "ppt-master"
        / "scripts"
    )
