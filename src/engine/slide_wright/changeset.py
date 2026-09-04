"""The change set: the contract between intent and mutation.

Nothing may modify a deck that is not written here first. That is what makes
"we changed only what you asked" checkable rather than aspirational — the
verifier compares what moved against this list, and an unattributed change
fails the job.

The change set is produced before anything is written, is reviewable, and is
individually revertible. A user who rejects entry 3 gets entries 1, 2 and 4.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Op(str, Enum):
    """What a change does. Deliberately small: every op must be verifiable."""

    SET_TEXT = "set_text"           # replace the text of a run or shape
    SET_TABLE_CELL = "set_table_cell"
    MOVE = "move"                   # change x/y
    RESIZE = "resize"               # change cx/cy
    SET_FONT_SIZE = "set_font_size"
    SET_COLOR = "set_color"
    DELETE_SHAPE = "delete_shape"


class Status(str, Enum):
    PROPOSED = "proposed"   # planner wrote it; nobody has looked
    APPROVED = "approved"   # user accepted
    REJECTED = "rejected"   # user declined; must not be applied
    APPLIED = "applied"     # written to the deck
    FAILED = "failed"       # attempted and could not be completed


@dataclass
class Change:
    """One intended mutation, addressed to one object."""

    id: str
    op: Op
    slide: int
    target: str                      # shape id, or "shape_id/r{row}/c{col}" for a cell
    before: Any = None
    after: Any = None
    rationale: str = ""
    status: Status = Status.PROPOSED

    @property
    def is_actionable(self) -> bool:
        return self.status is Status.APPROVED

    def describe(self) -> str:
        verb = {
            Op.SET_TEXT: "set text",
            Op.SET_TABLE_CELL: "set cell",
            Op.MOVE: "move",
            Op.RESIZE: "resize",
            Op.SET_FONT_SIZE: "set font size",
            Op.SET_COLOR: "set colour",
            Op.DELETE_SHAPE: "delete",
        }[self.op]
        if self.op in (Op.SET_TEXT, Op.SET_TABLE_CELL):
            return f"{verb} {self.before!r} -> {self.after!r}"
        if self.op is Op.DELETE_SHAPE:
            return verb
        return f"{verb} {self.before} -> {self.after}"


@dataclass
class Lock:
    """A user guarantee that something must not change.

    Enforced at the verifier, not only at the planner. A guarantee checked only
    at intent is not a guarantee.
    """

    scope: str          # "slide" | "shape" | "numbers"
    target: str = ""    # slide number or shape id; empty means deck-wide
    reason: str = ""

    def blocks(self, change: Change) -> bool:
        if self.scope == "slide":
            return not self.target or str(change.slide) == str(self.target)
        if self.scope == "shape":
            return change.target.split("/")[0] == self.target
        if self.scope == "numbers":
            # "polish it, but do not touch a single figure"
            return change.op in (Op.SET_TEXT, Op.SET_TABLE_CELL) and _contains_number(
                str(change.before)
            )
        return False


@dataclass
class ChangeSet:
    """An ordered, reviewable set of intended changes for one deck."""

    deck: str
    instruction: str = ""
    changes: list[Change] = field(default_factory=list)
    locks: list[Lock] = field(default_factory=list)

    # ── construction ─────────────────────────────────────────────────────────

    def add(self, change: Change) -> Change:
        """Add a change, refusing anything a lock forbids."""
        for lock in self.locks:
            if lock.blocks(change):
                change.status = Status.REJECTED
                change.rationale = (
                    f"blocked by {lock.scope} lock"
                    + (f" ({lock.reason})" if lock.reason else "")
                )
                break
        self.changes.append(change)
        return change

    def lock(self, scope: str, target: str = "", reason: str = "") -> Lock:
        lk = Lock(scope=scope, target=target, reason=reason)
        self.locks.append(lk)
        return lk

    # ── review ───────────────────────────────────────────────────────────────

    def approve_all(self) -> None:
        for c in self.changes:
            if c.status is Status.PROPOSED:
                c.status = Status.APPROVED

    def approve(self, *ids: str) -> None:
        for c in self.changes:
            if c.id in ids and c.status is Status.PROPOSED:
                c.status = Status.APPROVED

    def reject(self, *ids: str) -> None:
        for c in self.changes:
            if c.id in ids and c.status is not Status.APPLIED:
                c.status = Status.REJECTED

    # ── selectors ────────────────────────────────────────────────────────────

    @property
    def proposed(self) -> list[Change]:
        return [c for c in self.changes if c.status is Status.PROPOSED]

    @property
    def approved(self) -> list[Change]:
        return [c for c in self.changes if c.status is Status.APPROVED]

    @property
    def rejected(self) -> list[Change]:
        return [c for c in self.changes if c.status is Status.REJECTED]

    @property
    def applied(self) -> list[Change]:
        return [c for c in self.changes if c.status is Status.APPLIED]

    @property
    def target_slides(self) -> set[int]:
        """Slides the approved changes are permitted to touch."""
        return {c.slide for c in self.approved}

    @property
    def applied_slides(self) -> set[int]:
        return {c.slide for c in self.applied}

    def by_slide(self, n: int) -> list[Change]:
        return [c for c in self.changes if c.slide == n]

    # ── serialisation ────────────────────────────────────────────────────────

    def to_json(self) -> str:
        return json.dumps(
            {
                "deck": self.deck,
                "instruction": self.instruction,
                "locks": [asdict(l) for l in self.locks],
                "changes": [
                    {**asdict(c), "op": c.op.value, "status": c.status.value}
                    for c in self.changes
                ],
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, raw: str) -> ChangeSet:
        data = json.loads(raw)
        cs = cls(deck=data["deck"], instruction=data.get("instruction", ""))
        cs.locks = [Lock(**l) for l in data.get("locks", [])]
        for c in data.get("changes", []):
            cs.changes.append(
                Change(
                    id=c["id"], op=Op(c["op"]), slide=c["slide"], target=c["target"],
                    before=c.get("before"), after=c.get("after"),
                    rationale=c.get("rationale", ""), status=Status(c["status"]),
                )
            )
        return cs

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    # ── presentation ─────────────────────────────────────────────────────────

    def render(self) -> str:
        lines = [f"CHANGE SET — {Path(self.deck).name}"]
        if self.instruction:
            lines.append(f'  request: "{self.instruction}"')
        if self.locks:
            lines.append("  locks:")
            for l in self.locks:
                where = l.target or "deck-wide"
                lines.append(f"    · {l.scope} {where}" + (f" — {l.reason}" if l.reason else ""))
        lines.append("")
        if not self.changes:
            lines.append("  no changes proposed")
            return "\n".join(lines)
        for c in self.changes:
            mark = {
                Status.PROPOSED: "?", Status.APPROVED: "+", Status.REJECTED: "-",
                Status.APPLIED: "*", Status.FAILED: "!",
            }[c.status]
            lines.append(f"  [{mark}] {c.id}  slide {c.slide}  {c.describe()}")
            if c.rationale:
                lines.append(f"        {c.rationale}")
        lines += [
            "",
            f"  {len(self.proposed)} proposed · {len(self.approved)} approved · "
            f"{len(self.rejected)} rejected · {len(self.applied)} applied",
        ]
        return "\n".join(lines)


def _contains_number(text: str) -> bool:
    return any(ch.isdigit() for ch in text)
