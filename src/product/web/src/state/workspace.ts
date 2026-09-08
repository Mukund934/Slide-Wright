/**
 * Workspace state: one open deck, and what the user is doing to it.
 *
 * A reducer rather than scattered `useState`, for one reason that matters here:
 * the states of this product are a sequence, not a set. Idle → proposed →
 * reviewing → applying → verified-or-blocked. Expressing that as six booleans
 * makes "applying while nothing is approved" representable, and the whole
 * safety argument rests on it not being.
 */

import { useCallback, useMemo, useReducer, useRef } from "react";

import { ApiError, api, applyStreaming } from "../api/client";
import type {
  ApplyProgress,
  Change,
  ChangeSet,
  Deck,
  Delta,
  LockSpec,
  SetSpec,
  SlideDocument,
  Verification,
} from "../api/types";

/**
 * The document this browser had open, so a reload returns to it.
 *
 * The id and nothing else. It is a SHA-256 prefix of the deck's resolved path,
 * which the workspace derives *because* of this -- its own docstring says the
 * id is "derived from the resolved path rather than handed out in sequence, so
 * a client that reloads reconnects to its own document". The server was built
 * for a reconnect the client never attempted, and refreshing the page sent the
 * user back to an empty box to retype an absolute path.
 *
 * The id is stored and the path is not, deliberately. A deck's name is the one
 * thing about it that is confidential without opening it, and this product's
 * whole promise is about what it does not leave lying around. An opaque digest
 * says which document without saying which file.
 *
 * Every access is guarded: storage throws in a private window, and a reload
 * that cannot be resumed is not a reason to fail to start.
 */
const REMEMBERED = "slide-wright.document";

function remembered(): string | null {
  try {
    return window.localStorage.getItem(REMEMBERED);
  } catch {
    return null;
  }
}

function remember(id: string): void {
  try {
    window.localStorage.setItem(REMEMBERED, id);
  } catch {
    // Nothing to do and nothing worth saying: the session still works, it just
    // will not survive a reload.
  }
}

function forget(): void {
  try {
    window.localStorage.removeItem(REMEMBERED);
  } catch {
    // As above.
  }
}

export type Phase =
  | "empty"
  | "opening"
  | "ready"
  | "proposing"
  | "reviewing"
  | "applying"
  | "settled";

/**
 * Two versions held side by side, and the differences between them.
 *
 * `before` and `after` are both read from the engine rather than one being
 * derived from the other plus the diff. Deriving would make the canvas a second
 * implementation of the comparison, and the two would eventually disagree.
 */
export interface Comparison {
  from: number;
  to: number;
  before: Deck;
  after: Deck;
  deltas: Delta[];
  /**
   * Slides that appeared or vanished between the two versions.
   *
   * The engine has always reported these and the client dropped them on the
   * way in, so a slide going missing between two versions showed nothing at
   * all -- on the panel whose entire job is answering "what changed?". No
   * operation this engine performs can remove a slide, which is exactly why it
   * has to be said if one ever does: an unreportable change is the only kind
   * that can quietly happen.
   */
  slidesAdded: number[];
  slidesRemoved: number[];
  /** Which side the canvas is showing. Flipping is how a difference is found. */
  showing: "before" | "after";
  /**
   * How much of the *after* is drawn over the *before*, 0 to 1.
   *
   * A user-driven blend, which is a different thing from an automatic
   * crossfade. A transition the interface runs hides the difference, because
   * the eye follows the fade; a slider the reader drags is how they find one —
   * they control the rate, can hold at any mixture, and can rock back and forth
   * over the spot they are unsure about. It is the oldest trick in comparison
   * and it still works.
   */
  blend: number;
}

export interface WorkspaceState {
  phase: Phase;
  document: SlideDocument | null;
  changeset: ChangeSet | null;
  verification: Verification | null;
  /** Real stages from the engine, in order. Never interpolated. */
  progress: ApplyProgress[];
  selectedSlide: number;
  selectedShape: string | null;
  /** The shape the eye is being carried to. Cleared once it has arrived. */
  carriedShape: string | null;
  /** Non-null while two versions are being compared. */
  comparison: Comparison | null;
  /**
   * What the user has declared must not change.
   *
   * Session state rather than a property of one request. "Do not touch slide 4,
   * the partner signed it off" is a standing guarantee, and a lock that had to
   * be re-declared on every proposal would be forgotten on the one that
   * mattered. Every path that proposes — typed edits, a tidy, a refresh —
   * carries these, so there is no door they do not cover.
   */
  locks: LockSpec[];
  error: string | null;
}

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

