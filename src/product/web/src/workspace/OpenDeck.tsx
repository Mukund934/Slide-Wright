/**
 * First run. One box: point at a `.pptx`.
 *
 * No signup, no template gallery, no tour. The UX architecture is explicit that
 * if "open a deck, ask for one change, see the change report" does not produce
 * the reaction, no amount of onboarding will fix it.
 *
 * It asks for a *path*, which is unusual and is the point. A browser file input
 * hands over bytes and hides where they came from; this product's whole claim is
 * that the document stays where it is, and the engine opens it in place. Drag
 * and drop is supported because it is how people actually reach for a file —
 * and on the browsers that can, it yields the real path.
 *
 * The composition earns its restraint rather than defaulting to it. An earlier
 * version put a small card in the middle of a very large dark field, and the
 * result read as unfinished rather than spare: nothing was large enough to be
 * the subject. So the promise carries the page, the input is the only bright
 * thing on it, and the fine print is the one thing genuinely small.
 */

import { motion } from "motion/react";
import { useState } from "react";

import { Button } from "../design/primitives";
import { DURATION, EASE_OUT, enter } from "../motion/tokens";

export function OpenDeck({
  busy,
  error,
  onOpen,
}: {
  busy: boolean;
  error: string | null;
  onOpen: (path: string) => void;
}) {
  const [path, setPath] = useState("");
  const [over, setOver] = useState(false);

  const submit = () => {
    const trimmed = path.trim().replace(/^"|"$/g, "");
    if (trimmed && !busy) onOpen(trimmed);
  };

  return (
    <div
      className="flex h-full items-center justify-center overflow-y-auto p-6"
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={(event) => {
        // Only when the pointer actually left the page, not on every child.
        if (event.currentTarget === event.target) setOver(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        const dropped = event.dataTransfer.files[0];
        if (!dropped) return;
        // Chromium exposes the real path on a dropped file in some contexts and
        // not others. Where it does, this is the whole interaction; where it
        // does not, the name lands in the field and the user completes the
        // folder. Better than silently failing.
        const withPath = dropped as File & { path?: string };
        setPath(withPath.path ?? dropped.name);
      }}
    >
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: DURATION.deliberate, ease: EASE_OUT }}
        // Fluid rather than fixed. At 576px the block was right on a laptop and
        // adrift on a 1920 monitor, where it took 30% of the width and read as
        // small again -- the same composition problem as before, arriving from
        // the other direction. Width and type both scale with the viewport, so
        // there is no width at which this is the wrong size and no breakpoint
        // where it jumps.
        className="w-full"
        style={{ maxWidth: "clamp(28rem, 45vw, 44rem)" }}
      >
        <p className="text-2xs uppercase tracking-[0.18em] text-ink-faint">Slide-Wright</p>

        {/* The promise is the largest thing on the page, because it is the only
            claim the product makes and the whole reason to trust it with a file
            that matters. */}
        <h1
          className="mt-2 font-medium leading-snug tracking-[-0.015em] text-ink"
          style={{ fontSize: "clamp(1.25rem, 1rem + 1vw, 2.25rem)" }}
        >
          Change what you asked.
          <br />
          Preserve everything else.{" "}
          <span className="text-ink-faint">Prove it.</span>
        </h1>

        {/* The drop highlight is the one place outside the workspace that wears
            the attention colour, and it is a deliberate carve-out rather than a
            lapse: this screen has no deck open, so it has no "changed" anything
            for the colour to be confused with. Nowhere past this point may do
            the same. */}
        <div
          className={[
            "mt-7 rounded-lg border p-4 transition-colors duration-[220ms] 2xl:mt-9 2xl:p-5",
            over ? "border-changed bg-changed-wash" : "border-line-strong bg-panel",
          ].join(" ")}
        >
          <label
            htmlFor="deck-path"
            className="block text-2xs uppercase tracking-[0.08em] text-ink-faint"
          >
            Path to a .pptx on this machine
          </label>
          <div className="mt-2 flex gap-2">
            <input
              id="deck-path"
              value={path}
              onChange={(event) => setPath(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && submit()}
              placeholder="C:\Users\you\Documents\Pitchbook_v9.pptx"
              spellCheck={false}
              autoFocus
              className="text-evidence min-w-0 flex-1 rounded-md border border-line-strong bg-raised px-3 py-2 text-ink placeholder:text-ink-faint focus:border-ink-faint"
            />
            <Button tone="primary" onClick={submit} busy={busy} disabled={!path.trim()}>
              Open
            </Button>
          </div>
          <p className="mt-2 text-2xs text-ink-faint">
            {over ? "Drop it" : "or drop a file anywhere on this page"}
          </p>
        </div>

        {error && (
          <motion.p
            variants={enter}
            initial="hidden"
            animate="shown"
            role="alert"
            className="mt-3 rounded-md border border-blocked/40 bg-blocked-wash px-3 py-2 text-xs leading-relaxed text-blocked"
          >
            {error}
          </motion.p>
        )}

        <Assurances />
      </motion.div>
    </div>
  );
}

/**
 * What the product will and will not do with the file, as three facts.
 *
 * This was two paragraphs of fine print, which is how a privacy disclosure gets
 * skipped. The one that actually changes behaviour — a model key means a deck
 * summary leaves the machine — is stated on its own rather than buried in the
 * middle of a sentence about telemetry.
 */
function Assurances() {
  return (
    <ul className="mt-7 space-y-1.5 border-t border-line pt-4 2xl:mt-9">
      {[
        ["Opened where it sits", "versions are kept in a folder beside it"],
        ["Nothing is uploaded", "no telemetry, no crash reports, no samples"],
        [
          "One exception, stated plainly",
          "with a model key, describing a change in prose sends a structural summary of the whole deck — every slide title, and the first 70 characters of every text object. Editing directly, auditing, tidying, refreshing, verifying and reverting send nothing.",
        ],
      ].map(([heading, detail]) => (
        <li key={heading} className="flex gap-2 text-2xs leading-relaxed">
          <span aria-hidden className="mt-1.5 size-1 shrink-0 rounded-full bg-line-strong" />
          <span className="text-ink-faint">
            <span className="text-ink-muted">{heading}</span> — {detail}
          </span>
        </li>
      ))}
    </ul>
  );
}
