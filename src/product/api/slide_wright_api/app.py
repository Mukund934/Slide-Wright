"""The local HTTP surface over the engine.

ADR-0010. Every route here is a thin translation of an engine call into JSON.
Where a route looks like it is making a decision, it is not: `deliverable`,
`is_grounded`, `needs_review` and the blocking reasons all come from the engine,
because a product surface that computes its own second opinion on whether a deck
is safe has two answers to the only question that matters.

Three things this deliberately does not have:

  · authentication — there is one user, on their own machine (ADR-0008)
  · a job queue — one user, one deck at a time; ADR-0010 explains the trade
  · any outbound call — the engine's model access is the only network boundary,
    and it is optional
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from slide_wright import __version__ as engine_version
from slide_wright.audit import audit as audit_deck
from slide_wright.brand import plan_conformance, read_profile
from slide_wright.changeset import ChangeSet
from slide_wright.diff import diff
from slide_wright.inspect import EMU_PER_INCH
from slide_wright.layout import DEFAULT_TOLERANCE_EMU, plan_alignment
from slide_wright.session import Session, SessionError

from slide_wright_api import __version__
from slide_wright_api.contracts import (
    ApplyRequest,
    AuditOut,
    ChangeSetOut,
    DocumentOut,
    ExportRequest,
    OpenRequest,
    ProposeRequest,
    RevertRequest,
    ReviewRequest,
    TidyPlanOut,
    TidyRequest,
    VerificationOut,
    VersionOut,
)
from slide_wright_api.workspace import (
    Workspace,
    WorkspaceError,
    apply_locks,
    build_typed_changes,
)

# The client's dev server. Listed explicitly rather than with a wildcard: an
# allow-all origin on a loopback API means any page the user has open can drive
# their editor. The production build is served from this same origin and needs
# no entry at all.
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(*, workspace: Workspace | None = None, serve_client: bool = True) -> FastAPI:
    app = FastAPI(
        title="Slide-Wright",
        version=__version__,
        description="Change what I asked. Preserve everything else. Prove it.",
    )
    space = workspace or Workspace()
    app.state.workspace = space

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(DEV_ORIGINS),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    # ── health ───────────────────────────────────────────────────────────────

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        """What this install can actually do, stated before anyone relies on it."""
        model = _model_name()
        return {
            "ok": True,
            "api": __version__,
            "engine": engine_version,
            "model": model,
            "model_configured": model != "stub",
            "open_documents": len(space.sessions),
        }

    # ── documents ────────────────────────────────────────────────────────────

    @app.post("/api/documents", response_model=DocumentOut)
    def open_document(body: OpenRequest) -> DocumentOut:
        """Open a deck that is already on this machine.

        A path, not an upload. The engine snapshots it into a workspace beside
        itself; nothing crosses a network and nothing is copied anywhere the
        user did not choose.
        """
        doc_id, session = _guard(lambda: space.open(body.path))
        return DocumentOut.of(doc_id, session)

    @app.get("/api/documents/{doc_id}", response_model=DocumentOut)
    def read_document(doc_id: str) -> DocumentOut:
        return DocumentOut.of(doc_id, _require(space, doc_id))

    @app.delete("/api/documents/{doc_id}")
    def close_document(doc_id: str) -> dict[str, bool]:
        space.close(doc_id)
        return {"closed": True}

    @app.get("/api/documents/{doc_id}/audit", response_model=AuditOut)
    def audit(doc_id: str) -> AuditOut:
        """What is wrong with this deck.

        The full structural audit, not just the delivery gate. `Session.audit()`
        returns only the gate, which answers "may this be delivered" — a
        question about an edit that has already happened. What someone opening
        an inherited deck wants is the other one.
        """
        session = _require(space, doc_id)
        return AuditOut.of(audit_deck(session.deck(), session.source.name))

    # ── tidy ─────────────────────────────────────────────────────────────────

    @app.get("/api/documents/{doc_id}/tidy", response_model=TidyPlanOut)
    def tidy_plan(doc_id: str) -> TidyPlanOut:
        """What a tidy pass would change. Proposes nothing and writes nothing."""
        session = _require(space, doc_id)
        conformance, alignment = _plan_tidy(session)
        return TidyPlanOut(
            typefaces=len(conformance.changes),
            nudges=len(alignment.changes),
            tolerance_in=alignment.tolerance_emu / EMU_PER_INCH,
            worst_shift_in=alignment.worst_shift_emu / EMU_PER_INCH,
            skipped=[*conformance.skipped, *alignment.skipped],
            conforms_to="the deck's own theme",
        )

    @app.post("/api/documents/{doc_id}/tidy", response_model=ChangeSetOut)
    def tidy(doc_id: str, body: TidyRequest) -> ChangeSetOut:
        """Propose the corrections a tidy pass would make. Applies nothing.

        The CLI's `tidy` approves its own change set, which is right when a
        person typed the command naming the deck. Here it must not: consent
        precedes mutation everywhere else in this product, and a button that
        silently both proposes and applies would be the one place it does not.

        So this lands in the same change set the review panel is already
        showing, goes through the same approve step, and is applied and verified
        by the same path as everything else.
        """
        session = _require(space, doc_id)
        conformance, alignment = _plan_tidy(session)

        changeset = session.propose(f"tidy {session.source.name}")

        # The promise of a tidy is the ordinary one inverted: *change every
        # pixel of formatting, change not one word or number, and prove it.*
        #
        # The CLI proves it afterwards, by diffing the result and refusing if
        # any content moved. Declaring it as a lock is strictly stronger: the
        # engine then refuses a content-changing op at the moment it is added,
        # rather than detecting one after the file is written. Nothing a tidy
        # proposes is blocked by it — conformance emits typeface changes and
        # alignment emits moves — so the lock costs nothing and closes the door.
        #
        # It also shows up in the review panel under "Protected", which is where
        # a guarantee belongs: visible to the person being asked to approve.
        changeset.lock("wording", reason="a tidy changes presentation, never content")
        _guard(lambda: apply_locks(changeset, body.locks))
        for change in [*conformance.changes, *alignment.changes]:
            changeset.add(change)

        if not changeset.changes:
            session.changeset = None
            raise HTTPException(422, "There is nothing to tidy in this deck.")
        changeset.save(session.workspace / "changes.json")
        return ChangeSetOut.of(changeset)

    @app.get("/api/documents/{doc_id}/history", response_model=list[VersionOut])
    def history(doc_id: str) -> list[VersionOut]:
        session = _require(space, doc_id)
        return [VersionOut.of(v, current=v is session.current) for v in session.versions]

    # ── propose ──────────────────────────────────────────────────────────────

    @app.post("/api/documents/{doc_id}/propose", response_model=ChangeSetOut)
    def propose(doc_id: str, body: ProposeRequest) -> ChangeSetOut:
        """Work out what would change. Write nothing.

        This is the step the product is named for. The response is the contract
        the user reviews, and until they approve part of it no byte of their
        deck has moved.
        """
        session = _require(space, doc_id)
        changeset = session.propose(body.instruction)

        # Locks first: `ChangeSet.add` consults the locks that exist when a
        # change arrives, so one declared afterwards protects nothing.
        _guard(lambda: apply_locks(changeset, body.locks))

        deck = session.deck()
        for change in _guard(lambda: build_typed_changes(deck, body.sets)):
            changeset.add(change)

        if body.instruction:
            _plan_into(changeset, session, body.instruction)

        if not changeset.changes:
            session.changeset = None
            raise HTTPException(
                422,
                "Nothing to propose. Either edit something directly, or describe a "
                "change with a model key configured.",
            )
        changeset.save(session.workspace / "changes.json")
        return ChangeSetOut.of(changeset)

    @app.post("/api/documents/{doc_id}/review", response_model=ChangeSetOut)
    def review(doc_id: str, body: ReviewRequest) -> ChangeSetOut:
        """Approve or reject by id.

        Unknown ids are an error, never a silent no-op: a reviewer who believes
        they rejected something they did not is the worst outcome this screen
        can produce.
        """
        session = _require(space, doc_id)
        changeset = _require_changeset(session)

        known = {c.id for c in changeset.changes}
        unknown = sorted((set(body.approve) | set(body.reject)) - known)
        if unknown:
            raise HTTPException(422, f"no such change(s): {', '.join(unknown)}")

        if body.approve:
            changeset.approve(*body.approve)
        for change_id in body.reject:
            changeset.reject(change_id)
        if body.approve_all:
            changeset.approve_all(include_unreviewed=body.include_unreviewed)

        changeset.save(session.workspace / "changes.json")
        return ChangeSetOut.of(changeset)

    # ── apply ────────────────────────────────────────────────────────────────

    @app.post("/api/documents/{doc_id}/apply", response_model=VerificationOut)
    def apply(doc_id: str, body: ApplyRequest) -> VerificationOut:
        """Apply the approved changes and verify the result.

        A blocked result is a 200, not an error. The verification *worked*; its
        verdict was no. Returning 500 there would let a client's error handler
        swallow the single most important thing this product ever says.
        """
        session = _require(space, doc_id)
        _require_changeset(session)
        try:
            report = session.apply(body.note)
        except SessionError as exc:
            raise HTTPException(422, str(exc)) from exc
        return VerificationOut.of(report)

    @app.post("/api/documents/{doc_id}/apply/stream")
    def apply_stream(doc_id: str, body: ApplyRequest) -> StreamingResponse:
        """The same apply, narrated as it happens.

        Server-Sent Events because the work takes minutes and the UX
        architecture requires per-stage narration rather than a spinner.
        """
        session = _require(space, doc_id)
        _require_changeset(session)
        return StreamingResponse(
            _stream_apply(session, body.note),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── history ──────────────────────────────────────────────────────────────

    @app.post("/api/documents/{doc_id}/revert", response_model=DocumentOut)
    def revert(doc_id: str, body: RevertRequest) -> DocumentOut:
        """Make an earlier version current again.

        Nothing is undone. Every version stays on disk; going back is choosing
        one, which is why it cannot fail halfway.
        """
        session = _require(space, doc_id)
        try:
            session.rollback(body.to)
        except SessionError as exc:
            raise HTTPException(422, str(exc)) from exc
        return DocumentOut.of(doc_id, session)

    @app.get("/api/documents/{doc_id}/diff")
    def semantic_diff(doc_id: str, source: int = 0, output: int | None = None):
        """What a reader would notice between two versions.

        The counterpart to verification: `verify` answers whether the package is
        intact, this answers what actually reads differently.
        """
        session = _require(space, doc_id)
        before = _version(session, source)
        after = _version(session, session.current.number if output is None else output)
        result = diff(before.path, after.path)
        return {
            "source_version": before.number,
            "output_version": after.number,
            "changed": result.changed,
            "slides_added": result.slides_added,
            "slides_removed": result.slides_removed,
            "deltas": [
                {
                    "slide": d.slide,
                    "shape_id": d.shape_id,
                    "kind": d.kind,
                    "description": d.description,
                    # The axis the whole wedge turns on: a content change alters
                    # what the deck says, everything else alters how it looks.
                    "is_content": d.is_content,
                }
                for d in result.deltas
            ],
            "rendered": result.render(),
        }

    @app.post("/api/documents/{doc_id}/export")
    def export(doc_id: str, body: ExportRequest) -> dict[str, str]:
        """Write the current version out.

        Refuses a deck that failed verification. The engine enforces this; the
        route only translates the refusal, because a side door here would undo
        the entire guarantee.
        """
        session = _require(space, doc_id)
        try:
            written = session.export(body.destination)
        except SessionError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(422, f"could not write there: {exc}") from exc
        return {"path": str(written)}

    # ── the client ───────────────────────────────────────────────────────────

    if serve_client and WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="client")

    return app


# ── helpers ──────────────────────────────────────────────────────────────────


def _plan_tidy(session: Session):
    """Everything a tidy pass would correct, planned and nothing written.

    The deck's own theme is the authority, not a supplied template. That is the
    right default rather than a fallback: a deck assembled from several sources
    has a visual system of its own, and the pasted-in slides are the ones that
    depart from it.

    The tolerance is the planner's default, which is also what `tidy` and
    `align` use on the command line and what the audit reports against. If the
    three ever disagreed, the audit would name a number of shapes and the fix
    would touch a different set.
    """
    deck = session.deck()
    name = session.source.name
    conformance = plan_conformance(deck, read_profile(session.current.path), name)
    alignment = plan_alignment(deck, DEFAULT_TOLERANCE_EMU, name)
    return conformance, alignment


def _guard(call):
    """Run a workspace call, turning its refusal into a 422 a person can read."""
    try:
        return call()
    except WorkspaceError as exc:
        raise HTTPException(422, str(exc)) from exc


def _require(space: Workspace, doc_id: str) -> Session:
    try:
        return space.require(doc_id)
    except WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc


def _require_changeset(session: Session) -> ChangeSet:
    if session.changeset is None:
        raise HTTPException(422, "nothing has been proposed for this document yet")
    return session.changeset


def _version(session: Session, number: int):
    found = next((v for v in session.versions if v.number == number), None)
    if found is None:
        have = ", ".join(str(v.number) for v in session.versions)
        raise HTTPException(404, f"no version {number}; this document has {have}")
    return found


def _model_name() -> str:
    """Which provider a described change would reach, by name.

    Reported so the client can offer the text box honestly or not at all.
    "stub" means no key is configured: the deterministic half of the product is
    entirely unaffected (ADR-0008), and saying so plainly is better than a box
    that accepts a sentence and silently proposes nothing.
    """
    try:
        from slide_wright.llm.client import default_provider

        return default_provider().name
    except Exception:  # noqa: BLE001 - absence is the normal case, not an error
        return "stub"


def _plan_into(changeset: ChangeSet, session: Session, instruction: str) -> None:
    """Ask the model for changes and add whatever survives validation.

    Every proposal is validated against real deck structure before it is added,
    and arrives as origin MODEL, which means it is never auto-approved. The
    stub provider produces nothing; that is a configuration state the client is
    told about through /api/health, not a failure to hide here.
    """
    from slide_wright.llm.client import default_provider
    from slide_wright.planner import plan

    result = plan(
        session.deck(), instruction,
        deck_path=str(session.current.path), provider=default_provider(),
    )
    for change in result.changeset.changes:
        changeset.add(change)


def _stream_apply(session: Session, note: str) -> Iterator[str]:
    """Run the apply on a worker thread and forward its stages as they arrive.

    The engine's callback is synchronous and this generator is pulled by the
    server, so the two are joined by a queue. The worker's outcome -- report or
    exception -- is put on the same queue, which keeps ordering: the verdict can
    never overtake the stage that produced it.
    """
    events: queue.Queue = queue.Queue()
    DONE = object()

    def on_progress(stage: str, detail: str = "", **facts) -> None:
        events.put(("stage", {"stage": stage, "detail": detail, **facts}))

    def run() -> None:
        try:
            report = session.apply(note, on_progress=on_progress)
            events.put(("result", VerificationOut.of(report).model_dump()))
        except SessionError as exc:
            events.put(("error", {"message": str(exc)}))
        except Exception as exc:  # noqa: BLE001 - the client must hear about it either way
            events.put(("error", {"message": f"unexpected failure: {exc}"}))
        finally:
            events.put((DONE, None))

    worker = threading.Thread(target=run, name="slide-wright-apply", daemon=True)
    worker.start()

    while True:
        kind, payload = events.get()
        if kind is DONE:
            break
        yield f"event: {kind}\ndata: {json.dumps(payload)}\n\n"


app = create_app()
