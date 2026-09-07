/**
 * The state machine, tested for what it must never allow.
 *
 * These are not "does the reducer set the field" tests. The reducer exists
 * because the phases of this product are a sequence, and the safety argument is
 * that certain states are unreachable — applying with nothing approved, a
 * rejected change counted as pending, a verdict surviving a revert. Each of
 * those is a way a deck gets changed without consent, so each gets a test.
 */

import { describe, expect, it } from "vitest";

import type { Change, ChangeSet, SlideDocument, Verification } from "../api/types";
import { reducer, type WorkspaceState } from "./workspace";

const initial: WorkspaceState = {
  phase: "empty",
  document: null,
  changeset: null,
  verification: null,
  progress: [],
  selectedSlide: 1,
  selectedShape: null,
  carriedShape: null,
  comparison: null,
  locks: [],
  error: null,
};

function change(over: Partial<Change> = {}): Change {
  return {
    id: "c1", op: "set_text", slide: 3, target: "5", before: "a", after: "b",
    rationale: "", status: "proposed", origin: "user", citation: "",
    confidence: 1, impact: "", object_kind: "shape", description: "set text",
    is_grounded: true, needs_review: false, ...over,
  };
}

function changeset(changes: Change[]): ChangeSet {
  return {
    deck: "d.pptx", instruction: "", changes, locks: [],
    proposed_count: changes.filter((c) => c.status === "proposed").length,
    approved_count: changes.filter((c) => c.status === "approved").length,
    rejected_count: changes.filter((c) => c.status === "rejected").length,
    applied_count: changes.filter((c) => c.status === "applied").length,
    needs_review_count: changes.filter((c) => c.needs_review).length,
  };
}

function document(over: Partial<SlideDocument> = {}): SlideDocument {
  return {
    id: "doc", name: "d.pptx", workspace: "/ws",
    deck: {
      slide_width: 12192000, slide_height: 6858000, theme_fonts: {},
      slides: [
        { number: 1, part_name: "s1.xml", layout: null, title: "One", word_count: 3, shapes: [] },
        { number: 3, part_name: "s3.xml", layout: null, title: "Three", word_count: 9, shapes: [] },
      ],
    },
    versions: [
      { number: 0, created_at: "t0", note: "original", changes: [], is_original: true, is_current: true },
    ],
    changeset: null, verification: null, ...over,
  };
}

function verification(over: Partial<Verification> = {}): Verification {
  return {
    deliverable: true, identical_parts: 104, total_parts: 105, fidelity_score: 99.05,
    changed_parts: ["ppt/slides/slide3.xml"], changed_slides: [3], untouched_slides: 17,
    unrequested_slides: [], unrequested_parts: [], blocking_reasons: [],
    requested: [], census: [], rendered: "", ...over,
  };
}

describe("opening", () => {
  it("selects the deck's first slide, not slide 1 by assumption", () => {
    const doc = document({
      deck: { ...document().deck, slides: [
        { number: 7, part_name: "s7.xml", layout: null, title: "Seven", word_count: 1, shapes: [] },
      ] },
    });
    expect(reducer(initial, { type: "opened", document: doc }).selectedSlide).toBe(7);
  });

  it("adopts a change set the engine already had open", () => {
    // Reopening a deck after a restart must not silently discard work in
    // progress: the engine still holds it, and a client that ignored it would
    // let the user propose a second set over the first.
    const existing = changeset([change({ status: "approved" })]);
    const state = reducer(initial, { type: "opened", document: document({ changeset: existing }) });
    expect(state.changeset).toBe(existing);
  });
});

describe("nothing is applied without approval", () => {
  it("a proposal alone does not make the set applicable", () => {
    const state = reducer(
      { ...initial, phase: "ready", document: document() },
      { type: "proposed", changeset: changeset([change()]) },
    );
    expect(state.phase).toBe("reviewing");
    expect(state.changeset?.approved_count).toBe(0);
  });

  it("a rejected change is never counted as pending", () => {
    const state = reducer(
      { ...initial, document: document() },
      { type: "reviewed", changeset: changeset([change({ status: "rejected" })]) },
    );
    expect(state.changeset?.proposed_count).toBe(0);
    expect(state.changeset?.approved_count).toBe(0);
  });
});

