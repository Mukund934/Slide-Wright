/**
 * The proof.
 *
 * This panel has one job and it is not reassurance: it is accuracy. Every
 * number is computed by the engine from part hashes and object counts, and this
 * component adds no interpretation of its own beyond arranging them.
 *
 * The rule that shapes it: **there is no green state unless the engine said
 * deliverable.** Not "mostly fine", not "verified with warnings" invented in
 * the client. The engine fails closed — an unattributed slide change, a removed
 * part, native object loss or suspected rasterisation each block delivery — and
 * a UI that softened any of those would be lying about the only thing this
 * product sells.
 */

import { motion } from "motion/react";

import type { ApplyProgress, Verification } from "../api/types";
import { PanelHeading, Pill, Stat } from "../design/primitives";
import { enter, reveal } from "../motion/tokens";

export function VerificationPanel({
  verification,
  progress,
  applying,
  onGoToSlide,
}: {
  verification: Verification | null;
  progress: ApplyProgress[];
  applying: boolean;
  onGoToSlide: (n: number) => void;
}) {
  if (applying) return <Progress progress={progress} />;
  if (!verification) return null;

  const blocked = !verification.deliverable;

  return (
    <motion.section
      variants={enter}
      initial="hidden"
      animate="shown"
      // Capped and scrollable. The census grows with the deck, and an
      // uncapped panel pushed the last rows off the bottom of the window --
      // the rows that say whether anything was lost.
      className="flex max-h-[48%] shrink-0 flex-col overflow-hidden border-t border-line"
      aria-live="polite"
    >
      <PanelHeading
        trailing={
          <Pill verdict={blocked ? "blocked" : "verified"}>
            {blocked ? "Not delivered" : "Verified"}
          </Pill>
        }
      >
        {blocked ? "Blocked" : "Result"}
      </PanelHeading>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
        {blocked ? (
          <Blocked verification={verification} onGoToSlide={onGoToSlide} />
        ) : (
          <Delivered verification={verification} />
        )}

        <div className="mt-2 border-t border-line pt-2">
          {verification.census.map((row) => (
            <Stat
              key={row.label}
              label={row.label}
              value={`${row.source} → ${row.output}`}
              intact={row.intact}
            />
          ))}
        </div>
      </div>
    </motion.section>
  );
}

function Delivered({ verification }: { verification: Verification }) {
  return (
    <>
      <p className="text-xs leading-relaxed text-ink">
        <strong className="font-medium">
          {verification.identical_parts} of {verification.total_parts}
        </strong>{" "}
        package parts are byte-for-byte identical to the file you supplied
        <span className="text-ink-faint">
          {" "}
          ({verification.fidelity_score.toFixed(2)}%)
        </span>
        .
      </p>
      <p className="mt-1 text-xs text-ink-muted">
        {verification.untouched_slides} slide
        {verification.untouched_slides === 1 ? "" : "s"} untouched ·{" "}
        {verification.requested.length} requested change
        {verification.requested.length === 1 ? "" : "s"} applied ·{" "}
        <span className="text-verified">0 unexpected</span>
      </p>
    </>
  );
}

/**
 * A refusal, explained.
 *
 * The UX architecture flags this as the state most likely to be misread: users
 * trained by other tools read a refusal as breakage. It is the guarantee
 * working, and the copy has to say so without being smug about withholding
 * someone's deck.
 */
function Blocked({
  verification,
  onGoToSlide,
}: {
  verification: Verification;
  onGoToSlide: (n: number) => void;
}) {
  return (
    <>
      <p className="text-xs leading-relaxed text-ink">
        Slide-Wright checked the result and found changes it cannot account for,
        so it did not deliver the deck. Your original is untouched.
      </p>
      <ul className="mt-2 space-y-1">
        {verification.blocking_reasons.map((reason) => (
          <li key={reason} className="flex gap-1.5 text-xs text-blocked">
            <span aria-hidden>·</span>
            <span className="leading-relaxed">{reason}</span>
          </li>
        ))}
      </ul>
      {verification.unrequested_slides.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-2xs text-ink-faint">Inspect:</span>
          {verification.unrequested_slides.map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => onGoToSlide(n)}
              className="rounded-sm bg-blocked-wash px-1.5 py-0.5 text-evidence text-blocked transition-colors duration-[120ms] hover:brightness-125"
            >
              slide {n}
            </button>
          ))}
        </div>
      )}
    </>
  );
}

/**
 * What is happening, while it happens.
 *
 * Stages, not a percentage. The engine reports each transition as it occurs and
 * knows nothing about how long the next one takes, so a bar would be a
 * fabrication — and fabricated confidence is the specific thing this product
 * exists to replace.
 */
const STAGE_COPY: Record<string, string> = {
  applying: "Writing the approved changes",
  applied: "Changes written",
  verifying: "Comparing every part against your original",
  verified: "Verified",
  blocked: "Verification failed — the deck was not delivered",
  refused: "Refused before writing anything",
};

function Progress({ progress }: { progress: ApplyProgress[] }) {
  return (
    <section
      className="shrink-0 border-t border-line px-3 py-3"
      aria-live="polite"
      aria-busy
    >
      <ol className="space-y-1.5">
        {progress.map((stage, index) => {
          const current = index === progress.length - 1;
          return (
            <motion.li
              key={`${stage.stage}-${index}`}
              variants={enter}
              initial="hidden"
              animate="shown"
              transition={reveal}
              className="flex items-start gap-2 text-xs"
            >
              <span
                aria-hidden
                className={[
                  "mt-1.5 size-1.5 shrink-0 rounded-full",
                  current ? "bg-changed" : "bg-line-strong",
                ].join(" ")}
              />
              <span className={current ? "text-ink" : "text-ink-faint"}>
                {STAGE_COPY[stage.stage] ?? stage.detail}
                {stage.slides?.length ? (
                  <span className="text-ink-faint">
                    {" "}
                    · slide{stage.slides.length === 1 ? "" : "s"} {stage.slides.join(", ")}
                  </span>
                ) : null}
              </span>
            </motion.li>
          );
        })}
        {progress.length === 0 && (
          <li className="text-xs text-ink-faint">Starting…</li>
        )}
      </ol>
      <p className="mt-2 text-2xs leading-relaxed text-ink-faint">
        A large deck takes minutes. Nothing is delivered until verification passes.
      </p>
    </section>
  );
}
