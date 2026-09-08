"""The change report.

The product promise is "change what I asked, preserve everything else." The
promise is what the customer buys; this report is why they can believe it.

Every line is computed from part hashes and object counts. Nothing here is the
model's account of its own work — a system that asks you to trust its summary
has not solved the problem it claims to solve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from slide_wright.fidelity import FidelityReport


@dataclass
class RequestedChange:
    """One entry of the change set: something the user actually asked for."""

    slide: int
    description: str
    target: str = ""


# Parts that can execute. A macro project is the one that matters: nothing in
# this engine produces one, and no formatting pass acquires one, so its
# appearance means the output is not the document that went in.
#
# Matched on the OOXML part name rather than on content, because that is what
# PowerPoint itself dispatches on — a file renamed to look harmless is still
# loaded as a macro project if it sits at this path.
EXECUTABLE_PARTS = ("vbaproject.bin", "vbadata.xml")


def is_executable_part(name: str) -> bool:
    return name.rsplit("/", 1)[-1].lower() in EXECUTABLE_PARTS


@dataclass
class ChangeReport:
    """What changed, what did not, and whether anything unrequested moved."""

    fidelity: FidelityReport
    requested: list[RequestedChange] = field(default_factory=list)
    #: Guarantees the *output* breaks, whatever the change set said. Filled by
    #: `lock_violations`; see it for why this is checked here and not only where
    #: the changes were admitted.
    lock_breaks: list[str] = field(default_factory=list)

    # ── attribution ──────────────────────────────────────────────────────────

    @property
    def requested_slides(self) -> set[int]:
        return {c.slide for c in self.requested}

    @property
    def unrequested_slide_changes(self) -> list[int]:
        """Slides that changed without a change-set entry authorising it."""
        return sorted(set(self.fidelity.changed_slide_numbers) - self.requested_slides)

    @property
    def unrequested_part_changes(self) -> list[str]:
        """Non-slide parts that changed, and every part that appeared.

        Some changes are legitimate consequences of an edit (a chart's data
        workbook moves when its numbers do). They are surfaced rather than
        hidden, so a reviewer decides instead of the system deciding silently.

        Additions were missing from this list entirely, which meant a package
        could come back carrying parts nobody asked for and the report would
        mention none of them. "Preserve everything else" is a claim about what
        the output contains, not only about what it lost.
        """
        return [d.name for d in self.fidelity.changed if d.slide_owner is None] + [
            f"{d.name} (added)" for d in self.fidelity.added
        ]

    @property
    def executable_additions(self) -> list[str]:
        """Parts that appeared and can run code.

        A macro project is not a consequence of any edit this engine performs.
        Nothing in the in-place applier can produce one, and no legitimate
        formatting pass acquires one, so its appearance means the output is not
        the document that went in.

        This is deliberately narrow rather than "block every addition". A
        round-trip export can legitimately gain a part, and refusing all of them
        would make the gate fire on the ordinary case until someone learned to
        ignore it. Precision is what keeps a refusal meaningful.
        """
        return [d.name for d in self.fidelity.added if is_executable_part(d.name)]

    def explain_unrequested(self):
        """What actually changed on the slides nobody authorised.

        Computed lazily and only when it is needed. Naming the part is enough
        when everything was requested; when it was not, the reviewer is about
        to decide whether to ship a deck, and "slide4.xml changed" is not
        something anyone can decide on.

        Returns an empty list if the decks are no longer both readable -- an
        explanation is a courtesy, and failing to produce one must never turn
        a blocked report into an error.
        """
        if not self.unrequested_slide_changes:
            return []
        try:
            from slide_wright.diff import diff

            unrequested = set(self.unrequested_slide_changes)
            return [d for d in diff(self.fidelity.source, self.fidelity.output).deltas
                    if d.slide in unrequested]
        except Exception:
            return []

    def _render_requested(self, threshold: int = 8) -> list[str]:
        """List the changes, collapsing repetition rather than printing it.

        A conformance pass on a real deck produces a hundred identical-looking
        lines -- "set typeface Arial -> +mn-lt", once per run. Printing them
        all is not transparency; it is where a reviewer stops reading, and the
        one line that mattered is somewhere in the middle of it.

        So identical descriptions are grouped and counted, with the slides they
        touched named. A change that appears once is still printed in full,
        because that is the one worth looking at.
        """
        grouped: dict[str, list] = {}
        for change in self.requested:
            grouped.setdefault(change.description, []).append(change)

        lines = []
        for description, changes in grouped.items():
            slides = sorted({c.slide for c in changes if c.slide})
            if len(changes) == 1:
                c = changes[0]
                where = f"slide {c.slide}" if c.slide else "deck"
                target = f" · {c.target}" if c.target else ""
                lines.append(f"    · {where}{target} — {description}")
                continue

            where = ("slides " + ", ".join(str(n) for n in slides)) if slides else "deck"
            lines.append(f"    · {len(changes)}x on {where} — {description}")
            if len(changes) <= threshold:
                for c in changes:
                    lines.append(f"        {c.target}")
        return lines

    @property
    def deliverable(self) -> bool:
        """Whether this output may be handed to the user.

        Fails closed: an unattributed slide change, a removed part, native
        object loss, suspected rasterisation, a part that appeared and can run
        code, and a deck that reads in a different order all block delivery.
        """
        return (
            not self.lock_breaks
            and not self.unrequested_slide_changes
            and not self.fidelity.removed
            and not self.fidelity.native_losses
            and not self.fidelity.rasterisation_suspected
            and not self.executable_additions
            and not self.fidelity.slide_order_changed
        )

    @property
    def blocking_reasons(self) -> list[str]:
        reasons = []
        reasons.extend(self.lock_breaks)
        if self.unrequested_slide_changes:
            reasons.append(
                "unrequested changes on slide(s) "
                + ", ".join(str(n) for n in self.unrequested_slide_changes)
            )
        if self.fidelity.removed:
            reasons.append(f"{len(self.fidelity.removed)} part(s) removed from the package")
        if self.fidelity.native_losses:
            reasons.append("native object loss: " + "; ".join(self.fidelity.native_losses))
        if self.fidelity.rasterisation_suspected:
            reasons.append("content appears to have been rasterised")
        if self.executable_additions:
            reasons.append(
                "the output gained a part that can run code: "
                + ", ".join(self.executable_additions)
            )
        if self.fidelity.slide_order_changed:
            reasons.append("the deck presents its slides in a different order")
        return reasons

    # ── rendering ────────────────────────────────────────────────────────────

    def render(self) -> str:
        f = self.fidelity
        name = Path(f.source).name
        total = f.total_source_parts
        unchanged_slides = _slide_part_count(f) - len(f.changed_slide_numbers)

        lines = [
            f"CHANGE REPORT — {name}",
            "",
            f"  Requested    {len(self.requested)} change(s)",
            f"  Applied      {len(f.changed_slide_numbers)} slide(s) modified",
            f"  Unrequested  {len(self.unrequested_slide_changes)}",
            "",
            f"  {len(f.identical)} of {total} package parts are byte-for-byte identical "
            f"to the file you supplied ({f.fidelity_score:.2f}%)",
        ]

        if unchanged_slides > 0:
            lines.append(f"  {unchanged_slides} slide(s) untouched")

        if self.requested:
            lines += ["", "  Requested changes"]
            lines += self._render_requested()

        if self.unrequested_slide_changes:
            lines += ["", "  Changes nobody asked for"]
            explained = self.explain_unrequested()
            for delta in explained[:12]:
                lines.append(f"    · slide {delta.slide} — {delta.description}")
            if len(explained) > 12:
                lines.append(f"    · … {len(explained) - 12} more")
            if not explained:
                # The diff could not account for it, which is itself worth
                # saying: something changed below the level this can describe.
                for n in self.unrequested_slide_changes:
                    lines.append(f"    · slide {n} changed, with no structural "
                                 f"difference this can name")

        if self.unrequested_part_changes:
            lines += ["", "  Other parts changed (review)"]
            for n in self.unrequested_part_changes:
                lines.append(f"    · {n}")

        s, o = f.source_census, f.output_census
        lines += [
            "",
            "  Integrity                source -> output",
            f"    native tables          {s.tables:>5} -> {o.tables}   {_tick(o.tables >= s.tables)}",
            f"    chart parts            {s.chart_parts:>5} -> {o.chart_parts}   {_tick(o.chart_parts >= s.chart_parts)}",
            f"    SmartArt diagrams      {s.diagram_parts:>5} -> {o.diagram_parts}   {_tick(o.diagram_parts >= s.diagram_parts)}",
            f"    embedded workbooks     {s.embeddings:>5} -> {o.embeddings}   {_tick(o.embeddings >= s.embeddings)}",
            f"    media                  {s.media_parts:>5} -> {o.media_parts}   {_tick(o.media_parts >= s.media_parts)}",
            f"    editable text runs     {s.text_runs:>5} -> {o.text_runs}",
        ]

        lines += [""]
        if self.deliverable:
            lines.append("  VERIFIED — every change is accounted for.")
        else:
            lines.append("  BLOCKED — this deck was not delivered:")
            for reason in self.blocking_reasons:
                lines.append(f"    · {reason}")

        return "\n".join(lines)


def _tick(ok: bool) -> str:
    return "ok" if ok else "LOSS"


def _slide_part_count(f: FidelityReport) -> int:
    # Bodies, not owners. This counts slides, and a slide's rels are not a
    # second slide -- attributing them that way is right for blame and wrong
    # for arithmetic.
    return len([d for d in f.deltas if d.slide_number is not None and d.status != "added"])


#: Which delta kinds each scope forbids in the output. `slide`, `shape` and the
#: three exhibit scopes are matched by location instead, below.
_FORBIDDEN_KINDS = {
    "wording": {"text", "table", "added", "removed"},
    "layout": {"geometry", "size"},
    "formatting": {"formatting"},
}


def lock_violations(source, output, locks) -> list[str]:
    """Guarantees the written deck breaks, read off the deck rather than the plan.

    A lock is normally enforced where changes are admitted: the change arrives,
    `ChangeSet.add` refuses it, the status reads REJECTED. That proves the gate
    works and says nothing about the file that comes out, and the two came apart
    in practice -- `formatting` permits text edits *by design*, and an applier
    that stripped run styling while making one left the lock declared, honoured,
    reported honoured, and worthless.

    So the same question is asked of the output. Every held lock is turned into
    a property the two decks must share, and the semantic diff already computes
    every one of them. A violation blocks delivery, which is what the README has
    claimed since it was written and what nothing was doing.

    Returns sentences, because they are read by whoever is deciding not to send
    the deck.
    """
    if not locks:
        return []

    from slide_wright.diff import diff
    from slide_wright.inspect import inspect

    comparison = diff(source, output)
    if not comparison.deltas:
        return []

    kinds = {
        (sl.number, sh.id): sh.kind
        for sl in inspect(source).slides
        for sh in sl.shapes
    }
    exhibit = {"tables": "table", "charts": "chart", "media": "picture"}

    broken: list[str] = []
    for lock in locks:
        for delta in comparison.deltas:
            if not _breaks(lock, delta, kinds, exhibit):
                continue
            held = lock.scope + (f":{lock.target}" if lock.target else "")
            broken.append(f"the {held} lock was held, and {delta.description}")
            break          # one sentence per lock; the diff carries the rest
    return broken


def _breaks(lock, delta, kinds, exhibit) -> bool:
    if lock.scope == "slide":
        return not lock.target or str(delta.slide) == str(lock.target)
    if lock.scope == "shape":
        return delta.shape_id == lock.target
    if lock.scope == "numbers":
        return delta.changes_figures
    if lock.scope in exhibit:
        return kinds.get((delta.slide, delta.shape_id)) == exhibit[lock.scope]
    return delta.kind in _FORBIDDEN_KINDS.get(lock.scope, set())


def build(
    fidelity: FidelityReport,
    requested: list[RequestedChange] | None = None,
    lock_breaks: list[str] | None = None,
) -> ChangeReport:
    return ChangeReport(
        fidelity=fidelity, requested=requested or [], lock_breaks=lock_breaks or []
    )
