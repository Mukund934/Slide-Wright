"""Wire contracts between the engine and the client.

These exist for one reason: the client must not reach into engine dataclasses.
The engine's shapes are free to change for engine reasons; this file is where
that change becomes visible, and where the TypeScript types are generated from.

Two rules held throughout:

  · Every field the client renders is computed here from engine values. The
    client never re-derives a fact the engine already knows -- a second
    implementation of "is this deliverable" is a second answer to it.
  · Units cross the wire as the engine holds them (EMU), with the slide
    dimensions alongside. Converting to pixels here would bake in a viewport
    the API cannot see.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from slide_wright.changeset import Change, ChangeSet, Lock
from slide_wright.audit import DeckAudit, Observation
from slide_wright.gate import Finding, GateResult
from slide_wright.inspect import DeckInfo, ShapeInfo, SlideInfo, TextRun
from slide_wright.report import ChangeReport
from slide_wright.session import Session, Version

# ── deck structure ───────────────────────────────────────────────────────────


class RunOut(BaseModel):
    text: str
    size_pt: float | None = None
    bold: bool = False
    italic: bool = False
    font: str | None = None
    color: str | None = None

    @classmethod
    def of(cls, run: TextRun) -> RunOut:
        return cls(
            text=run.text, size_pt=run.size_pt, bold=run.bold,
            italic=run.italic, font=run.font, color=run.color,
        )


class ShapeOut(BaseModel):
    """One object, with enough geometry to draw it and enough identity to address it."""

    id: str
    name: str
    kind: str
    placeholder_type: str | None = None
    x: int | None = None
    y: int | None = None
    cx: int | None = None
    cy: int | None = None
    rotation_deg: float | None = None
    # True when the box came from the layout or master. The shape is really
    # there and really that size; it just has no position of its own, which is
    # why the applier refuses to move it.
    geometry_inherited: bool = False
    geometry: str | None = None
    runs: list[RunOut] = Field(default_factory=list)
    table_rows: int = 0
    table_cols: int = 0
    # Keyed "r0/c0", zero-based, matching the suffix a change target carries.
    # The client appends the key to the shape id to address a cell.
    table_cells: dict[str, str] = Field(default_factory=dict)
    child_count: int = 0
    text: str = ""

    @classmethod
    def of(cls, shape: ShapeInfo) -> ShapeOut:
        return cls(
            id=shape.id, name=shape.name, kind=shape.kind,
            placeholder_type=shape.placeholder_type,
            x=shape.x, y=shape.y, cx=shape.cx, cy=shape.cy,
            rotation_deg=shape.rotation_deg,
            geometry_inherited=shape.geometry_inherited,
            geometry=shape.geometry,
            runs=[RunOut.of(r) for r in shape.runs],
            table_rows=shape.table_rows, table_cols=shape.table_cols,
            table_cells=dict(shape.table_cells),
            child_count=shape.child_count, text=shape.text,
        )


class SlideOut(BaseModel):
    number: int
    part_name: str
    layout: str | None = None
    title: str | None = None
    word_count: int = 0
    shapes: list[ShapeOut] = Field(default_factory=list)

    @classmethod
    def of(cls, slide: SlideInfo) -> SlideOut:
        return cls(
            number=slide.number, part_name=slide.part_name, layout=slide.layout,
            title=slide.title, word_count=slide.word_count,
            shapes=[ShapeOut.of(s) for s in slide.shapes],
        )


class DeckOut(BaseModel):
    slide_width: int
    slide_height: int
    theme_fonts: dict[str, str] = Field(default_factory=dict)
    slides: list[SlideOut] = Field(default_factory=list)

    @classmethod
    def of(cls, deck: DeckInfo) -> DeckOut:
        return cls(
            slide_width=deck.slide_width, slide_height=deck.slide_height,
            theme_fonts=dict(deck.theme_fonts),
            slides=[SlideOut.of(s) for s in deck.slides],
        )


# ── change set ───────────────────────────────────────────────────────────────


class ChangeOut(BaseModel):
    """One proposed mutation, carrying everything a reviewer needs to decide.

    `is_grounded`, `needs_review` and the description are the engine's answers,
    not the client's. A reviewer deciding on a different basis from the one the
    verifier enforces is the failure this whole product is built against.
    """

    id: str
    op: str
    slide: int
    target: str
    before: Any = None
    after: Any = None
    rationale: str = ""
    status: str
    origin: str
    citation: str = ""
    confidence: float = 1.0
    impact: str = ""
    object_kind: str = ""
    description: str = ""
    is_grounded: bool = True
    needs_review: bool = False

    @classmethod
    def of(cls, change: Change) -> ChangeOut:
        return cls(
            id=change.id, op=change.op.value, slide=change.slide,
            target=change.target, before=change.before, after=change.after,
            rationale=change.rationale, status=change.status.value,
            origin=change.origin.value, citation=change.citation,
            confidence=change.confidence, impact=change.impact,
            object_kind=change.object_kind, description=change.describe(),
            is_grounded=change.is_grounded, needs_review=change.needs_review,
        )


class LockOut(BaseModel):
    scope: str
    target: str = ""
    reason: str = ""

    @classmethod
    def of(cls, lock: Lock) -> LockOut:
        return cls(scope=lock.scope, target=lock.target, reason=lock.reason)


class ChangeSetOut(BaseModel):
    deck: str
    instruction: str = ""
    changes: list[ChangeOut] = Field(default_factory=list)
    locks: list[LockOut] = Field(default_factory=list)
    proposed_count: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    applied_count: int = 0
    needs_review_count: int = 0

    @classmethod
    def of(cls, cs: ChangeSet) -> ChangeSetOut:
        return cls(
            deck=cs.deck, instruction=cs.instruction,
            changes=[ChangeOut.of(c) for c in cs.changes],
            locks=[LockOut.of(lock) for lock in cs.locks],
            proposed_count=len(cs.proposed), approved_count=len(cs.approved),
            rejected_count=len(cs.rejected), applied_count=len(cs.applied),
            needs_review_count=len(cs.needing_review),
        )


# ── audit ────────────────────────────────────────────────────────────────────


class FindingOut(BaseModel):
    code: str
    severity: str
    slide: int
    message: str
    repair: str = ""
    shape_id: str = ""
    shape_name: str = ""

    @classmethod
    def of(cls, finding: Finding) -> FindingOut:
        return cls(
            code=finding.code, severity=finding.severity.value, slide=finding.slide,
            message=finding.message, repair=finding.repair,
            shape_id=finding.shape_id, shape_name=finding.shape_name,
        )


class GateOut(BaseModel):
    """May this be delivered? A per-slide, pass/fail question about an edit."""

    passed: bool
    error_count: int
    warning_count: int
    findings: list[FindingOut] = Field(default_factory=list)

    @classmethod
    def of(cls, result: GateResult) -> GateOut:
        return cls(
            passed=result.passed, error_count=len(result.errors),
            warning_count=len(result.warnings),
            findings=[FindingOut.of(f) for f in result.findings],
        )


class ObservationOut(BaseModel):
    """One thing true about the deck that its author would want to know.

    `remedy` is the engine's answer to "can this be corrected without a person
    deciding", and the client must not compute its own. A surface that decided
    for itself which findings are fixable would eventually offer a fix the
    engine cannot perform — which is the one promise this product cannot break.
    """

    area: str
    slides: list[int] = Field(default_factory=list)
    where: str
    message: str
    suggestion: str = ""
    severity: str
    remedy: str = ""
    is_automatable: bool = False

    @classmethod
    def of(cls, observation: Observation) -> ObservationOut:
        return cls(
            area=observation.area.value, slides=list(observation.slides),
            where=observation.where, message=observation.message,
            suggestion=observation.suggestion, severity=observation.severity.value,
            remedy=observation.remedy.value,
            is_automatable=observation.is_automatable,
        )


class AuditOut(BaseModel):
    """What should change? Asked of a deck nobody has touched yet.

    Carries both halves the engine computes: the structural observations and the
    delivery gate. They answer different questions and are shown as different
    things, so flattening them here would lose the distinction before the client
    ever sees it.
    """

    deck: str
    slide_count: int
    word_count: int
    words_per_slide: float
    observations: list[ObservationOut] = Field(default_factory=list)
    gate: GateOut
    automatable_count: int = 0
    rendered: str = ""

    @classmethod
    def of(cls, result: DeckAudit) -> AuditOut:
        return cls(
            deck=result.deck,
            slide_count=result.slide_count,
            word_count=result.word_count,
            words_per_slide=result.words_per_slide,
            observations=[ObservationOut.of(o) for o in result.observations],
            gate=GateOut.of(result.gate) if result.gate else GateOut(
                passed=True, error_count=0, warning_count=0
            ),
            automatable_count=sum(1 for o in result.observations if o.is_automatable),
            rendered=result.render(),
        )


class MatchOut(BaseModel):
    """One deck cell a source row/column pair explains."""

    slide: int
    target: str
    current: str
    proposed: str
    citation: str


class RefreshPlanOut(BaseModel):
    """What a refresh would do, before anything is proposed.

    Three outcomes, and all three are shown. `updates` is the obvious one.

    `confirmed` is the unusual one and is worth as much: cells the source agrees
    with, which is positive evidence that a figure is *still right*. A tool that
    reports only what it changed leaves the reader unable to tell "checked and
    correct" from "never looked at".

    `unmatched` is the honest one: figures the source cannot explain. They are
    left untouched, because a figure this engine cannot justify with a
    coordinate is a figure it will not change.
    """

    sources: list[str] = Field(default_factory=list)
    tables: int = 0
    updates: list[MatchOut] = Field(default_factory=list)
    confirmed: list[MatchOut] = Field(default_factory=list)
    unmatched: list[str] = Field(default_factory=list)
    rendered: str = ""

    @classmethod
    def of(cls, plan, names: list[str], tables: int) -> RefreshPlanOut:
        return cls(
            sources=names,
            tables=tables,
            updates=[_match(m, changed=True) for m in plan.updates],
            confirmed=[_match(m, changed=False) for m in plan.confirmed],
            unmatched=[
                f"slide {slide}: {label!r} — {why}" for slide, label, why in plan.unmatched
            ],
            rendered=plan.render(),
        )


def _match(match, *, changed: bool) -> MatchOut:
    return MatchOut(
        slide=match.slide,
        target=match.target,
        current=match.current,
        # A confirmed cell proposes nothing: the source and the deck already
        # agree, and showing an "after" identical to the "before" would read as
        # a change nobody asked for.
        proposed=match.citation.value if changed else "",
        citation=match.citation.reference,
    )


class TidyPlanOut(BaseModel):
    """What a tidy pass would change, before anything is proposed.

    Two counts and a bound. The bound matters: alignment is only ever allowed to
    move a shape onto a line its neighbours already sit on, so `worst_shift_in`
    can never exceed the tolerance, and showing both is what makes "nothing here
    is visible to the eye" checkable rather than asserted.
    """

    typefaces: int
    nudges: int
    tolerance_in: float
    worst_shift_in: float
    skipped: list[str] = Field(default_factory=list)
    conforms_to: str


# ── verification ─────────────────────────────────────────────────────────────


class RequestedOut(BaseModel):
    slide: int
    description: str
    target: str = ""


class CensusOut(BaseModel):
    """Native object counts, source against output. The unarguable half of the proof."""

    label: str
    source: int
    output: int
    intact: bool


class VerificationOut(BaseModel):
    """The trust surface.

    `deliverable` is the engine's fail-closed verdict and is never recomputed by
    the client. `blocking_reasons` is why, in the engine's own words.
    """

    deliverable: bool
    identical_parts: int
    total_parts: int
    fidelity_score: float
    changed_parts: list[str] = Field(default_factory=list)
    changed_slides: list[int] = Field(default_factory=list)
    untouched_slides: int = 0
    unrequested_slides: list[int] = Field(default_factory=list)
    unrequested_parts: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    requested: list[RequestedOut] = Field(default_factory=list)
    census: list[CensusOut] = Field(default_factory=list)
    rendered: str = ""

    @classmethod
    def of(cls, report: ChangeReport) -> VerificationOut:
        fidelity = report.fidelity
        return cls(
            deliverable=report.deliverable,
            identical_parts=len(fidelity.identical),
            total_parts=fidelity.total_source_parts,
            fidelity_score=fidelity.fidelity_score,
            changed_parts=[d.name for d in fidelity.changed],
            changed_slides=sorted(fidelity.changed_slide_numbers),
            untouched_slides=_untouched(fidelity),
            unrequested_slides=report.unrequested_slide_changes,
            unrequested_parts=report.unrequested_part_changes,
            blocking_reasons=report.blocking_reasons,
            requested=[
                RequestedOut(slide=c.slide, description=c.description, target=c.target)
                for c in report.requested
            ],
            census=_census(fidelity),
            rendered=report.render(),
        )


def _untouched(fidelity) -> int:
    return len([
        d for d in fidelity.identical if getattr(d, "slide_number", None) is not None
    ])


# The census rows worth showing a reviewer, in the order the CLI prints them.
# `pictures` is deliberately absent: gaining one is the rasterisation signature,
# which `deliverable` already blocks on, and showing it as a count invites the
# reading that more is better.
CENSUS_ROWS = (
    ("native tables", "tables"),
    ("chart parts", "chart_parts"),
    ("diagram parts", "diagram_parts"),
    ("media parts", "media_parts"),
    ("embedded workbooks", "embeddings"),
    ("editable text runs", "text_runs"),
)


def _census(fidelity) -> list[CensusOut]:
    """Native object counts, source against output.

    Read defensively: the census is engine detail and this is a presentation
    concern. A counter this file does not recognise must degrade the display,
    never fail the request that was about to tell someone their deck is safe.
    """
    out: list[CensusOut] = []
    before = getattr(fidelity, "source_census", None)
    after = getattr(fidelity, "output_census", None)
    if before is None or after is None:
        return out
    for label, attr in CENSUS_ROWS:
        source, output = getattr(before, attr, None), getattr(after, attr, None)
        if source is None or output is None:
            continue
        out.append(
            CensusOut(label=label, source=source, output=output, intact=output >= source)
        )
    return out


# ── history ──────────────────────────────────────────────────────────────────


class VersionOut(BaseModel):
    number: int
    created_at: str
    note: str = ""
    changes: list[str] = Field(default_factory=list)
    is_original: bool = False
    is_current: bool = False

    @classmethod
    def of(cls, version: Version, *, current: bool) -> VersionOut:
        return cls(
            number=version.number, created_at=version.created_at, note=version.note,
            changes=list(version.changes), is_original=version.is_original,
            is_current=current,
        )


# ── the open document ────────────────────────────────────────────────────────


class DocumentOut(BaseModel):
    """Everything the workspace needs to render, in one response.

    Deliberately not split across four requests. The client's first paint is the
    moment the user decides whether this product is serious; four round trips
    means four chances to paint a partial deck.
    """

    id: str
    name: str
    workspace: str
    deck: DeckOut
    versions: list[VersionOut] = Field(default_factory=list)
    changeset: ChangeSetOut | None = None
    verification: VerificationOut | None = None

    @classmethod
    def of(cls, doc_id: str, session: Session) -> DocumentOut:
        return cls(
            id=doc_id,
            name=session.source.name,
            workspace=str(session.workspace),
            deck=DeckOut.of(session.deck()),
            versions=[
                VersionOut.of(v, current=v is session.current) for v in session.versions
            ],
            changeset=ChangeSetOut.of(session.changeset) if session.changeset else None,
            verification=(
                VerificationOut.of(session.last_report) if session.last_report else None
            ),
        )


# ── requests ─────────────────────────────────────────────────────────────────


class OpenRequest(BaseModel):
    path: str


class SetSpec(BaseModel):
    """A change the user typed. Origin USER, and therefore trusted without review."""

    slide: int
    target: str
    op: Literal[
        "set_text", "set_table_cell", "move", "resize",
        "set_font_size", "set_font", "set_color", "delete_shape",
    ] = "set_text"
    after: Any = None
    rationale: str = ""


class LockSpec(BaseModel):
    scope: str
    target: str = ""
    reason: str = ""


class ProposeRequest(BaseModel):
    instruction: str = ""
    sets: list[SetSpec] = Field(default_factory=list)
    locks: list[LockSpec] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    approve: list[str] = Field(default_factory=list)
    reject: list[str] = Field(default_factory=list)
    approve_all: bool = False
    include_unreviewed: bool = False


class ApplyRequest(BaseModel):
    note: str = ""


class RefreshRequest(BaseModel):
    """Paths to workbooks or CSVs already on this machine.

    Paths, not uploads, for exactly the reason the deck is a path: the numbers
    behind a board pack are as confidential as the pack (ADR-0008).
    """

    sources: list[str] = Field(default_factory=list)
    locks: list[LockSpec] = Field(default_factory=list)


class TidyRequest(BaseModel):
    """Locks apply to a tidy exactly as they do to any other proposal.

    Which is the point: "conform the typefaces but do not touch slide 4, the
    partner signed it off" is an ordinary request, and it is a constraint rather
    than a preference, so the engine enforces it.
    """

    locks: list[LockSpec] = Field(default_factory=list)


class RevertRequest(BaseModel):
    to: int = 0


class ExportRequest(BaseModel):
    destination: str