describe("applying", () => {
  it("clears the previous run's stages before the new one starts", () => {
    const stale = { ...initial, progress: [{ stage: "verified" as const, detail: "old" }] };
    expect(reducer(stale, { type: "applying" }).progress).toEqual([]);
  });

  it("keeps stages in the order the engine reported them", () => {
    let state = reducer({ ...initial, document: document() }, { type: "applying" });
    for (const stage of ["applying", "applied", "verifying", "verified"] as const) {
      state = reducer(state, { type: "stage", progress: { stage, detail: "" } });
    }
    expect(state.progress.map((p) => p.stage)).toEqual([
      "applying", "applied", "verifying", "verified",
    ]);
  });

  it("carries the view to the first slide that actually changed", () => {
    const state = reducer(
      { ...initial, phase: "applying", selectedSlide: 1, document: document() },
      { type: "settled", verification: verification({ changed_slides: [3, 9] }), document: document() },
    );
    expect(state.selectedSlide).toBe(3);
  });

  it("leaves the view alone when nothing changed", () => {
    const state = reducer(
      { ...initial, phase: "applying", selectedSlide: 5, document: document() },
      { type: "settled", verification: verification({ changed_slides: [] }), document: document() },
    );
    expect(state.selectedSlide).toBe(5);
  });

  it("records a blocked verdict rather than discarding it", () => {
    // The blocked result is the most important thing the product ever says.
    // Treating it as a failed request would drop it on the floor.
    const blocked = verification({
      deliverable: false,
      blocking_reasons: ["unrequested changes on slide(s) 4"],
      unrequested_slides: [4],
    });
    const state = reducer(
      { ...initial, phase: "applying", document: document() },
      { type: "settled", verification: blocked, document: document() },
    );
    expect(state.phase).toBe("settled");
    expect(state.verification?.deliverable).toBe(false);
    expect(state.error).toBeNull();
  });
});

describe("reverting", () => {
  it("clears the verdict and the change set", () => {
    // A verification describes one comparison. Leaving it on screen after the
    // document moved underneath it would attach a "verified" badge to a state
    // nothing verified.
    const state = reducer(
      {
        ...initial,
        phase: "settled",
        document: document(),
        changeset: changeset([change({ status: "applied" })]),
        verification: verification(),
        progress: [{ stage: "verified", detail: "" }],
      },
      { type: "reverted", document: document() },
    );
    expect(state.verification).toBeNull();
    expect(state.changeset).toBeNull();
    expect(state.progress).toEqual([]);
    expect(state.phase).toBe("ready");
  });
});

describe("failure", () => {
  it("returns to review when there is still a change set to fix", () => {
    const state = reducer(
      { ...initial, phase: "applying", document: document(), changeset: changeset([change()]) },
      { type: "failed", message: "apply refused" },
    );
    expect(state.phase).toBe("reviewing");
    expect(state.changeset).not.toBeNull();
    expect(state.error).toBe("apply refused");
  });

  it("returns to the open document when there is not", () => {
    const state = reducer(
      { ...initial, phase: "proposing", document: document() },
      { type: "failed", message: "nothing to propose" },
    );
    expect(state.phase).toBe("ready");
  });

  it("returns to the first-run screen when nothing is open", () => {
    const state = reducer(
      { ...initial, phase: "opening" },
      { type: "failed", message: "no file at /nope" },
    );
    expect(state.phase).toBe("empty");
    expect(state.error).toBe("no file at /nope");
  });
});

describe("selection", () => {
  it("carries the eye only when asked to", () => {
    const carried = reducer(initial, { type: "select", slide: 3, shape: "5", carry: true });
    expect(carried.carriedShape).toBe("5");
    const plain = reducer(initial, { type: "select", slide: 3, shape: "5" });
    expect(plain.carriedShape).toBeNull();
  });

  it("keeps the selected object when only the slide is named", () => {
    const state = reducer(
      { ...initial, selectedShape: "9" },
      { type: "select", slide: 4 },
    );
    expect(state.selectedShape).toBe("9");
  });

  it("clears the selected object when told to explicitly", () => {
    const state = reducer(
      { ...initial, selectedShape: "9" },
      { type: "select", slide: 4, shape: null },
    );
    expect(state.selectedShape).toBeNull();
  });
});

describe("comparing two versions", () => {
  const before = document().deck;
  const after = document().deck;
  const deltas = [
    { slide: 3, shape_id: "5", kind: "formatting", description: "font changed", is_content: false },
    { slide: 9, shape_id: "7", kind: "text", description: "9.4 -> 11.8", is_content: true },
  ];
  const comparison = { from: 0, to: 1, before, after, deltas, showing: "after" as const };

  it("lands on the first slide that actually differs", () => {
    // Opening a comparison on a slide where nothing changed makes the feature
    // look broken the first time anyone uses it.
    const state = reducer(
      { ...initial, document: document(), selectedSlide: 1 },
      { type: "comparing", comparison },
    );
    expect(state.selectedSlide).toBe(3);
  });

  it("stays put when the two versions read the same", () => {
    const state = reducer(
      { ...initial, document: document(), selectedSlide: 5 },
      { type: "comparing", comparison: { ...comparison, deltas: [] } },
    );
    expect(state.selectedSlide).toBe(5);
  });

  it("flips between the two sides", () => {
    const showing = reducer(
      { ...initial, comparison },
      { type: "flip" },
    ).comparison?.showing;
    expect(showing).toBe("before");
  });

  it("flipping with nothing to compare is a no-op, not a crash", () => {
    expect(reducer(initial, { type: "flip" })).toBe(initial);
  });

  it("is discarded when an apply moves the document underneath it", () => {
    // A comparison describes two specific versions. After an apply the document
    // has moved, so the one on screen no longer describes it.
    const state = reducer(
      { ...initial, phase: "applying", document: document(), comparison },
      { type: "settled", verification: verification(), document: document() },
    );
    expect(state.comparison).toBeNull();
  });

  it("is discarded on a revert too", () => {
    const state = reducer(
      { ...initial, document: document(), comparison },
      { type: "reverted", document: document() },
    );
    expect(state.comparison).toBeNull();
  });

  it("can be closed without touching anything else", () => {
    const state = reducer(
      { ...initial, document: document(), selectedSlide: 3, comparison },
      { type: "stopComparing" },
    );
    expect(state.comparison).toBeNull();
    expect(state.selectedSlide).toBe(3);
    expect(state.document).not.toBeNull();
  });
});