type Action =
  | { type: "opening" }
  | { type: "opened"; document: SlideDocument }
  | { type: "closed" }
  | { type: "proposing" }
  | { type: "proposed"; changeset: ChangeSet }
  | { type: "reviewed"; changeset: ChangeSet }
  | { type: "applying" }
  | { type: "stage"; progress: ApplyProgress }
  | { type: "settled"; verification: Verification; document: SlideDocument }
  | { type: "reverted"; document: SlideDocument }
  | { type: "select"; slide: number; shape?: string | null; carry?: boolean }
  | { type: "arrived" }
  | { type: "comparing"; comparison: Comparison }
  | { type: "flip" }
  | { type: "blend"; value: number }
  | { type: "stopComparing" }
  | { type: "lock"; lock: LockSpec }
  | { type: "unlock"; lock: LockSpec }
  | { type: "failed"; message: string }
  | { type: "dismissError" };

export function reducer(state: WorkspaceState, action: Action): WorkspaceState {
  switch (action.type) {
    case "opening":
      return { ...initial, phase: "opening" };

    case "opened":
      return {
        ...initial,
        phase: "ready",
        document: action.document,
        changeset: action.document.changeset,
        verification: action.document.verification,
        selectedSlide: action.document.deck.slides[0]?.number ?? 1,
      };

    case "closed":
      return initial;

    case "proposing":
      return { ...state, phase: "proposing", error: null };

    case "proposed":
      return { ...state, phase: "reviewing", changeset: action.changeset, verification: null };

    case "reviewed":
      return { ...state, changeset: action.changeset };

    case "applying":
      // Progress is cleared here rather than on completion, so a second apply
      // never shows the first one's stages while it starts.
      return { ...state, phase: "applying", progress: [], error: null };

    case "stage":
      return { ...state, progress: [...state.progress, action.progress] };

    case "settled":
      return {
        ...state,
        phase: "settled",
        verification: action.verification,
        document: action.document,
        changeset: action.document.changeset,
        // A comparison describes two specific versions. After an apply the
        // document has moved, so the one on screen no longer describes it.
        comparison: null,
        // Carry the eye to the first slide that actually changed. If nothing
        // changed there is nothing to carry to, and the view stays put.
        selectedSlide: action.verification.changed_slides[0] ?? state.selectedSlide,
      };

    case "reverted":
      return {
        ...state,
        phase: "ready",
        document: action.document,
        changeset: null,
        verification: null,
        progress: [],
        selectedShape: null,
        carriedShape: null,
        comparison: null,
      };

    case "select":
      return {
        ...state,
        selectedSlide: action.slide,
        selectedShape: action.shape === undefined ? state.selectedShape : action.shape,
        carriedShape: action.carry ? (action.shape ?? null) : null,
      };

    case "arrived":
      return { ...state, carriedShape: null };

    case "comparing":
      return {
        ...state,
        comparison: action.comparison,
        // Land on the first slide that differs. Opening a comparison on a slide
        // where nothing changed makes the feature look broken on its first use.
        selectedSlide: action.comparison.deltas[0]?.slide ?? state.selectedSlide,
        selectedShape: null,
      };

    case "flip": {
      if (!state.comparison) return state;
      const showing = state.comparison.showing === "after" ? "before" : "after";
      // Flipping resets the blend to that end, so the two controls never
      // disagree about what is on screen.
      return {
        ...state,
        comparison: { ...state.comparison, showing, blend: showing === "after" ? 1 : 0 },
      };
    }

    case "blend": {
      if (!state.comparison) return state;
      const value = Math.min(1, Math.max(0, action.value));
      return {
        ...state,
        comparison: {
          ...state.comparison,
          blend: value,
          // The label follows the blend so "showing v001" never contradicts a
          // canvas that is mostly v000.
          showing: value >= 0.5 ? "after" : "before",
        },
      };
    }

    case "stopComparing":
      return { ...state, comparison: null };

    case "lock":
      // Idempotent. Locking the same object twice is a slip, not an intent to
      // hold two identical guarantees.
      return same(state.locks, action.lock)
        ? state
        : { ...state, locks: [...state.locks, action.lock] };

    case "unlock":
      return { ...state, locks: state.locks.filter((l) => !matches(l, action.lock)) };

    case "failed":
      // A failure returns the user to where they can act, never to a dead end.
      // After a failed apply the change set is still theirs to fix.
      return {
        ...state,
        phase: state.changeset ? "reviewing" : state.document ? "ready" : "empty",
        error: action.message,
      };

    case "dismissError":
      return { ...state, error: null };
  }
}

function matches(a: LockSpec, b: LockSpec): boolean {
  return a.scope === b.scope && (a.target ?? "") === (b.target ?? "");
}

function same(locks: LockSpec[], lock: LockSpec): boolean {
  return locks.some((l) => matches(l, lock));
}

