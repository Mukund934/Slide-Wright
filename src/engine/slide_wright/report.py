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


@dataclass
class ChangeReport:
    """What changed, what did not, and whether anything unrequested moved."""

    fidelity: FidelityReport
    requested: list[RequestedChange] = field(default_factory=list)

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
        """Non-slide parts that changed.

        Some are legitimate consequences of an edit (a chart's data workbook
        moves when its numbers do). They are surfaced rather than hidden, so a
        reviewer decides instead of the system deciding silently.
        """
        return [d.name for d in self.fidelity.changed if d.slide_number is None]

    @property
    def deliverable(self) -> bool:
        """Whether this output may be handed to the user.

        Fails closed: an unattributed slide change, a removed part, native
        object loss, or suspected rasterisation all block delivery.
        """
        return (
            not self.unrequested_slide_changes
            and not self.fidelity.removed
            and not self.fidelity.native_losses
            and not self.fidelity.rasterisation_suspected
        )

    @property
    def blocking_reasons(self) -> list[str]:
        reasons = []
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
            for c in self.requested:
                where = f"slide {c.slide}" if c.slide else "deck"
                target = f" · {c.target}" if c.target else ""
                lines.append(f"    · {where}{target} — {c.description}")

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
    return len([d for d in f.deltas if d.slide_number is not None and d.status != "added"])


def build(fidelity: FidelityReport, requested: list[RequestedChange] | None = None) -> ChangeReport:
    return ChangeReport(fidelity=fidelity, requested=requested or [])
