/**
 * The filmstrip: where the change is not.
 *
 * The UX architecture is blunt about this one. The overwhelming majority of
 * these entries stay unmarked, and that is the reassurance the product is
 * selling. So the unchanged state is the designed state, and the marked state
 * is the exception -- not the other way round.
 *
 * Which is why there is no pagination. Seeing that fifty-eight of sixty slides
 * are untouched is the whole point, and a paginated list can only ever show you
 * that the twelve you are looking at are.
 */

import { motion } from "motion/react";
import { useEffect, useRef } from "react";

import type { Slide } from "../api/types";
import { feedback } from "../motion/tokens";

export function Filmstrip({
  slides,
  selected,
  changed,
  onSelect,
}: {
  slides: Slide[];
  selected: number;
  changed: Set<number>;
  onSelect: (n: number) => void;
}) {
  const listRef = useRef<HTMLUListElement>(null);

  // Keep the selection in view when it moves because something else moved it --
  // jumping to a change from the review panel, most often. `nearest` so a
  // click on a visible row never scrolls the list under the pointer.
  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-slide="${selected}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  return (
    <ul
      ref={listRef}
      className="flex-1 overflow-y-auto py-1"
      role="listbox"
      aria-label="Slides"
      aria-activedescendant={`slide-${selected}`}
      tabIndex={0}
      onKeyDown={(event) => {
        const index = slides.findIndex((s) => s.number === selected);
        if (event.key === "ArrowDown" || event.key === "ArrowRight") {
          event.preventDefault();
          const next = slides[Math.min(index + 1, slides.length - 1)];
          if (next) onSelect(next.number);
        } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
          event.preventDefault();
          const previous = slides[Math.max(index - 1, 0)];
          if (previous) onSelect(previous.number);
        } else if (event.key === "Home") {
          event.preventDefault();
          if (slides[0]) onSelect(slides[0].number);
        } else if (event.key === "End") {
          event.preventDefault();
          const last = slides[slides.length - 1];
          if (last) onSelect(last.number);
        }
      }}
    >
      {slides.map((slide) => (
        <Row
          key={slide.number}
          slide={slide}
          selected={slide.number === selected}
          changed={changed.has(slide.number)}
          onSelect={onSelect}
        />
      ))}
    </ul>
  );
}

function Row({
  slide,
  selected,
  changed,
  onSelect,
}: {
  slide: Slide;
  selected: boolean;
  changed: boolean;
  onSelect: (n: number) => void;
}) {
  return (
    <li
      id={`slide-${slide.number}`}
      data-slide={slide.number}
      data-changed={changed || undefined}
      role="option"
      aria-selected={selected}
    >
      <button
        type="button"
        onClick={() => onSelect(slide.number)}
        className={[
          "group flex w-full items-center gap-2 px-3 py-1.5 text-left",
          "transition-colors duration-[--duration-fast]",
          selected ? "bg-[--color-raised]" : "hover:bg-[color-mix(in_oklab,var(--color-panel),white_3%)]",
        ].join(" ")}
      >
        <Mark changed={changed} />
        <span
          className={[
            "w-6 shrink-0 text-evidence tabular-nums",
            selected ? "text-[--color-ink]" : "text-[--color-ink-faint]",
          ].join(" ")}
        >
          {slide.number}
        </span>
        <span
          className={[
            "truncate text-xs",
            selected ? "text-[--color-ink]" : "text-[--color-ink-muted]",
          ].join(" ")}
        >
          {slide.title ?? <span className="italic opacity-60">untitled</span>}
        </span>
      </button>
    </li>
  );
}

/**
 * Hollow when untouched, filled when changed.
 *
 * A shape difference as well as a colour one, so the strip is readable without
 * relying on colour perception -- and so a screenshot in greyscale still makes
 * the argument.
 */
function Mark({ changed }: { changed: boolean }) {
  return (
    <motion.span
      aria-hidden
      initial={false}
      animate={{ scale: changed ? 1 : 0.72 }}
      transition={feedback}
      className={[
        "size-1.5 shrink-0 rounded-full border",
        changed
          ? "border-[--color-changed] bg-[--color-changed]"
          : "border-[--color-line-strong] bg-transparent",
      ].join(" ")}
    />
  );
}
