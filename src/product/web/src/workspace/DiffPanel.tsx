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
 *   · **presentation** — how it looks. Typeface, colour, position, size,
 *     and the line a sentence sits on: its bullet, indent, alignment,
 *     spacing, and how many lines the shape ends up with.
 *
 * That split is the engine's (`ShapeDelta.is_content`), not this component's.
 * A surface that decided for itself which changes were "real" would be a second
 * opinion on the only claim the product makes.
 */

import { useState } from "react";

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
  onBlend,
  onClose,
}: {
  comparison: Comparison;
  onGoTo: (delta: Delta) => void;
  onFlip: () => void;
  onBlend: (value: number) => void;
  onClose: () => void;
}) {
  const content = sorted(comparison.deltas.filter((d) => d.is_content));
  const presentation = sorted(comparison.deltas.filter((d) => !d.is_content));
  const figures = comparison.deltas.filter((d) => d.changes_figures);

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

      {/* Above the counts, because a slide that is gone outranks a figure that
          moved, and because this is the one difference a reader cannot find by
          looking at the slide in front of them. */}
      {(comparison.slidesRemoved.length > 0 || comparison.slidesAdded.length > 0) && (
        <p className="shrink-0 border-b border-line px-3 py-2 text-xs leading-relaxed text-ink">
          {comparison.slidesRemoved.length > 0 && (
            <>
              <span className="text-evidence text-blocked">
                {comparison.slidesRemoved.length} slide
                {comparison.slidesRemoved.length === 1 ? "" : "s"} removed
              </span>{" "}
              ({comparison.slidesRemoved.join(", ")}){" "}
            </>
          )}
          {comparison.slidesAdded.length > 0 && (
            <>
              <span className="text-evidence text-changed">
                {comparison.slidesAdded.length} slide
                {comparison.slidesAdded.length === 1 ? "" : "s"} added
              </span>{" "}
              ({comparison.slidesAdded.join(", ")})
            </>
          )}
        </p>
      )}

      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-line px-3 py-2">
        {/* Figures first, and stated even when the answer is none.
            "No figure changed" is the sharpest claim this product can make and
            the one someone asking for a formatting pass actually wants — a
            reassurance about prose is not what they came for. It is the only
            count worth printing when it is zero, because a zero here is the
            whole point and a reader who has to infer it from an absence has not
            been told anything. */}
        <p className="text-2xs leading-relaxed text-ink-faint">
          {comparison.deltas.length === 0 ? (
            "Nothing a reader would notice."
          ) : (
            <>
              <span
                className={[
                  "text-evidence",
                  figures.length === 0 ? "text-verified" : "text-changed",
                ].join(" ")}
              >
                {figures.length === 0 ? "no figure changed" : `${figures.length} figure${figures.length === 1 ? "" : "s"} changed`}
              </span>{" "}
              ·{" "}
              <span className="text-evidence text-ink-muted">{content.length}</span> in what
              it says ·{" "}
              <span className="text-evidence text-ink-muted">{presentation.length}</span> in
              how it looks
            </>
          )}
        </p>
        <Flip comparison={comparison} onFlip={onFlip} />
      </div>

      <Blend comparison={comparison} onBlend={onBlend} />

      {comparison.deltas.length === 0
        && comparison.slidesAdded.length === 0
        && comparison.slidesRemoved.length === 0 ? (
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
              note="Typeface, colour, position, size, and how the lines sit."
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

/**
 * The blend between the two versions.
 *
 * A slider the reader drags, not a transition the interface plays. The
 * distinction is the whole value: an automatic crossfade *hides* a difference,
 * because the eye follows the fade instead of the change. A control the reader
 * holds lets them rock back and forth over the one spot they are unsure about,
 * at whatever rate finds it. Blink comparison predates the computer and it
 * still works.
 *
 * Hidden with the canvas, for the same reason the flip is: a slider with
 * nothing to look at does nothing.
 */
function Blend({
  comparison,
  onBlend,
}: {
  comparison: Comparison;
  onBlend: (value: number) => void;
}) {
  return (
    <div className="hidden shrink-0 items-center gap-2 border-b border-line px-3 py-1.5 md:flex">
      <label htmlFor="blend" className="shrink-0 text-2xs text-ink-faint">
        Blend
      </label>
      <input
        id="blend"
        type="range"
        min={0}
        max={1}
        step={0.02}
        value={comparison.blend}
        onChange={(event) => onBlend(Number(event.target.value))}
        aria-label={`Blend between v${pad(comparison.from)} and v${pad(comparison.to)}`}
        aria-valuetext={`${Math.round(comparison.blend * 100)}% v${pad(comparison.to)}`}
        className="h-1 min-w-0 flex-1 cursor-pointer appearance-none rounded-full bg-line-strong accent-[var(--color-ink)]"
      />
      <span className="text-evidence w-16 shrink-0 text-right text-ink-faint">
        {Math.round(comparison.blend * 100)}% v{pad(comparison.to)}
      </span>
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
      {collapse(deltas).map((run, index) =>
        run.deltas.length === 1 ? (
          <Row
            key={`${run.key}-${index}`}
            delta={run.first}
            onGoTo={onGoTo}
          />
        ) : (
          <RepeatedRow key={`${run.key}-${index}`} run={run} onGoTo={onGoTo} />
        ),
      )}
    </section>
  );
}

interface Repeated {
  key: string;
  first: Delta;
  deltas: Delta[];
}

/**
 * The same change in many places, shown once.
 *
 * A conformance pass on a real deck produces 272 differences and 266 of them
 * are one change: `font 'Century Gothic' -> '+mn-lt'`. This panel is the
 * evidence for the sentence above it — *no figure changed · 0 in what it says ·
 * 272 in how it looks* — and evidence nobody scrolls to the end of is not
 * evidence. `report.py` reached the same conclusion for the CLI first.
 *
 * Grouped on the engine's `summary`, which is the description with the location
 * taken out. Doing that here by trimming the sentence would be string surgery
 * on prose, and it would break the first time a shape was called "run 1".
 */
function collapse(deltas: Delta[]): Repeated[] {
  const runs: Repeated[] = [];
  const index = new Map<string, Repeated>();
  for (const delta of deltas) {
    const key = `${delta.kind}\u0000${delta.summary || delta.description}`;
    let run = index.get(key);
    if (!run) {
      run = { key, first: delta, deltas: [] };
      index.set(key, run);
      runs.push(run);
    }
    run.deltas.push(delta);
  }
  return runs;
}

function RepeatedRow({
  run,
  onGoTo,
}: {
  run: Repeated;
  onGoTo: (delta: Delta) => void;
}) {
  const [open, setOpen] = useState(false);
  const slides = [...new Set(run.deltas.map((d) => d.slide))].sort((a, b) => a - b);
  const where =
    slides.length === 1
      ? `slide ${slides[0]}`
      : `slides ${slides[0]}–${slides[slides.length - 1]} (${slides.length})`;

  return (
    <motion.li variants={enter} className="list-none border-b border-line last:border-b-0">
      <div className="flex w-full items-start gap-2 px-3 py-2">
        <span className="text-evidence w-5 shrink-0 pt-0.5 text-ink-muted">
          {run.deltas.length}&times;
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-xs leading-snug text-ink">
            {run.first.summary || run.first.description}
          </span>
          <span className="mt-1 flex flex-wrap items-center gap-1">
            <Pill verdict={run.first.is_content ? "changed" : "neutral"}>
              {run.first.kind}
            </Pill>
            <Pill>{where}</Pill>
            <button
              type="button"
              onClick={() => setOpen((was) => !was)}
              aria-expanded={open}
              className="text-2xs text-ink-faint underline decoration-dotted underline-offset-2 hover:text-ink"
            >
              {open ? "hide" : `show all ${run.deltas.length}`}
            </button>
          </span>
        </span>
      </div>
      {open && (
        <ul className="border-t border-line pl-3">
          {run.deltas.map((delta, index) => (
            <Row key={`${delta.slide}-${delta.shape_id}-${index}`} delta={delta} onGoTo={onGoTo} />
          ))}
        </ul>
      )}
    </motion.li>
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
          <span className="mt-1 flex flex-wrap items-center gap-1">
            <Pill verdict={delta.is_content ? "changed" : "neutral"}>{delta.kind}</Pill>
            {delta.changes_figures && <Pill verdict="changed">figure</Pill>}
          </span>
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
