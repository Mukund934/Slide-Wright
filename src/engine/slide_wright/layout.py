"""Near-miss alignment: fix the slips, never the decisions.

The most repeated complaint in the practitioner evidence is not a hard problem.
It is boxes that are *almost* lined up — three headers at 1.00in, 1.01in and
1.00in — and a person nudging them with arrow keys at midnight.

Automating that is easy to do and easy to do catastrophically, because the hard
part is not moving shapes. It is deciding which shapes were *meant* to line up.
Get that wrong and the tool rearranges a layout somebody chose on purpose, which
is exactly the behaviour that makes people stop trusting software with their
decks.

So the rule here is narrow and mechanical rather than clever:

> **A shape 2 inches out of line is a decision. A shape 0.01 inches out of line
> is a slip. Only slips are fixed.**

Edges are clustered, and a cluster only forms when its values already sit within
a tolerance of each other — near-misses, by construction. Everything follows
from that:

  · **Movement is bounded by the tolerance.** Nothing can move further than the
    distance that made it a near-miss in the first place. At the default of
    0.02in no correction is perceptible; the shape was already there.
  · **Deliberate offsets are invisible to this module.** A shape placed well
    away from a group never joins its cluster, so it is never touched.
  · **The majority wins.** A cluster snaps to the value most shapes already use,
    so the fewest possible shapes move.

This changes presentation and never content, so a run of it should produce zero
content deltas in `diff.py`. That is asserted, not assumed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from slide_wright.changeset import Change, ChangeSet, Op, Origin
from slide_wright.inspect import EMU_PER_INCH, DeckInfo, ShapeInfo

# 0.02in. Wide enough to catch the arrow-key nudges people actually leave
# behind, narrow enough that no correction is visible to the eye.
DEFAULT_TOLERANCE_EMU = 18288

# Below this, a difference is rounding rather than misalignment. Snapping it
# would churn the file for no visible gain.
NOISE_EMU = 254  # 1/3600 in


@dataclass
class Cluster:
    """Shapes whose edges nearly agree, and the value they should share."""

    edge: str                       # left | right | top | bottom | centre-x | centre-y
    target: int
    members: list[tuple[str, int]] = field(default_factory=list)  # (shape id, current)

    @property
    def strays(self) -> list[tuple[str, int]]:
        return [(sid, v) for sid, v in self.members if v != self.target]

    @property
    def worst_shift_emu(self) -> int:
        return max((abs(v - self.target) for _, v in self.strays), default=0)


@dataclass
class LayoutPlan:
    """What an alignment pass would change, before anything is written."""

    deck: str = ""
    tolerance_emu: int = DEFAULT_TOLERANCE_EMU
    clusters: list[Cluster] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.changes

    @property
    def worst_shift_emu(self) -> int:
        """The largest distance any shape would move. Must not exceed tolerance."""
        return max((c.worst_shift_emu for c in self.clusters), default=0)

    def to_changeset(self, deck_path: str) -> ChangeSet:
        changeset = ChangeSet(deck=deck_path, instruction="align near-miss edges")
        for change in self.changes:
            changeset.add(change)
        return changeset

    def render(self) -> str:
        lines = [f"ALIGNMENT PLAN — {self.deck}", ""]
        tol = self.tolerance_emu / EMU_PER_INCH
        if self.empty:
            lines.append(f"  Nothing within {tol:.3f}in of alignment is out of line.")
        else:
            lines.append(
                f"  {len(self.changes)} shape(s) to nudge across "
                f"{len(self.clusters)} near-miss group(s)"
            )
            lines.append(
                f"  largest movement {self.worst_shift_emu / EMU_PER_INCH:.4f}in "
                f"— tolerance is {tol:.3f}in"
            )
            lines.append("")
            for cluster in self.clusters[:12]:
                strays = ", ".join(
                    f"{sid} ({(v - cluster.target) / EMU_PER_INCH:+.4f}in)"
                    for sid, v in cluster.strays
                )
                lines.append(
                    f"    · {cluster.edge} edge at "
                    f"{cluster.target / EMU_PER_INCH:.3f}in — {strays}"
                )
            if len(self.clusters) > 12:
                lines.append(f"    · … {len(self.clusters) - 12} more group(s)")

        if self.skipped:
            lines += ["", "  Left alone"]
            for note in self.skipped[:6]:
                lines.append(f"    · {note}")
            if len(self.skipped) > 6:
                lines.append(f"    · … {len(self.skipped) - 6} more")

        lines += ["", "  Nothing moves further than the tolerance, and no text changes."]
        return "\n".join(lines)


def plan_alignment(
    deck: DeckInfo,
    tolerance_emu: int = DEFAULT_TOLERANCE_EMU,
    name: str = "",
) -> LayoutPlan:
    """Find edges that nearly agree, and snap them to the value most already use."""
    plan = LayoutPlan(deck=name or "deck", tolerance_emu=tolerance_emu)

    for slide in deck.slides:
        positioned = [s for s in slide.shapes if s.x is not None and s.y is not None]
        placed_by_layout = len(slide.shapes) - len(positioned)
        if placed_by_layout:
            plan.skipped.append(
                f"slide {slide.number}: {placed_by_layout} shape(s) positioned by "
                "the layout, which this must not override"
            )
        if len(positioned) < 2:
            continue

        # A shape whose edge is *exactly* flush with another's is anchored on
        # that axis and must not move, even to fix a near-miss on one of its
        # other edges. Moving it would trade a real alignment for a smaller
        # one, and the deck would be worse for it.
        #
        # This is also what makes the pass terminate. Without it, snapping a
        # left edge shifts the right edge out of a flush pair, the next pass
        # snaps it back, and the two alternate forever -- measured on a real
        # deck as a stable cycle of nine shapes moving back and forth. With it,
        # exact alignments can only be created and never destroyed, so repeated
        # runs converge.
        anchored = _anchored_axes(positioned)

        # Proposed new coordinates, accumulated across every edge kind so a
        # shape misaligned both horizontally and vertically moves once.
        moves: dict[str, dict[str, int]] = {}

        for edge, read, axis in (
            ("left", lambda s: s.x, "x"),
            ("right", lambda s: s.right, "x"),
            ("centre-x", lambda s: _centre_x(s), "x"),
            ("top", lambda s: s.y, "y"),
            ("bottom", lambda s: s.bottom, "y"),
            ("centre-y", lambda s: _centre_y(s), "y"),
        ):
            values = [(s.id, read(s)) for s in positioned if read(s) is not None]
            for cluster in _cluster(edge, values, tolerance_emu):
                plan.clusters.append(cluster)
                for shape_id, current in cluster.strays:
                    if axis in anchored.get(shape_id, ()):
                        continue
                    shift = cluster.target - current
                    # Left, centre and right are three constraints on one axis,
                    # not three corrections to apply in turn. Adding them
                    # compounds a movement each was individually happy with.
                    # The strongest consensus wins; ties go to the smaller move.
                    candidate = (len(cluster.members), -abs(shift), shift)
                    best = moves.setdefault(shape_id, {}).get(axis)
                    if best is None or candidate[:2] > best[:2]:
                        moves[shape_id][axis] = candidate

        held = sorted(sid for sid, axes in anchored.items() if axes)
        if held:
            plan.skipped.append(
                f"slide {slide.number}: {len(held)} shape(s) already exactly "
                "flush with another, so held still"
            )

        by_id = {s.id: s for s in positioned}
        for shape_id, shifts in sorted(moves.items()):
            shape = by_id[shape_id]
            new_x = shape.x + (shifts["x"][2] if "x" in shifts else 0)
            new_y = shape.y + (shifts["y"][2] if "y" in shifts else 0)
            if (new_x, new_y) == (shape.x, shape.y):
                continue
            shift_emu = max(abs(new_x - shape.x), abs(new_y - shape.y))
            if shift_emu > tolerance_emu:
                # Unreachable if the clustering is right. Kept because the bound
                # is the whole safety argument, and a silent violation of it is
                # exactly the failure that would destroy trust in this feature.
                plan.skipped.append(
                    f"slide {slide.number} shape {shape_id}: refused a "
                    f"{shift_emu / EMU_PER_INCH:.4f}in move exceeding the "
                    f"{tolerance_emu / EMU_PER_INCH:.3f}in tolerance"
                )
                continue
            plan.changes.append(Change(
                id=f"a{len(plan.changes) + 1}",
                op=Op.MOVE,
                slide=slide.number,
                target=shape_id,
                before=(shape.x, shape.y),
                after=(new_x, new_y),
                rationale=(
                    f"snapped to a near-miss edge "
                    f"({(new_x - shape.x) / EMU_PER_INCH:+.4f}, "
                    f"{(new_y - shape.y) / EMU_PER_INCH:+.4f})in"
                ),
                # A deterministic rule, not a model's opinion about design.
                origin=Origin.RULE,
                object_kind=shape.kind,
            ))
    return plan


def _anchored_axes(shapes: list[ShapeInfo]) -> dict[str, set[str]]:
    """Which axes each shape is already exactly aligned on, and so pinned.

    An edge value shared *exactly* by two or more shapes is a real alignment —
    almost certainly deliberate, and certainly not something to break in order
    to close a gap of a hundredth of an inch somewhere else.
    """
    axes: dict[str, set[str]] = {}
    for axis, readers in (
        ("x", (lambda s: s.x, lambda s: s.right, _centre_x)),
        ("y", (lambda s: s.y, lambda s: s.bottom, _centre_y)),
    ):
        for read in readers:
            seen: dict[int, list[str]] = {}
            for shape in shapes:
                value = read(shape)
                if value is not None:
                    seen.setdefault(value, []).append(shape.id)
            for ids in seen.values():
                if len(ids) > 1:
                    for shape_id in ids:
                        axes.setdefault(shape_id, set()).add(axis)
    return axes


def _cluster(edge: str, values: list[tuple[str, int]], tolerance: int) -> list[Cluster]:
    """Group edge values whose whole span sits within `tolerance`.

    The span is what is bounded, not the gap between neighbours. Single-linkage
    chaining -- extending a run whenever the *next* value is close enough --
    reads naturally and is wrong here: values at 0, 15 and 30 EMU each neighbour
    within a tolerance of 18 form a run spanning 30, and the shape at one end
    would move further than the tolerance that justified touching it at all.

    Measured on a real deck before this was fixed: a claimed bound of 0.02in
    produced a 0.031in move. The safety property is the entire argument for
    doing this automatically, so it is enforced here and asserted again after
    the plan is built.
    """
    if len(values) < 2:
        return []

    clusters: list[Cluster] = []
    ordered = sorted(values, key=lambda pair: pair[1])
    run: list[tuple[str, int]] = [ordered[0]]

    for item in ordered[1:]:
        # Compare against the run's *start*, so the span can never exceed the
        # tolerance however many shapes join.
        if item[1] - run[0][1] <= tolerance:
            run.append(item)
        else:
            clusters.extend(_finish(edge, run))
            run = [item]
    clusters.extend(_finish(edge, run))
    return clusters


def _finish(edge: str, run: list[tuple[str, int]]) -> list[Cluster]:
    """Turn one run of near-equal values into a cluster, if it needs correcting."""
    if len(run) < 2:
        return []
    distinct = {v for _, v in run}
    if len(distinct) < 2:
        return []                      # already flush
    if max(distinct) - min(distinct) <= NOISE_EMU:
        return []                      # rounding, not misalignment

    # Snap to the value the most shapes already use, so the fewest shapes move.
    counts = Counter(v for _, v in run)
    target, agreement = counts.most_common(1)[0]

    if agreement < 2:
        # No established line here, only shapes near each other at distinct
        # positions. Which of them is "right" is a design question, and
        # inventing an answer is how this pass turns destructive.
        #
        # It is also how it fails to terminate. Two shapes near each other on
        # two different edges produce two clusters that disagree about which is
        # the reference: the top cluster pulls one up to the other, the
        # centre-y cluster pulls the other up to the first, and they chase each
        # other down the slide forever. Measured on a real deck as a permanent
        # two-shape cycle with the target drifting every pass.
        #
        # Snapping only to a line at least two shapes *already* share fixes
        # both problems at once: every target is an existing position, so
        # corrections can only add exact alignments, never invent or chase one.
        return []

    return [Cluster(edge=edge, target=target, members=list(run))]


def _centre_x(shape: ShapeInfo) -> int | None:
    if shape.x is None or shape.cx is None:
        return None
    return shape.x + shape.cx // 2


def _centre_y(shape: ShapeInfo) -> int | None:
    if shape.y is None or shape.cy is None:
        return None
    return shape.y + shape.cy // 2