export function useWorkspace() {
  const [state, dispatch] = useReducer(reducer, initial);

  /**
   * The locks, readable from any callback without a dependency array.
   *
   * This is deliberate and it is not a shortcut. Every proposing callback needs
   * the *current* locks, and reading them from state means every one of those
   * callbacks must remember to list `state.locks` as a dependency. Two of three
   * did not, so `useCallback` handed back a closure holding the locks as they
   * were when it was created -- empty. The user protected an object, the
   * interface showed it protected, and the lock never reached the engine.
   *
   * A safety guarantee must not depend on getting a dependency array right. A
   * ref cannot go stale, so the failure is unavailable rather than merely
   * fixed.
   */
  const locksRef = useRef(state.locks);
  locksRef.current = state.locks;

  const fail = useCallback((error: unknown) => {
    const message =
      error instanceof ApiError
        ? error.message
        : error instanceof Error
          ? error.message
          : "Something failed and did not say why.";
    dispatch({ type: "failed", message });
  }, []);

  const open = useCallback(
    async (path: string) => {
      dispatch({ type: "opening" });
      try {
        const document = await api.open(path);
        remember(document.id);
        dispatch({ type: "opened", document });
      } catch (error) {
        fail(error);
      }
    },
    [fail],
  );

  const restore = useCallback(async () => {
    const id = remembered();
    if (!id) return;
    dispatch({ type: "opening" });
    try {
      dispatch({ type: "opened", document: await api.read(id) });
    } catch {
      // The document is gone -- the app was restarted, the deck moved, the
      // workspace deleted. None of those is an error the user asked about, so
      // this is not `fail`: it is the first-run screen, which is where they
      // were headed anyway.
      forget();
      dispatch({ type: "closed" });
    }
  }, []);

  const propose = useCallback(
    async (body: { instruction?: string; sets?: SetSpec[]; locks?: LockSpec[] }) => {
      if (!state.document) return;
      dispatch({ type: "proposing" });
      try {
        dispatch({
          type: "proposed",
          changeset: await api.propose(state.document.id, {
            ...body,
            locks: [...locksRef.current, ...(body.locks ?? [])],
          }),
        });
      } catch (error) {
        fail(error);
      }
    },
    [state.document, fail],
  );

  const review = useCallback(
    async (body: {
      approve?: string[];
      reject?: string[];
      approve_all?: boolean;
      include_unreviewed?: boolean;
    }) => {
      if (!state.document) return;
      try {
        dispatch({ type: "reviewed", changeset: await api.review(state.document.id, body) });
      } catch (error) {
        fail(error);
      }
    },
    [state.document, fail],
  );

  const apply = useCallback(
    async (note: string) => {
      if (!state.document) return;
      const id = state.document.id;
      dispatch({ type: "applying" });
      try {
        const verification = await applyStreaming(id, note, (progress) =>
          dispatch({ type: "stage", progress }),
        );
        // Re-read rather than patching locally: after an apply the versions,
        // the change set statuses and the deck itself have all moved, and a
        // client that reconstructs them is a second implementation of the
        // engine's bookkeeping.
        dispatch({ type: "settled", verification, document: await api.read(id) });
      } catch (error) {
        fail(error);
      }
    },
    [state.document, fail],
  );

  /**
   * Propose the corrections a tidy pass would make.
   *
   * Deliberately routed through the same `proposed` action as an ordinary
   * request. A tidy that had its own state would be a second path to mutation,
   * and the one thing this product cannot have is two answers to "did someone
   * approve this".
   */
  const tidy = useCallback(
    async (template = "") => {
      if (!state.document) return;
      dispatch({ type: "proposing" });
      try {
        dispatch({
          type: "proposed",
          changeset: await api.tidy(state.document.id, template, locksRef.current),
        });
      } catch (error) {
        fail(error);
      }
    },
    [state.document, fail],
  );

  /**
   * Propose the figures a source explains.
   *
   * The same `proposed` action as everything else. Refresh is the highest-stakes
   * thing this product does — a wrong number is invisible, because it looks
   * exactly like a right one — which is the strongest possible argument for it
   * going through the ordinary review step rather than around it.
   */
  const refresh = useCallback(
    async (sources: string[]) => {
      if (!state.document) return;
      dispatch({ type: "proposing" });
      try {
        dispatch({
          type: "proposed",
          changeset: await api.refresh(state.document.id, sources, locksRef.current),
        });
      } catch (error) {
        fail(error);
      }
    },
    [state.document, fail],
  );

  const revert = useCallback(
    async (to: number) => {
      if (!state.document) return;
      try {
        dispatch({ type: "reverted", document: await api.revert(state.document.id, to) });
      } catch (error) {
        fail(error);
      }
    },
    [state.document, fail],
  );

  /**
   * Compare two versions of the deck.
   *
   * Both decks and the deltas are fetched together, because a comparison that
   * paints one side before the other has arrived shows a difference that is not
   * there — the missing half looks like a deletion.
   */
  const compare = useCallback(
    async (from: number, to: number) => {
      if (!state.document) return;
      const id = state.document.id;
      try {
        const [before, after, diff] = await Promise.all([
          api.deck(id, from),
          api.deck(id, to),
          api.diff(id, from, to),
        ]);
        dispatch({
          type: "comparing",
          comparison: {
            from, to, before, after, deltas: diff.deltas,
            slidesAdded: diff.slides_added ?? [],
            slidesRemoved: diff.slides_removed ?? [],
            showing: "after", blend: 1,
          },
        });
      } catch (error) {
        fail(error);
      }
    },
    [state.document, fail],
  );

  const flip = useCallback(() => dispatch({ type: "flip" }), []);
  const blend = useCallback((value: number) => dispatch({ type: "blend", value }), []);
  const stopComparing = useCallback(() => dispatch({ type: "stopComparing" }), []);

  const lock = useCallback((next: LockSpec) => dispatch({ type: "lock", lock: next }), []);
  const unlock = useCallback((next: LockSpec) => dispatch({ type: "unlock", lock: next }), []);

  const select = useCallback((slide: number, shape?: string | null, carryEye = false) => {
    dispatch({ type: "select", slide, shape, carry: carryEye });
  }, []);

  /** Jump to a change: select its slide, then carry the eye to its object. */
  const goToChange = useCallback((change: Change) => {
    dispatch({
      type: "select",
      slide: change.slide,
      shape: change.target.split("/")[0] ?? null,
      carry: true,
    });
  }, []);

  const arrived = useCallback(() => dispatch({ type: "arrived" }), []);
  const dismissError = useCallback(() => dispatch({ type: "dismissError" }), []);

  const derived = useDerived(state);

  return {
    ...state,
    ...derived,
    open,
    restore,
    propose,
    tidy,
    refresh,
    review,
    apply,
    revert,
    select,
    lock,
    unlock,
    compare,
    flip,
    blend,
    stopComparing,
    goToChange,
    arrived,
    dismissError,
  };
}

