"""The open documents this process is holding.

A local product has one user and a handful of decks, so the registry is a dict
and the durable state stays where the engine already puts it: version files and
`history.json` on disk, next to the deck.

That split is deliberate. Restarting this process must lose a *view*, never a
version. Everything here is reconstructible by reopening the same path.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from slide_wright.changeset import Change, ChangeSet, Op, Origin
from slide_wright.inspect import DeckInfo
from slide_wright.session import Session, SessionError


class WorkspaceError(Exception):
    """A request that cannot be served. Carries a message meant for a person."""


@dataclass
class Workspace:
    """Every deck this process has open, addressed by a stable id."""

    sessions: dict[str, Session] = field(default_factory=dict)

    # ── opening ──────────────────────────────────────────────────────────────

    def open(self, path: str | Path) -> tuple[str, Session]:
        """Open a deck, or return the session already holding it.

        The id is derived from the resolved path rather than handed out in
        sequence, so a client that reloads reconnects to its own document
        instead of opening a second session over the same file -- two sessions
        on one deck would each believe they own its version numbering.
        """
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            resolved = (Path.cwd() / resolved).resolve()
        else:
            resolved = resolved.resolve()

        if not resolved.exists():
            raise WorkspaceError(f"no file at {resolved}")
        if not resolved.is_file():
            raise WorkspaceError(f"{resolved} is a folder, not a deck")
        if resolved.suffix.lower() not in (".pptx", ".potx"):
            raise WorkspaceError(
                f"{resolved.name} is not a PowerPoint file "
                "(.pptx or .potx). Slide-Wright reads the OOXML package directly, "
                "so there is nothing it can honestly do with another format."
            )

        doc_id = _identify(resolved)
        existing = self.sessions.get(doc_id)
        if existing is not None:
            return doc_id, existing

        try:
            session = Session.open(resolved)
        except SessionError as exc:
            raise WorkspaceError(str(exc)) from exc
        except Exception as exc:  # UnsafePackageError and anything the reader raises
            raise WorkspaceError(
                f"{resolved.name} could not be opened as a PowerPoint package: {exc}"
            ) from exc

        self.sessions[doc_id] = session
        return doc_id, session

    def require(self, doc_id: str) -> Session:
        session = self.sessions.get(doc_id)
        if session is None:
            raise WorkspaceError(
                "that document is not open. It may have been opened by an earlier "
                "run of the app -- open the file again and its history comes back."
            )
        return session

    def close(self, doc_id: str) -> None:
        """Drop the view. The workspace on disk is untouched and still complete."""
        self.sessions.pop(doc_id, None)


def _identify(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]


# ── building changes the user typed ──────────────────────────────────────────


def find_shape(deck: DeckInfo, slide: int, target: str):
    """Locate the shape a target addresses, or None.

    Targets are `shape_id` or `shape_id/r{row}/c{col}`; the shape is the part
    before the first slash either way.
    """
    shape_id = target.split("/")[0]
    for slide_info in deck.slides:
        if slide_info.number != slide:
            continue
        for shape in slide_info.shapes:
            if shape.id == shape_id:
                return shape
    return None


CELL_TARGET = re.compile(r"/(r\d+/c\d+)$")


def current_text(deck: DeckInfo, slide: int, target: str) -> str | None:
    """What is there now, read from the deck rather than taken from the client.

    The applier matches on `before`. Trusting the client for it means a stale
    tab can propose a change whose `before` no longer exists, and the failure
    surfaces at apply time as "target not found" -- long after the reviewer
    approved it. Reading it here fails at propose time instead, which is the
    only place it can be explained.

    It also decides whether a `numbers` lock fires: the lock tests `before` for
    digits, so a cell whose `before` was left empty would be editable under a
    lock whose entire purpose is to protect figures.
    """
    shape = find_shape(deck, slide, target)
    if shape is None:
        return None
    cell = CELL_TARGET.search(target)
    return shape.table_cells.get(cell.group(1)) if cell else shape.text


def build_typed_changes(deck: DeckInfo, specs, start: int = 1) -> list[Change]:
    """Turn the client's edits into changes, refusing anything that does not exist.

    Origin is USER: these were typed by the person who owns the deck, so they
    are grounded and need no model review. That is the *only* origin this path
    may produce -- a model's proposal arriving through the same door dressed as
    a user edit would bypass the review the whole product is built around.
    """
    changes: list[Change] = []
    for index, spec in enumerate(specs, start=start):
        op = Op(spec.op)
        if op in (Op.SET_TEXT, Op.SET_TABLE_CELL):
            before = current_text(deck, spec.slide, spec.target)
            if before is None:
                raise WorkspaceError(
                    f"slide {spec.slide} has no object {spec.target!r} to edit"
                )
        else:
            shape = find_shape(deck, spec.slide, spec.target)
            if shape is None:
                raise WorkspaceError(
                    f"slide {spec.slide} has no object {spec.target!r} to edit"
                )
            before = _geometry_before(shape, op)

        changes.append(
            Change(
                id=f"u{index}",
                op=op,
                slide=spec.slide,
                target=spec.target,
                before=before,
                after=spec.after,
                rationale=spec.rationale,
                origin=Origin.USER,
                object_kind=_kind(deck, spec.slide, spec.target),
            )
        )
    return changes


def _geometry_before(shape, op: Op):
    if op is Op.MOVE:
        return [shape.x, shape.y]
    if op is Op.RESIZE:
        return [shape.cx, shape.cy]
    if op is Op.SET_FONT_SIZE:
        return shape.runs[0].size_pt if shape.runs else None
    if op is Op.SET_FONT:
        return shape.runs[0].font if shape.runs else None
    if op is Op.SET_COLOR:
        return shape.runs[0].color if shape.runs else None
    return None


def _kind(deck: DeckInfo, slide: int, target: str) -> str:
    shape = find_shape(deck, slide, target)
    return shape.kind if shape else ""


def apply_locks(changeset: ChangeSet, specs) -> None:
    """Declare the guarantees before adding any change.

    Order matters and is not incidental: `ChangeSet.add` consults the locks that
    exist at the moment the change arrives. Locks added afterwards protect
    nothing that is already in the set.
    """
    for spec in specs:
        try:
            changeset.lock(spec.scope, spec.target, spec.reason)
        except ValueError as exc:
            raise WorkspaceError(str(exc)) from exc
