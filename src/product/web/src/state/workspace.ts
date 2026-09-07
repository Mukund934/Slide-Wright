/**
 * Workspace state: one open deck, and what the user is doing to it.
 *
 * A reducer rather than scattered `useState`, for one reason that matters here:
 * the states of this product are a sequence, not a set. Idle → proposed →
 * reviewing → applying → verified-or-blocked. Expressing that as six booleans
 * makes "applying while nothing is approved" representable, and the whole
 * safety argument rests on it not being.
 */

import { useCallback, useMemo, useReducer } from "react";

import { ApiError, api, applyStreaming } from "../api/client";
import type {
  ApplyProgress,
  Change,
  ChangeSet,
  LockSpec,
  SetSpec,
  SlideDocument,
  Verification,
} from "../api/types";

export type Phase =
  | "empty"
  | "opening"
  | "ready"
  | "proposing"
  | "reviewing"
  | "applying"
  | "settled";

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

export function useWorkspace() {
  const [state, dispatch] = useReducer(reducer, initial);

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
        dispatch({ type: "opened", document: await api.open(path) });
      } catch (error) {
        fail(error);
      }
    },
    [fail],
  );

  const propose = useCallback(
    async (body: { instruction?: string; sets?: SetSpec[]; locks?: LockSpec[] }) => {
      if (!state.document) return;
      dispatch({ type: "proposing" });
      try {
        dispatch({ type: "proposed", changeset: await api.propose(state.document.id, body) });
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
  const tidy = useCallback(async () => {
    if (!state.document) return;
    dispatch({ type: "proposing" });
    try {
      dispatch({ type: "proposed", changeset: await api.tidy(state.document.id) });
    } catch (error) {
      fail(error);
    }
  }, [state.document, fail]);

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
    propose,
    tidy,
    review,
    apply,
    revert,
    select,
    goToChange,
    arrived,
    dismissError,
  };
}

function useDerived(state: WorkspaceState) {
  const slide = useMemo(
    () => state.document?.deck.slides.find((s) => s.number === state.selectedSlide) ?? null,
    [state.document, state.selectedSlide],
  );

  /**
   * Slides the current change set would touch, or did.
   *
   * Taken from the change set while reviewing and from the verification once
   * applied. Those are different questions -- "what is proposed" and "what
   * actually moved" -- and conflating them is how a UI ends up claiming a
   * rejected change was made.
   */
  const changedSlides = useMemo(() => {
    if (state.verification) return new Set(state.verification.changed_slides);
    const live = state.changeset?.changes.filter(
      (c) => c.status === "approved" || c.status === "proposed",
    );
    return new Set((live ?? []).map((c) => c.slide));
  }, [state.changeset, state.verification]);

  const changedShapes = useMemo(() => {
    const changes = state.changeset?.changes ?? [];
    return new Set(
      changes
        .filter((c) => c.status !== "rejected")
        .map((c) => c.target.split("/")[0])
        .filter((id): id is string => Boolean(id)),
    );
  }, [state.changeset]);

  const pending = state.changeset?.proposed_count ?? 0;
  const approved = state.changeset?.approved_count ?? 0;

  return {
    slide,
    changedSlides,
    changedShapes,
    pending,
    approved,
    canApply: approved > 0 && state.phase !== "applying",
  };
}
