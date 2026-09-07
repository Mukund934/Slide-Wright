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
 */

import { motion } from "motion/react";
import { useState } from "react";

import { Button } from "../design/primitives";
import { enter } from "../motion/tokens";

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
    <div className="flex h-full items-center justify-center p-8">
      <motion.div
        variants={enter}
        initial="hidden"
        animate="shown"
        className="w-full max-w-lg"
      >
        <h1 className="text-base font-medium text-[--color-ink]">Slide-Wright</h1>
        <p className="mt-1 text-xs leading-relaxed text-[--color-ink-muted]">
          Change what you asked. Preserve everything else. Prove it.
        </p>

        <div
          onDragOver={(event) => {
            event.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(event) => {
            event.preventDefault();
            setOver(false);
            const dropped = event.dataTransfer.files[0];
            if (!dropped) return;
            // Chromium exposes the real path on a dropped file in some
            // contexts and not others. Where it does, this is the whole
            // interaction; where it does not, the name lands in the field and
            // the user completes the folder. Better than silently failing.
            const withPath = dropped as File & { path?: string };
            setPath(withPath.path ?? dropped.name);
          }}
          className={[
            "mt-5 rounded-[--radius-lg] border border-dashed p-6 transition-colors duration-[--duration-normal]",
            over
              ? "border-[--color-changed] bg-[--color-changed-wash]"
              : "border-[--color-line-strong] bg-[--color-panel]",
          ].join(" ")}
        >
          <label
            htmlFor="deck-path"
            className="block text-2xs uppercase tracking-[0.08em] text-[--color-ink-faint]"
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
              className="text-evidence min-w-0 flex-1 rounded-[--radius-md] border border-[--color-line-strong] bg-[--color-raised] px-2.5 py-2 text-[--color-ink] placeholder:text-[--color-ink-faint] focus:border-[--color-changed-dim] focus:outline-none"
            />
            <Button tone="primary" onClick={submit} busy={busy} disabled={!path.trim()}>
              Open
            </Button>
          </div>
          <p className="mt-2 text-2xs leading-relaxed text-[--color-ink-faint]">
            or drop a file here
          </p>
        </div>

        {error && (
          <motion.p
            variants={enter}
            initial="hidden"
            animate="shown"
            role="alert"
            className="mt-3 rounded-[--radius-md] bg-[--color-blocked-wash] px-3 py-2 text-xs leading-relaxed text-[--color-blocked]"
          >
            {error}
          </motion.p>
        )}

        <div className="mt-6 space-y-1.5 text-2xs leading-relaxed text-[--color-ink-faint]">
          <p>
            Your deck is opened where it sits. Slide-Wright keeps its versions in a
            folder beside it. Nothing is uploaded, and there is no telemetry.
          </p>
          <p>
            The one exception, stated plainly: if you configure a model key and{" "}
            <em>describe</em> a change, a structural summary of the whole deck —
            every slide title, and the first 70 characters of every text object —
            goes to that provider. Editing objects directly, auditing, verifying
            and reverting send nothing.
          </p>
        </div>
      </motion.div>
    </div>
  );
}
