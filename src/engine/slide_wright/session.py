"""A transactional editing session.

Ties the pieces into the product loop:

    open -> inspect -> propose -> review -> apply -> verify -> deliver | rollback

Two invariants hold throughout:

  · the source file is never modified — every step produces a new artifact
    verified against the original;
  · nothing is delivered unless verification passes. A failed verification is
    not a warning to be clicked past; the output is withheld.

Rollback is trivial by construction: the original always exists, so reverting
is choosing an earlier version rather than undoing an edit.

That only holds if versions outlive the process. A session therefore restores
its history from the workspace when it reopens, and version numbers are never
reused -- otherwise a second `edit` on the same deck would overwrite the first
one's artifact and silently rebase onto the original.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from slide_wright.apply import ApplyError, ApplyResult, apply_changes
from slide_wright.changeset import Change, ChangeSet, Status
from slide_wright.fidelity import FidelityReport, compare
from slide_wright.gate import GateResult, check
from slide_wright.inspect import DeckInfo, inspect
from slide_wright.package import Package
from slide_wright.report import ChangeReport, RequestedChange, build


class SessionError(Exception):
    """The session refused to proceed. Always preferable to a silent bad deck."""


@dataclass
class Version:
    """One committed state of the deck."""

    number: int
    path: Path
    created_at: str
    note: str = ""
    changes: list[str] = field(default_factory=list)

    @property
    def is_original(self) -> bool:
        return self.number == 0


@dataclass
class Session:
    """An editing session over one deck."""

    source: Path
    workspace: Path
    versions: list[Version] = field(default_factory=list)
    changeset: ChangeSet | None = None
    last_report: ChangeReport | None = None
    next_number: int = 1

    # ── lifecycle ────────────────────────────────────────────────────────────

    @classmethod
    def open(cls, deck: str | Path, workspace: str | Path | None = None) -> Session:
        """Open a deck and snapshot it immutably as version 0."""
        deck = Path(deck).resolve()
        Package.open(deck)  # validates; raises UnsafePackageError on hostile input

        ws = Path(workspace) if workspace else deck.parent / f".slidewright/{deck.stem}"
        ws.mkdir(parents=True, exist_ok=True)

        snapshot = ws / "v000-original.pptx"
        if not snapshot.exists():
            shutil.copy(deck, snapshot)

        session = cls(source=deck, workspace=ws)
        session._restore()
        if not session.versions:
            session.versions.append(
                Version(number=0, path=snapshot, created_at=_now(), note="original")
            )
            session._commit_history()
        return session

    # ── persistence ──────────────────────────────────────────────────────────

    @property
    def _history_file(self) -> Path:
        return self.workspace / "history.json"

    def _restore(self) -> None:
        """Reload versions written by an earlier session.

        Without this the session sees only version 0 on every open, so a second
        edit overwrites the first edit's file and starts again from the
        original. Both losses are silent, which is the worst kind.
        """
        if not self._history_file.is_file():
            return
        try:
            saved = json.loads(self._history_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as exc:
            raise SessionError(
                f"workspace history is unreadable ({exc}); "
                f"delete {self._history_file} to start a new session"
            ) from exc

        versions = []
        for entry in saved.get("versions", []):
            path = self.workspace / entry["file"]
            if not path.is_file():
                # A version named in history but absent on disk means the
                # workspace was edited underneath us. Refuse rather than
                # present a history that cannot be rolled back to.
                raise SessionError(
                    f"version {entry['number']} is missing from the workspace "
                    f"({path.name}); the history no longer describes what is there"
                )
            versions.append(Version(
                number=entry["number"], path=path, created_at=entry["created_at"],
                note=entry.get("note", ""), changes=entry.get("changes", []),
            ))
        if not versions:
            return
        self.versions = versions
        self.next_number = max(
            saved.get("next_number", 0), max(v.number for v in versions) + 1
        )

    def _commit_history(self) -> None:
        self._history_file.write_text(
            json.dumps(
                {
                    "source": self.source.name,
                    "next_number": self.next_number,
                    "versions": [
                        {
                            "number": v.number, "file": v.path.name,
                            "created_at": v.created_at, "note": v.note,
                            "changes": v.changes,
                        }
                        for v in self.versions
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # ── inspection ───────────────────────────────────────────────────────────

    @property
    def current(self) -> Version:
        return self.versions[-1]

    def deck(self) -> DeckInfo:
        """Structural model of the current version."""
        return inspect(self.current.path)

    def audit(self) -> GateResult:
        """Deterministic quality findings for the current version."""
        return check(self.deck())

    # ── propose / review ─────────────────────────────────────────────────────

    def propose(self, instruction: str = "") -> ChangeSet:
        """Start a change set. Changes are added by a planner, then reviewed."""
        self.changeset = ChangeSet(deck=str(self.current.path), instruction=instruction)
        return self.changeset

    def require_changeset(self) -> ChangeSet:
        if self.changeset is None:
            raise SessionError("no change set; call propose() first")
        return self.changeset

    # ── apply / verify ───────────────────────────────────────────────────────

    def apply(self, note: str = "") -> ChangeReport:
        """Apply approved changes, verify the result, and commit a version.

        The new version is committed only if verification passes. A blocked
        result leaves the session on its previous version and the output on
        disk for inspection.
        """
        cs = self.require_changeset()
        if not cs.approved:
            raise SessionError("no approved changes; approve some first")

        # Never derived from len(versions): after a rollback that would reuse
        # a number whose file is still on disk, overwriting real history.
        number = self.next_number
        target = self.workspace / f"v{number:03d}-edited.pptx"
        try:
            result: ApplyResult = apply_changes(self.current.path, cs, target)
        except ApplyError as exc:
            raise SessionError(f"apply refused: {exc}") from exc

        # A change that could not be written is a failure, not a quiet no-op.
        # Without this, an applier that matched nothing would produce an
        # unchanged file and a report saying "verified" — the worst possible
        # outcome, because it looks like success.
        if result.failed:
            detail = "; ".join(f"{c.id}: {why}" for c, why in result.failed)
            raise SessionError(f"{len(result.failed)} change(s) could not be applied: {detail}")
        if not result.applied:
            raise SessionError("no changes were applied; refusing to commit a no-op version")

        report = self.verify(self.current.path, target, cs)
        self.last_report = report

        if not report.deliverable:
            # Keep the artifact for inspection; do not advance the session.
            return report

        self.versions.append(
            Version(
                number=number,
                path=target,
                created_at=_now(),
                note=note or cs.instruction,
                changes=[c.id for c in result.applied],
            )
        )
        self.next_number = number + 1
        self._commit_history()
        return report

    def verify(
        self,
        source: str | Path,
        output: str | Path,
        cs: ChangeSet | None = None,
    ) -> ChangeReport:
        """Compare an output against a source and attribute every change."""
        fidelity: FidelityReport = compare(source, output)
        requested = [
            RequestedChange(slide=c.slide, description=c.describe(), target=c.target)
            for c in (cs.applied if cs else [])
        ]
        return build(fidelity, requested)

    # ── history ──────────────────────────────────────────────────────────────

    def rollback(self, to: int = 0) -> Version:
        """Return to an earlier version. The original is always version 0.

        Versions are addressed by number rather than by position, because
        numbers are never reused and so stop being contiguous once a rollback
        has been followed by another edit.
        """
        index = next((i for i, v in enumerate(self.versions) if v.number == to), None)
        if index is None:
            have = ", ".join(str(v.number) for v in self.versions)
            raise SessionError(f"no version {to}; have {have}")
        # Discarded artifacts stay on disk. They are evidence, they cost
        # nothing, and their numbers will never be handed out again.
        self.versions = self.versions[: index + 1]
        self.changeset = None
        self.last_report = None
        self._commit_history()
        return self.current

    def export(self, destination: str | Path) -> Path:
        """Write the current version out.

        Refuses if the last apply was blocked — a deck that failed verification
        must not leave the session by a side door.
        """
        if self.last_report is not None and not self.last_report.deliverable:
            raise SessionError(
                "current state failed verification and cannot be exported: "
                + "; ".join(self.last_report.blocking_reasons)
            )
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.current.path, destination)
        return destination

    def history(self) -> str:
        lines = [f"HISTORY — {self.source.name}"]
        for v in self.versions:
            marker = "*" if v is self.current else " "
            note = f"  {v.note}" if v.note else ""
            lines.append(f" {marker} v{v.number:03d}  {v.created_at}{note}")
            if v.changes:
                lines.append(f"        changes: {', '.join(v.changes)}")
        return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