function useDerived(state: WorkspaceState) {
  /**
   * The slide on screen, from whichever deck is being shown.
   *
   * In a comparison this is the chosen side rather than the document's current
   * version — the whole point of flipping is that the canvas actually changes.
   */
  const slide = useMemo(() => {
    const deck = state.comparison
      ? state.comparison[state.comparison.showing]
      : state.document?.deck;
    return deck?.slides.find((s) => s.number === state.selectedSlide) ?? null;
  }, [state.document, state.comparison, state.selectedSlide]);

  /**
   * Slides the current change set would touch, or did.
   *
   * Taken from the change set while reviewing and from the verification once
   * applied. Those are different questions -- "what is proposed" and "what
   * actually moved" -- and conflating them is how a UI ends up claiming a
   * rejected change was made.
   */
  const changedSlides = useMemo(() => {
    // A comparison answers its own question and overrides both: the user asked
    // what differs between these two versions, not what a pending change set
    // would touch.
    if (state.comparison) return new Set(state.comparison.deltas.map((d) => d.slide));
    if (state.verification) return new Set(state.verification.changed_slides);
    const live = state.changeset?.changes.filter(
      (c) => c.status === "approved" || c.status === "proposed",
    );
    return new Set((live ?? []).map((c) => c.slide));
  }, [state.changeset, state.verification, state.comparison]);

  const changedShapes = useMemo(() => {
    if (state.comparison) {
      return new Set(state.comparison.deltas.map((d) => d.shape_id));
    }
    const changes = state.changeset?.changes ?? [];
    return new Set(
      changes
        .filter((c) => c.status !== "rejected")
        .map((c) => c.target.split("/")[0])
        .filter((id): id is string => Boolean(id)),
    );
  }, [state.changeset, state.comparison]);

  /**
   * Shapes the user has protected, resolved for the slide on screen.
   *
   * A `slide` lock protects everything on it, so it expands here rather than
   * being drawn as a single marker somewhere — the reader needs to see that
   * *these objects* are the ones that cannot move.
   */
  const protectedShapes = useMemo(() => {
    const shapes = new Set<string>();
    const slideLocked = state.locks.some(
      (l) => l.scope === "slide" && String(l.target) === String(state.selectedSlide),
    );
    for (const shape of slide?.shapes ?? []) {
      if (slideLocked) shapes.add(shape.id);
    }
    for (const lock of state.locks) {
      if (lock.scope === "shape" && lock.target) shapes.add(lock.target);
    }
    return shapes;
  }, [state.locks, state.selectedSlide, slide]);

  const pending = state.changeset?.proposed_count ?? 0;
  const approved = state.changeset?.approved_count ?? 0;

  return {
    slide,
    changedSlides,
    changedShapes,
    protectedShapes,
    pending,
    approved,
    canApply: approved > 0 && state.phase !== "applying",
  };
}
