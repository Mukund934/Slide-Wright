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
    SET_FONT = "set_font"           # typeface of one run
    SET_COLOR = "set_color"         # colour of one run
    DELETE_SHAPE = "delete_shape"


class Status(str, Enum):
    PROPOSED = "proposed"   # planner wrote it; nobody has looked
    APPROVED = "approved"   # user accepted
    REJECTED = "rejected"   # user declined; must not be applied
    APPLIED = "applied"     # written to the deck
    FAILED = "failed"       # attempted and could not be completed


class Origin(str, Enum):
    """Where a change came from. Determines how much scrutiny it deserves."""

    USER = "user"          # typed explicitly — highest trust
    SOURCE = "source"      # derived from a cited spreadsheet cell — deterministic
    MODEL = "model"        # proposed by a model — needs review
    RULE = "rule"          # produced by a deterministic rule, e.g. brand conformance


@dataclass
class Change:
    """One intended mutation, addressed to one object.

    A reviewer should be able to answer, from this record alone: *what* is
    changing, *where*, *why*, *who proposed it*, *how sure are they*, and *what
    else might it affect*. A change that cannot answer those is not reviewable,
    and an unreviewable change set is just an opaque diff with extra steps.
    """

    id: str
    op: Op
    slide: int
    target: str                      # shape id, or "shape_id/r{row}/c{col}" for a cell
    before: Any = None
    after: Any = None
    rationale: str = ""
    status: Status = Status.PROPOSED

    # ── provenance and confidence ────────────────────────────────────────────
    origin: Origin = Origin.USER
    citation: str = ""               # e.g. "comps.csv!B2" — a coordinate, not a claim
    confidence: float = 1.0          # 0-1; only meaningful for MODEL origin
    impact: str = ""                 # what else this plausibly affects
    object_kind: str = ""            # shape | table | chart | picture — for review UX

    @property
    def is_actionable(self) -> bool:
        return self.status is Status.APPROVED

    @property
    def is_grounded(self) -> bool:
        """True when the change traces to something checkable.

        A user typing a value and a spreadsheet cell are both grounded. A model
        proposing one is not — it needs a human, which is why model-origin
        changes are never auto-approved.
        """
        return self.origin in (Origin.USER, Origin.SOURCE, Origin.RULE) or bool(self.citation)

    @property
    def needs_review(self) -> bool:
        return self.origin is Origin.MODEL and not self.citation

    def describe(self) -> str:
        # Every member of Op must appear here. A missing entry raises KeyError
        # at report time -- after the edit has already been written -- so the
        # completeness of this map is asserted by a test.
        verb = {
            Op.SET_TEXT: "set text",
            Op.SET_TABLE_CELL: "set cell",
            Op.MOVE: "move",
            Op.RESIZE: "resize",
            Op.SET_FONT_SIZE: "set font size",
            Op.SET_FONT: "set typeface",
            Op.SET_COLOR: "set colour",
            Op.DELETE_SHAPE: "delete",
        }[self.op]
        if self.op in (Op.SET_TEXT, Op.SET_TABLE_CELL):
            return f"{verb} {self.before!r} -> {self.after!r}"
        if self.op is Op.DELETE_SHAPE:
            return verb
        return f"{verb} {self.before} -> {self.after}"


# Protection scopes a user can declare. Each is a promise the engine keeps,
# enforced at the verifier as well as the planner — a guarantee checked only at
# intent is not a guarantee.
SCOPES = ("slide", "shape", "numbers", "wording", "layout", "formatting",
          "tables", "charts", "media")


@dataclass
class Lock:
    """A user guarantee that something must not change.

    These exist because the requests people actually make are conditional:
    *"polish this deck but do not touch a single figure"*, *"restyle it but
    leave slide 4 alone — the partner signed it off"*. Those are constraints,
    not prompt text, so they live in the engine.
    """

    scope: str          # one of SCOPES
    target: str = ""    # slide number or shape id; empty means deck-wide
    reason: str = ""

    def __post_init__(self) -> None:
        if self.scope not in SCOPES:
            raise ValueError(
                f"unknown protection scope {self.scope!r}; expected one of {', '.join(SCOPES)}"
            )

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
        if self.scope == "wording":
            # "improve the layout, leave my words exactly as written"
            return change.op in (Op.SET_TEXT, Op.SET_TABLE_CELL)
        if self.scope == "layout":
            # "rewrite the copy, do not move anything"
            return change.op in (Op.MOVE, Op.RESIZE)
        if self.scope == "formatting":
            # "fix the words, leave my styling exactly as it is" -- the inverse
            # of `wording`, and the one a brand-conformance pass must honour.
            return change.op in (Op.SET_FONT, Op.SET_COLOR, Op.SET_FONT_SIZE)
        if self.scope in ("tables", "charts", "media"):
            # "leave the exhibits alone" — matched on the object being edited
            kind = {"tables": "table", "charts": "chart", "media": "picture"}[self.scope]
            return change.object_kind == kind
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

    def approve_all(self, *, include_unreviewed: bool = True) -> None:
        """Approve every proposed change.

        `include_unreviewed=False` holds back model-proposed changes that carry
        no citation — useful when a caller wants to auto-apply the grounded ones
        and put the rest in front of a person.
        """
        for c in self.changes:
            if c.status is not Status.PROPOSED:
                continue
            if not include_unreviewed and c.needs_review:
                continue
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
    def needing_review(self) -> list[Change]:
        """Proposed changes a model invented without a citation."""
        return [c for c in self.changes if c.status is Status.PROPOSED and c.needs_review]

    @property
    def grounded(self) -> list[Change]:
        return [c for c in self.changes if c.is_grounded]

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
                    {
                        **asdict(c),
                        "op": c.op.value,
                        "status": c.status.value,
                        "origin": c.origin.value,
                    }
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
                    origin=Origin(c.get("origin", "user")),
                    citation=c.get("citation", ""),
                    confidence=c.get("confidence", 1.0),
                    impact=c.get("impact", ""),
                    object_kind=c.get("object_kind", ""),
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
            provenance = []
            if c.citation:
                provenance.append(f"source {c.citation}")
            elif c.origin is not Origin.USER:
                provenance.append(c.origin.value)
            if c.origin is Origin.MODEL and c.confidence < 1.0:
                provenance.append(f"confidence {c.confidence:.0%}")
            if c.needs_review:
                provenance.append("NEEDS REVIEW")
            if provenance:
                lines.append(f"        [{' · '.join(provenance)}]")
            if c.impact:
                lines.append(f"        may also affect: {c.impact}")
        lines += [
            "",
            f"  {len(self.proposed)} proposed · {len(self.approved)} approved · "
            f"{len(self.rejected)} rejected · {len(self.applied)} applied",
        ]
        return "\n".join(lines)


def _contains_number(text: str) -> bool:
    return any(ch.isdigit() for ch in text)
