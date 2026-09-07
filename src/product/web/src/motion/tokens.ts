/**
 * The motion language. Three durations, two curves, one exception.
 *
 * The UX architecture is explicit about why this stays small: a product whose
 * promise is "I will not touch what you did not ask me to touch" cannot ship an
 * interface that redecorates itself. Motion here is either feedback (something
 * you did) or continuity (something moved and you should know where).
 *
 * The exception is `carry`, and it is the one animation worth real investment:
 * connecting a claim about a change to the evidence for it. When the report
 * says slide 12 changed, motion is what walks the eye from the filmstrip mark
 * to the outlined region on the canvas. Everything else stays at the floor.
 */

import type { Transition, Variants } from "motion/react";

export const DURATION = {
  fast: 0.12,
  normal: 0.22,
  deliberate: 0.42,
} as const;

/** Decisive in, gentler out. Nothing overshoots, because nothing here bounces. */
export const EASE_OUT = [0.16, 1, 0.3, 1] as const;
export const EASE_IN_OUT = [0.65, 0, 0.35, 1] as const;

export const feedback: Transition = { duration: DURATION.fast, ease: EASE_OUT };
export const reveal: Transition = { duration: DURATION.normal, ease: EASE_OUT };
export const carry: Transition = { duration: DURATION.deliberate, ease: EASE_IN_OUT };

/**
 * Panels and rows entering or leaving.
 *
 * The offset is 4px, not 20px. A panel that travels far enough to notice is
 * telling you about itself; one that travels 4px is telling you it arrived.
 */
export const enter: Variants = {
  hidden: { opacity: 0, y: 4 },
  shown: { opacity: 1, y: 0, transition: reveal },
  gone: { opacity: 0, y: -2, transition: { duration: DURATION.fast, ease: EASE_OUT } },
};

/** A list whose items should read as arriving together, not marching in. */
export const stagger = (count: number): Transition => ({
  // Capped so a 40-change review does not become a 4-second wait to read it.
  staggerChildren: Math.min(0.02, 0.4 / Math.max(count, 1)),
});

/**
 * The changed-region pulse: a halo that swells once and settles.
 *
 * `rest` must paint nothing. An earlier version put the changed ring in the
 * rest state, which meant every object on every slide wore the attention
 * colour permanently — the exact opposite of what the colour is for, and it
 * made "nothing else moved" impossible to see.
 *
 * The ring itself belongs to the `ring-changed` class, so it survives whatever
 * this animation is doing; only the halo around it is animated. It runs once
 * and stops, because a pulse that repeats is an alarm and nothing in a verified
 * result is an alarm.
 */
export const attention: Variants = {
  rest: { outlineWidth: 0, outlineColor: "rgba(0,0,0,0)" },
  carried: {
    outlineWidth: [0, 6, 0],
    outlineColor: [
      "rgba(0,0,0,0)",
      "var(--color-changed-wash)",
      "rgba(0,0,0,0)",
    ],
    transition: { duration: DURATION.deliberate, ease: EASE_IN_OUT },
  },
};
