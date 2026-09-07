/**
 * The review panel, tested for the distinctions it must not blur.
 *
 * A change set is only useful if grounded and invented changes look different,
 * and if a change a lock refused reads as refused rather than as broken. Those
 * are the two ways this screen can quietly become an "approve all" button with
 * extra rows.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Change, ChangeSet } from "../api/types";
import { ChangeSetPanel } from "./ChangeSetPanel";

function change(over: Partial<Change> = {}): Change {
  return {
    id: "c1", op: "set_text", slide: 12, target: "5", before: "old", after: "new",
    rationale: "", status: "proposed", origin: "user", citation: "",
    confidence: 1, impact: "", object_kind: "shape",
    description: "set text 'old' -> 'new'",
    is_grounded: true, needs_review: false, ...over,
  };
}

function changeset(changes: Change[], locks: ChangeSet["locks"] = []): ChangeSet {
  return {
    deck: "d.pptx", instruction: "", changes, locks,
    proposed_count: changes.filter((c) => c.status === "proposed").length,
    approved_count: changes.filter((c) => c.status === "approved").length,
    rejected_count: changes.filter((c) => c.status === "rejected").length,
    applied_count: changes.filter((c) => c.status === "applied").length,
    needs_review_count: changes.filter((c) => c.needs_review).length,
  };
}

const handlers = {
  busy: false,
  onGoTo: vi.fn(),
  onApprove: vi.fn(),
  onReject: vi.fn(),
  onApproveAll: vi.fn(),
};

describe("the empty state", () => {
  it("says what will happen rather than that there is nothing", () => {
    render(<ChangeSetPanel {...handlers} changeset={null} />);
    expect(screen.getByText(/before it touches the file/)).toBeInTheDocument();
  });

  it("does not say 'nothing proposed' to someone who just applied something", () => {
    // A change set is closed the moment it is applied — every `before` in it
    // was read from the version it described. So this panel empties on a
    // successful apply, and the copy written for an untouched deck then reads
    // as though the work had not happened.
    render(<ChangeSetPanel {...handlers} changeset={null} applied />);
    expect(screen.queryByText(/Nothing proposed/)).not.toBeInTheDocument();
    expect(screen.getByText(/described the version you edited/)).toBeInTheDocument();
  });
});

describe("provenance", () => {
  it("marks a model's uncited proposal as needing review", () => {
    render(
      <ChangeSetPanel
        {...handlers}
        changeset={changeset([
          change({ origin: "model", confidence: 0.6, is_grounded: false, needs_review: true }),
        ])}
      />,
    );
    expect(screen.getByText("needs review")).toBeInTheDocument();
    expect(screen.getByText(/model · 60%/)).toBeInTheDocument();
  });

  it("shows a citation instead, when the change traces to a cell", () => {
    render(
      <ChangeSetPanel
        {...handlers}
        changeset={changeset([change({ origin: "source", citation: "comps.csv!B2" })])}
      />,
    );
    expect(screen.getByText("comps.csv!B2")).toBeInTheDocument();
    expect(screen.queryByText("needs review")).not.toBeInTheDocument();
  });

  it("says nothing extra about a change the user typed", () => {
    render(<ChangeSetPanel {...handlers} changeset={changeset([change()])} />);
    expect(screen.queryByText("needs review")).not.toBeInTheDocument();
    expect(screen.queryByText(/model/)).not.toBeInTheDocument();
  });
});

describe("locks", () => {
  it("names what is protected", () => {
    render(
      <ChangeSetPanel
        {...handlers}
        changeset={changeset([change()], [
          { scope: "numbers", target: "", reason: "the partner signed these off" },
        ])}
      />,
    );
    expect(screen.getByText("Protected")).toBeInTheDocument();
    expect(screen.getByText("numbers")).toBeInTheDocument();
  });

  it("shows a refused change as rejected, with the lock's reason", () => {
    render(
      <ChangeSetPanel
        {...handlers}
        changeset={changeset([
          change({
            status: "rejected",
            rationale: "blocked by numbers lock (the partner signed these off)",
          }),
        ])}
      />,
    );
    expect(screen.getByText("rejected")).toBeInTheDocument();
    expect(screen.getByText(/blocked by numbers lock/)).toBeInTheDocument();
  });
});

describe("approving in bulk", () => {
  it("offers the grounded ones separately from the invented ones", () => {
    render(
      <ChangeSetPanel
        {...handlers}
        changeset={changeset([
          change({ id: "a" }),
          change({ id: "b" }),
          change({ id: "c", origin: "model", needs_review: true, is_grounded: false }),
        ])}
      />,
    );
    expect(screen.getByRole("button", { name: /Approve 2 grounded/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Include 1 unreviewed/ })).toBeInTheDocument();
  });

  it("passes include-unreviewed only when that button is the one pressed", async () => {
    const onApproveAll = vi.fn();
    render(
      <ChangeSetPanel
        {...handlers}
        onApproveAll={onApproveAll}
        changeset={changeset([
          change({ id: "a" }),
          change({ id: "c", origin: "model", needs_review: true, is_grounded: false }),
        ])}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Approve 1 grounded/ }));
    expect(onApproveAll).toHaveBeenLastCalledWith(false);

    await userEvent.click(screen.getByRole("button", { name: /Include 1 unreviewed/ }));
    expect(onApproveAll).toHaveBeenLastCalledWith(true);
  });

  it("disables the grounded button when every pending change needs review", () => {
    render(
      <ChangeSetPanel
        {...handlers}
        changeset={changeset([
          change({ id: "c", origin: "model", needs_review: true, is_grounded: false }),
        ])}
      />,
    );
    expect(screen.getByRole("button", { name: /Approve 0 grounded/ })).toBeDisabled();
  });
});

describe("deciding one at a time", () => {
  it("approves and rejects by id", async () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(
      <ChangeSetPanel
        {...handlers}
        onApprove={onApprove}
        onReject={onReject}
        changeset={changeset([change({ id: "c7" })])}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApprove).toHaveBeenCalledWith(["c7"]);
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onReject).toHaveBeenCalledWith(["c7"]);
  });

  it("offers no decision on a change that is already decided", () => {
    render(
      <ChangeSetPanel {...handlers} changeset={changeset([change({ status: "applied" })])} />,
    );
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByText("applied")).toBeInTheDocument();
  });

  it("jumps to the object a change addresses", async () => {
    const onGoTo = vi.fn();
    const target = change({ slide: 12 });
    render(<ChangeSetPanel {...handlers} onGoTo={onGoTo} changeset={changeset([target])} />);
    await userEvent.click(screen.getByRole("button", { name: /Go to slide 12/ }));
    expect(onGoTo).toHaveBeenCalledWith(target);
  });
});

describe("the tally", () => {
  it("counts toward what can still be decided, not the whole list", () => {
    // A change a lock refused is not work the reviewer is behind on.
    render(
      <ChangeSetPanel
        {...handlers}
        changeset={changeset([
          change({ id: "a", status: "approved" }),
          change({ id: "b", status: "rejected" }),
        ])}
      />,
    );
    expect(screen.getByText(/1\/1 approved · 1 blocked/)).toBeInTheDocument();
  });

  it("reports what was applied once anything has been", () => {
    render(
      <ChangeSetPanel
        {...handlers}
        changeset={changeset([
          change({ id: "a", status: "applied" }),
          change({ id: "b", status: "rejected" }),
        ])}
      />,
    );
    expect(screen.getByText(/1 applied · 1 blocked/)).toBeInTheDocument();
  });
});

describe("while an apply is running", () => {
  it("takes no further decisions", () => {
    render(<ChangeSetPanel {...handlers} busy changeset={changeset([change()])} />);
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });
});


describe("identical changes are collapsed", () => {
  /**
   * A tidy on a real deck proposes 272 corrections and 266 of them say the same
   * sentence. One row each is not a review surface, it is a wall — and a
   * reviewer facing a wall presses Approve all, which is the one decision this
   * panel exists to make them stop and take.
   *
   * `report.py` learned this first, for the CLI: printing every line "is not
   * transparency; it is where a reviewer stops reading, and the one line that
   * mattered is somewhere in the middle of it."
   */
  const many = (count: number, over: Partial<Change> = {}) =>
    Array.from({ length: count }, (_, i) =>
      change({ id: `c${i}`, slide: (i % 3) + 1, description: "set typeface A -> B", ...over }),
    );

  it("shows one row with a count rather than a wall", async () => {
    render(<ChangeSetPanel {...handlers} changeset={changeset(many(266))} />);
    expect(screen.getByText("266×")).toBeInTheDocument();
    expect(screen.getAllByText("set typeface A -> B")).toHaveLength(1);
  });

  it("says which slides it lands on", async () => {
    render(<ChangeSetPanel {...handlers} changeset={changeset(many(266))} />);
    expect(screen.getByText(/slides 1, 2, 3/)).toBeInTheDocument();
  });

  it("approves every change it stands for, and says how many first", async () => {
    const onApprove = vi.fn();
    render(
      <ChangeSetPanel {...handlers} onApprove={onApprove} changeset={changeset(many(4))} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Approve all 4" }));
    expect(onApprove).toHaveBeenCalledWith(["c0", "c1", "c2", "c3"]);
  });

  it("still lets a reviewer open the list and check one", async () => {
    render(<ChangeSetPanel {...handlers} changeset={changeset(many(4))} />);
    await userEvent.click(screen.getByRole("button", { name: "Show all 4" }));
    expect(screen.getAllByText("set typeface A -> B")).toHaveLength(5); // 1 summary + 4
  });

  it("never merges a model's uncited proposal into a rule's pass", async () => {
    // Same sentence, different provenance. Collapsing these together would let
    // one click approve something that was never grounded in anything.
    const mixed = [
      ...many(2, { origin: "rule", needs_review: false }),
      change({ id: "m1", description: "set typeface A -> B", origin: "model",
               is_grounded: false, needs_review: true }),
    ];
    render(<ChangeSetPanel {...handlers} changeset={changeset(mixed)} />);
    expect(screen.getByText("2×")).toBeInTheDocument();
    expect(screen.queryByText("3×")).not.toBeInTheDocument();
  });

  it("leaves a change that stands alone exactly as it was", async () => {
    render(<ChangeSetPanel {...handlers} changeset={changeset([change({ id: "c1" })])} />);
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.queryByText(/Approve all/)).not.toBeInTheDocument();
  });
});
