import { MotionConfig } from "motion/react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("no #root to mount into");

/**
 * `reducedMotion="user"` is the whole reason this file has a wrapper.
 *
 * styles.css has honoured `prefers-reduced-motion` since the first commit, and
 * the design language says so — but that rule collapses CSS animations and
 * transitions, and Motion animates by writing inline styles from JavaScript. It
 * never touched a single one of them: not the panel entrances, not the stagger,
 * not the changed-region halo. The claim in `07-design-language.md` was true of
 * the stylesheet and false of the application.
 *
 * Motion defaults to `"never"`, which means "ignore the preference". `"user"`
 * drops transform and layout animation for a reader who has asked for less
 * motion, while keeping opacity — which is the right line: a fade is not what
 * causes vestibular trouble, movement is.
 */
createRoot(root).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <App />
    </MotionConfig>
  </StrictMode>,
);
