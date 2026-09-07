/**
 * What a reader would notice.
 *
 * The counterpart to verification, and the distinction is the point. `verify`
 * answers *is the package intact* — which parts differ byte for byte, whether
 * any native object was lost. It is the guarantee, and it cannot be argued
 * with. This answers *what actually reads differently*, which is a question a
 * part-level hash cannot reach: "104 of 105 parts identical" is true and tells
 * you nothing about whether a figure moved.
 *
 * So the deltas are split on the one axis the whole wedge turns on:
 *
 *   · **content** — what the deck says. Text, table shape, slides added or
 *     removed. A tidy that produces one of these has failed.
 *   · **presentation** — how it looks. Typeface, colour, position, size.
 *
 * That split is the engine's (`ShapeDelta.is_content`), not this component's.
 * A surface that decided for itself which changes were "real" would be a second
 * opinion on the only claim the product makes.
 */

import { motion } from "motion/react";

import type { Comparison } from "../state/workspace";
import type { Delta } from "../api/types";
import { Button, Empty, PanelHeading, Pill } from "../design/primitives";
import { enter, stagger } from "../motion/tokens";

/** Delta kinds, as the engine names them, in the order a reviewer cares about. */
const KIND_ORDER = ["removed", "added", "text", "table", "geometry", "size", "formatting"];

export function DiffPanel({
  comparison,
  onGoTo,
  onFlip,
  onClose,
}: {
  comparison: Comparison;
  onGoTo: (delta: Delta) => void;
  onFlip: () => void;
  onClose: () => void;
}) {
  const content = sorted(comparison.deltas.filter((d) => d.is_content));
  const presentation = sorted(comparison.deltas.filter((d) => !d.is_content));

  return (
    <>
      <PanelHeading
        trailing={
          <Button tone="quiet" onClick={onClose}>
            Close
          </Button>
        }
      >
        v{pad(comparison.from)} → v{pad(comparison.to)}
      </PanelHeading>

      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-line px-3 py-2">
        <p className="text-2xs leading-relaxed text-ink-faint">
          {comparison.deltas.length === 0 ? (
            "Nothing a reader would notice."
          ) : (
            <>
              <span className="text-evidence text-ink-muted">{content.length}</span> change
              {content.length === 1 ? "" : "s"} what it says ·{" "}
              <span className="text-evidence text-ink-muted">{presentation.length}</span>{" "}
              change{presentation.length === 1 ? "" : "s"} how it looks
            </>
          )}
        </p>
        <Flip comparison={comparison} onFlip={onFlip} />
      </div>

      {comparison.deltas.length === 0 ? (
        <Empty
          title="These two versions read the same"
          detail="Their bytes may still differ — verification answers that. Nothing a reader would notice is different."
        />
      ) : (
        <motion.div
          className="min-h-0 flex-1 overflow-y-auto"
          initial="hidden"
          animate="shown"
          transition={stagger(comparison.deltas.length)}
        >
          {content.length > 0 && (
            <Group
              title="Changes what the deck says"
              note="Words, numbers, table shape. A tidy that produced one of these has failed."
              deltas={content}
              onGoTo={onGoTo}
            />
          )}
          {presentation.length > 0 && (
            <Group
              title="Changes how it looks"
              note="Typeface, colour, position, size."
              deltas={presentation}
              onGoTo={onGoTo}
            />
          )}
        </motion.div>
      )}
    </>
  );
}

/**
 * The before/after switch.
 *
 * Deliberately a hard swap with no transition, which is the one place in this
 * application where motion is refused on purpose. A crossfade between two
 * nearly identical images is precisely what hides the difference between them —
 * the eye follows the fade instead of the change. Blink comparison has been how
 * this is done since photographic plates.
 */
function Flip({ comparison, onFlip }: { comparison: Comparison; onFlip: () => void }) {
  return (
    // Hidden at the width where the canvas is, and for the same reason: a flip
    // control with nothing to look at is a button that appears to do nothing.
    // The delta list stays — reading what changed does not need a picture.
    <div
      className="hidden shrink-0 overflow-hidden rounded-md border border-line-strong md:flex"
      role="group"
      aria-label="Which version the canvas is showing"
    >
      {(["before", "after"] as const).map((side) => (
        <button
          key={side}
          type="button"
          aria-pressed={comparison.showing === side}
          onClick={() => comparison.showing !== side && onFlip()}
          className={[
            "px-2 py-0.5 text-2xs transition-colors duration-[120ms]",
            comparison.showing === side
              ? "bg-ink font-medium text-ground"
              : "bg-raised text-ink-faint hover:text-ink",
          ].join(" ")}
        >
          {side === "before" ? `v${pad(comparison.from)}` : `v${pad(comparison.to)}`}
        </button>
      ))}
    </div>
  );
}

function Group({
  title,
  note,
  deltas,
  onGoTo,
}: {
  title: string;
  note: string;
  deltas: Delta[];
  onGoTo: (delta: Delta) => void;
}) {
  return (
    <section>
      <div className="sticky top-0 z-10 border-b border-line bg-panel px-3 py-1.5">
        {/* h3: these sit under the panel heading, which is the h2. The tabbed
            panels have no heading of their own — their tab labels them — so
            their sections are h2 instead. */}
        <h3 className="text-2xs font-medium uppercase tracking-[0.08em] text-ink-muted">
          {title}
        </h3>
        <p className="mt-0.5 text-2xs leading-relaxed text-ink-faint">{note}</p>
      </div>
      {deltas.map((delta, index) => (
        <Row key={`${delta.slide}-${delta.shape_id}-${index}`} delta={delta} onGoTo={onGoTo} />
      ))}
    </section>
  );
}

function Row({ delta, onGoTo }: { delta: Delta; onGoTo: (delta: Delta) => void }) {
  return (
    <motion.li
      variants={enter}
      className="list-none border-b border-line last:border-b-0"
    >
      <button
        type="button"
        onClick={() => onGoTo(delta)}
        aria-label={`Go to slide ${delta.slide}: ${delta.description}`}
        className="group flex w-full items-start gap-2 px-3 py-2 text-left transition-colors duration-[120ms] hover:bg-[color-mix(in_oklab,var(--color-panel),white_3%)]"
      >
        <span className="text-evidence w-5 shrink-0 pt-0.5 text-ink-faint">{delta.slide}</span>
        <span className="min-w-0 flex-1">
          <span className="block text-xs leading-snug text-ink">{delta.description}</span>
          <Pill
            className="mt-1"
            verdict={delta.is_content ? "changed" : "neutral"}
          >
            {delta.kind}
          </Pill>
        </span>
      </button>
    </motion.li>
  );
}

function sorted(deltas: Delta[]): Delta[] {
  const rank = (d: Delta) => {
    const index = KIND_ORDER.indexOf(d.kind);
    return index === -1 ? KIND_ORDER.length : index;
  };
  return [...deltas].sort((a, b) => a.slide - b.slide || rank(a) - rank(b));
}

function pad(version: number): string {
  return String(version).padStart(3, "0");
}