describe("the canvas follows the selection", () => {
  /**
   * The invariant a broken slide swap violated in the worst possible way: the
   * filmstrip reached slide 10 while the canvas went on showing slide 1, and
   * stayed there. On the surface whose whole job is proving what did and did
   * not change, a reviewer would have been checking against the wrong slide.
   *
   * The cause was an animation, which no reducer test can reach. What is
   * testable is the rule underneath: the slide on screen is always the slide
   * that is selected, from whichever deck is in view.
   */
  const deck = {
    slide_width: 12192000,
    slide_height: 6858000,
    theme_fonts: {},
    slides: [1, 4, 10].map((number) => ({
      number,
      part_name: `s${number}.xml`,
      layout: null,
      title: `Slide ${number}`,
      word_count: 1,
      shapes: [],
    })),
  };

  const shown = (state: WorkspaceState) => {
    const source = state.comparison ? state.comparison[state.comparison.showing] : deck;
    return source.slides.find((s) => s.number === state.selectedSlide)?.number ?? null;
  };

  it("shows whichever slide was selected, not the one before it", () => {
    let state = reducer(initial, { type: "opened", document: document({ deck }) });
    expect(shown(state)).toBe(1);

    state = reducer(state, { type: "select", slide: 10 });
    expect(shown(state)).toBe(10);

    state = reducer(state, { type: "select", slide: 4 });
    expect(shown(state)).toBe(4);
  });

  it("keeps up when selections arrive faster than anything could animate", () => {
    let state = reducer(initial, { type: "opened", document: document({ deck }) });
    for (const slide of [4, 10, 1, 10, 4]) {
      state = reducer(state, { type: "select", slide });
    }
    expect(state.selectedSlide).toBe(4);
    expect(shown(state)).toBe(4);
  });
});

describe("protection stands across proposals", () => {
  /**
   * A guarantee that had to be re-declared on every request would be forgotten
   * on the one that mattered, so locks are session state. The failure this
   * guards against is worse than the feature being absent: the interface showed
   * an object protected while the lock never reached the engine, because two of
   * three proposing callbacks read the locks without listing them as a
   * dependency and `useCallback` handed back an empty closure.
   */
  it("holds a lock until it is lifted", () => {
    let state = reducer(initial, { type: "lock", lock: { scope: "shape", target: "7" } });
    expect(state.locks).toEqual([{ scope: "shape", target: "7" }]);

    state = reducer(state, { type: "lock", lock: { scope: "numbers" } });
    expect(state.locks).toHaveLength(2);

    state = reducer(state, { type: "unlock", lock: { scope: "shape", target: "7" } });
    expect(state.locks).toEqual([{ scope: "numbers" }]);
  });

  it("locking the same thing twice is a slip, not two guarantees", () => {
    const once = reducer(initial, { type: "lock", lock: { scope: "slide", target: "4" } });
    const twice = reducer(once, { type: "lock", lock: { scope: "slide", target: "4" } });
    expect(twice.locks).toHaveLength(1);
    expect(twice).toBe(once);
  });

  it("tells apart the same scope on different targets", () => {
    let state = reducer(initial, { type: "lock", lock: { scope: "shape", target: "7" } });
    state = reducer(state, { type: "lock", lock: { scope: "shape", target: "9" } });
    state = reducer(state, { type: "unlock", lock: { scope: "shape", target: "7" } });
    expect(state.locks).toEqual([{ scope: "shape", target: "9" }]);
  });

  it("is cleared by opening a deck, because ids do not carry across", () => {
    // A shape lock names an id and a slide lock names a number. Both mean
    // something different in a different deck, so carrying them over would
    // silently protect the wrong objects — the worst possible outcome for a
    // guarantee, because it would still look like it was working.
    const held = reducer(initial, { type: "lock", lock: { scope: "shape", target: "7" } });
    expect(reducer(held, { type: "opened", document: document() }).locks).toEqual([]);
  });

  it("survives everything that moves the document within one deck", () => {
    // Applying and reverting replace large parts of the state. A lock lost to
    // either is a guarantee silently withdrawn while the deck stays open.
    let state = reducer(initial, { type: "opened", document: document() });
    state = reducer(state, { type: "lock", lock: { scope: "numbers" } });
    state = reducer(state, {
      type: "settled",
      verification: verification(),
      document: document(),
    });
    expect(state.locks, "an apply must not clear them").toEqual([{ scope: "numbers" }]);

    state = reducer(state, { type: "reverted", document: document() });
    expect(state.locks, "a revert must not clear them").toEqual([{ scope: "numbers" }]);
  });
});
