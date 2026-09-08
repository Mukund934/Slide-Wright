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
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from slide_wright.apply import ApplyError, ApplyResult, apply_changes
from slide_wright.changeset import Change, ChangeSet, Status
from slide_wright.fidelity import FidelityReport, compare
from slide_wright.gate import GateResult, check
from slide_wright.inspect import DeckInfo, inspect
from slide_wright.package import Package
from slide_wright.report import ChangeReport, RequestedChange, build


class SessionError(Exception):
    """The session refused to proceed. Always preferable to a silent bad deck."""


# Called with (stage, human-readable detail, **facts). Stages are:
#   applying · applied · verifying · verified · blocked · refused
Progress = Callable[..., None]


def _emitter(on_progress: Progress | None) -> Progress:
    """Wrap a progress callback so it cannot take the apply down with it.

    The listener is a display concern -- an SSE stream whose client closed the
    tab, most often. An edit that has already written a file must not fail
    because nobody is watching it; the version would be on disk with the
    session believing it never happened.
    """
    if on_progress is None:
        return lambda *args, **kwargs: None

    def emit(stage: str, detail: str = "", **facts) -> None:
        try:
            on_progress(stage, detail, **facts)
        except Exception:  # noqa: BLE001 - a broken listener is not a broken edit
            pass

    return emit


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

    # What history.json said the last time this session read or wrote it. A
    # second session over the same workspace hands out the same version number,
    # writes the same filename, and the later write wins -- measured: two
    # sessions each committed "version 1", both believed they held their own
    # edit, and the file on disk was one of them. Neither was told.
    _history_seen: tuple | None = field(default=None, repr=False)

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
        self._history_seen = self._fingerprint()

    def _fingerprint(self) -> tuple | None:
        """What the workspace holds, ignoring anything two sessions could
        legitimately disagree about.

        Numbers and filenames only -- not timestamps or notes. Two sessions
        opening the same fresh workspace both write version 0 and would differ
        by the second on `created_at`, and refusing on that would be a false
        alarm about a state they actually agree on.
        """
        if not self._history_file.is_file():
            return None
        try:
            saved = json.loads(self._history_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return None
        return (
            saved.get("next_number"),
            tuple((v.get("number"), v.get("file")) for v in saved.get("versions", [])),
        )

    def _assert_workspace_unmoved(self) -> None:
        """Refuse to write over a version another session created.

        This narrows the race rather than eliminating it: two processes could
        still pass this check within the same instant and both write. It is
        checked immediately before committing, which is the last moment the
        answer is still useful, and it catches the case that actually happens --
        two long-lived sessions on one workspace, a CLI run beside the app.
        """
        current = self._fingerprint()
        if current == self._history_seen:
            return
        raise SessionError(
            "the workspace changed since this session opened it -- another "
            "session or a CLI run has written to it. Reopen the deck to "
            "continue; committing now would overwrite that work without "
            "either side being told."
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
        self._history_seen = self._fingerprint()

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
        # Starting a new set abandons whatever the last one was blocked on. Left
        # standing, that verdict went on refusing every export -- of a version
        # that had itself passed verification, because a blocked apply never
        # advances the session.
        self.last_report = None
        return self.changeset

    def require_changeset(self) -> ChangeSet:
        if self.changeset is None:
            raise SessionError("no change set; call propose() first")
        return self.changeset

    # ── apply / verify ───────────────────────────────────────────────────────

    def apply(self, note: str = "", on_progress: Progress | None = None) -> ChangeReport:
        """Apply approved changes, verify the result, and commit a version.

        The new version is committed only if verification passes. A blocked
        result leaves the session on its previous version and the output on
        disk for inspection.

        `on_progress` is called as each stage begins and ends. It exists because
        applying and verifying a real deck takes minutes, and a caller with a
        person waiting needs to say what is happening. Every stage reported is
        one that actually occurs -- there is no interpolated percentage, because
        the engine does not know how long a stage will take and a made-up number
        is the specific dishonesty this product exists to avoid.
        """
        emit = _emitter(on_progress)
        cs = self.require_changeset()
        if not cs.approved:
            raise SessionError("no approved changes; approve some first")

        # A change set describes one version. Every change carries a `before`
        # read from that version, and the applier writes against whatever is
        # current -- so a set that outlived its parent writes coordinates and
        # replacements computed from a file nobody is looking at any more.
        #
        # Measured: propose against v000, apply (v001 committed, set still
        # live), add one more move to the same set and apply again. The shape
        # went to `original + 200000` rather than `current + 200000`, silently
        # discarding v001's own edit -- and the report said VERIFIED, because
        # the slide was in the change set and every part was accounted for.
        if Path(cs.deck).resolve() != self.current.path.resolve():
            raise SessionError(
                f"this change set was built against {Path(cs.deck).name} and the "
                f"session is on {self.current.path.name}. Propose again: its "
                "changes describe a version that is no longer current."
            )

        self._assert_workspace_unmoved()

        # Never derived from len(versions): after a rollback that would reuse
        # a number whose file is still on disk, overwriting real history.
        number = self.next_number
        target = self.workspace / f"v{number:03d}-edited.pptx"
        emit("applying", f"writing {len(cs.approved)} approved change(s)",
             slides=sorted(cs.target_slides))
        try:
            result: ApplyResult = apply_changes(self.current.path, cs, target)
        except ApplyError as exc:
            emit("refused", str(exc))
            raise SessionError(f"apply refused: {exc}") from exc

        # A change that could not be written is a failure, not a quiet no-op.
        # Without this, an applier that matched nothing would produce an
        # unchanged file and a report saying "verified" — the worst possible
        # outcome, because it looks like success.
        if result.failed:
            detail = "; ".join(f"{c.id}: {why}" for c, why in result.failed)
            emit("refused", f"{len(result.failed)} change(s) could not be applied")
            raise SessionError(f"{len(result.failed)} change(s) could not be applied: {detail}")
        if not result.applied:
            emit("refused", "nothing was written")
            raise SessionError("no changes were applied; refusing to commit a no-op version")

        emit("applied", f"{len(result.applied)} change(s) written",
             slides=sorted(cs.applied_slides))
        emit("verifying", "comparing every part against the file you supplied")
        report = self.verify(self.current.path, target, cs)
        self.last_report = report

        if not report.deliverable:
            # Keep the artifact for inspection; do not advance the session.
            emit("blocked", "; ".join(report.blocking_reasons))
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
        self._assert_workspace_unmoved()
        self._commit_history()

        # Closed on commit, not on a blocked result. A blocked set is still the
        # user's work and they may want to adjust it; a committed one describes
        # the previous version and is refused above if it is used again. Saying
        # so here is clearer than letting them find out at the next apply.
        self.changeset = None

        emit("verified", f"version {number} committed", version=number)
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
        # Rewrites history, so it can discard another session's versions just
        # as an apply can overwrite them.
        self._assert_workspace_unmoved()
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
                "the last apply was blocked and its result must not leave the "
                "session: " + "; ".join(self.last_report.blocking_reasons)
                + f". Version {self.current.number} is still intact -- propose "
                "again, or roll back, to export it."
            )
        destination = Path(destination)
        if destination.is_dir():
            # `shutil.copy` would happily write *into* it, under the workspace's
            # own filename, and hand back the folder as if that were the file.
            # The caller was told a path where nothing exists, and the file that
            # did get written was called v001-edited.pptx -- an internal name
            # the user never chose and would not recognise.
            raise SessionError(
                f"{destination} is a folder. Give the name to write, not the "
                "place to put it."
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Beside, then moved. An interrupted export is the worst version of this
        # failure: the truncated file is sitting at a path the user chose,
        # under the name they gave it, and it is the one they attach.
        scratch = destination.with_name(f"{destination.name}.{os.getpid()}.partial")
        try:
            shutil.copy(self.current.path, scratch)
            os.replace(scratch, destination)
        finally:
            scratch.unlink(missing_ok=True)
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
