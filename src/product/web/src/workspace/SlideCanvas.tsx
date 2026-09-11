/**
 * The slide, drawn from its structure.
 *
 * This is a *structural* preview and is labelled as one in the UI. It draws
 * every object where the OOXML says it is, at the size the OOXML says it is,
 * with the text and formatting the engine read. It is not a PowerPoint render:
 * there is no theme fill resolution, no autofit, no line breaking that matches
 * Word's, and pictures show as their frames rather than their pixels.
 *
 * Calling it a preview and leaving it at that would be the ordinary dishonesty
 * here. It is worth being precise, because for the job this canvas actually
 * does -- showing *where* a change landed and that nothing around it moved --
 * exact boxes are better evidence than an approximate render would be. What it
 * is bad at is judging whether the result looks good, and the UI never claims
 * it is for that.
 */

import { motion } from "motion/react";
import { useMemo } from "react";

import type { Run, Shape, Slide } from "../api/types";
import { attention, carry } from "../motion/tokens";

/** EMU per CSS pixel at 96 DPI. 914400 EMU to the inch. */
const EMU_PER_PX = 9525;
/** Points to pixels. Text is specified in points and drawn in pixels. */
const PT_TO_PX = 96 / 72;

const px = (emu: number | null | undefined) => (emu == null ? 0 : emu / EMU_PER_PX);

/** Objects the engine reads but does not draw. Named, never faked. */
const OPAQUE = new Set(["chart", "picture", "smartart", "media"]);

export interface CanvasProps {
  slide: Slide;
  slideWidth: number;
  slideHeight: number;
  /** Shape ids the current change set touches. The only thing outlined. */
  changedShapes: Set<string>;
  /** Shape ids the user has declared must not change. */
  protectedShapes?: Set<string>;
  /** The shape just navigated to, if any: the one the eye is being carried to. */
  carriedShape?: string | null;
  selectedShape?: string | null;
  onSelectShape?: (shapeId: string | null) => void;
}

export function SlideCanvas({
  slide,
  slideWidth,
  slideHeight,
  changedShapes,
  protectedShapes,
  carriedShape,
  selectedShape,
  onSelectShape,
}: CanvasProps) {
  const width = px(slideWidth) || 960;
  const height = px(slideHeight) || 540;

  // Drawn in the slide's own pixel space and scaled by CSS. Laying out at the
  // display size instead would re-run text metrics on every resize, and the
  // canvas would reflow while the user was reading it.
  const ordered = useMemo(() => slide.shapes, [slide.shapes]);

  // Two elements, and the outer one is not decoration. `transform: scale` does
  // not change the space an element occupies, so a scaled canvas alone still
  // claims its full 1280x720 and overflows the stage in both directions. The
  // outer box carries the *scaled* size in layout; the inner one is drawn at
  // the slide's own size and scaled into it. Both read the same CSS variable,
  // so zooming re-renders nothing.
  return (
    <div
      style={{
        width: `calc(${width}px * var(--canvas-scale, 1))`,
        height: `calc(${height}px * var(--canvas-scale, 1))`,
      }}
    >
      <div
        className="paper-lift relative select-none bg-paper text-paper-ink"
        style={{
          width,
          height,
          transform: "scale(var(--canvas-scale, 1))",
          transformOrigin: "top left",
        }}
        onPointerDown={(event) => {
          if (event.target === event.currentTarget) onSelectShape?.(null);
        }}
        role="group"
        aria-label={`Slide ${slide.number}${slide.title ? `: ${slide.title}` : ""}`}
      >
        {ordered.map((shape) => (
          <ShapeBox
            key={shape.id}
            shape={shape}
            changed={changedShapes.has(shape.id)}
            locked={protectedShapes?.has(shape.id) ?? false}
            carried={carriedShape === shape.id}
            selected={selectedShape === shape.id}
            onSelect={onSelectShape}
          />
        ))}
      </div>
    </div>
  );
}

function ShapeBox({
  shape,
  changed,
  locked,
  carried,
  selected,
  onSelect,
}: {
  shape: Shape;
  changed: boolean;
  locked: boolean;
  carried: boolean;
  selected: boolean;
  onSelect?: (id: string | null) => void;
}) {
  // A shape with no geometry of its own is placed by the layout, which this
  // canvas cannot resolve. Drawing it at 0,0 would put it in the corner and
  // look like a bug in the deck; omitting it silently would be worse. It is
  // listed in the object inspector instead, and the canvas says nothing.
  if (shape.x == null || shape.y == null || shape.cx == null || shape.cy == null) {
    return null;
  }

  const interactive = Boolean(onSelect);

  return (
    <motion.div
      layout={false}
      variants={attention}
      initial={false}
      animate={carried ? "carried" : "rest"}
      transition={carry}
      className={[
        "absolute overflow-hidden",
        locked ? "protected-hatch" : "",
        changed ? "ring-changed" : "",
        selected && !changed ? "ring-selected" : "",
        interactive ? "cursor-pointer" : "",
      ].join(" ")}
      style={{
        left: px(shape.x),
        top: px(shape.y),
        width: px(shape.cx),
        height: px(shape.cy),
        rotate: shape.rotation_deg ?? 0,
      }}
      onPointerDown={
        interactive
          ? (event) => {
              event.stopPropagation();
              onSelect?.(shape.id);
            }
          : undefined
      }
      data-shape-id={shape.id}
      data-changed={changed || undefined}
      data-protected={locked || undefined}
      aria-label={locked ? `${shape.name || shape.kind} — protected` : undefined}
    >
      <ShapeBody shape={shape} />
    </motion.div>
  );
}

function ShapeBody({ shape }: { shape: Shape }) {
  if (shape.kind === "table") return <TableBody shape={shape} />;
  if (OPAQUE.has(shape.kind)) return <OpaqueBody shape={shape} />;
  return <TextBody shape={shape} />;
}

function TextBody({ shape }: { shape: Shape }) {
  if (!shape.runs.length) {
    // An empty box is real content: it holds space, and the audit has an
    // opinion about it. Drawn as a hairline so the layout stays honest.
    return <div className="size-full border border-dashed border-paper-line opacity-40" />;
  }
  return (
    // Top-anchored, which is OOXML's default. Centring looked tidier and was
    // wrong more often, which on a view whose only claim is accuracy is the
    // worse trade.
    //
    // Paragraphs are the lines; runs are a formatting split *within* a line.
    // Laying the runs out directly as a column made every run its own flex
    // item, so "Revenue grew **15%** in FY25" drew as three stacked lines --
    // measured in Chrome at tops 1, 17 and 33 where there should have been one.
    // Any sentence with a bold word, an emphasised figure or a hyperlink in it
    // was drawn broken, on the view whose only claim is that it is accurate.
    <div className="flex size-full flex-col justify-start overflow-hidden px-1 leading-tight">
      {paragraphsOf(shape.runs, shape.paragraph_count).map((runs, line) => (
        // A line with no runs still needs height, or the lines below it move
        // up. `\u00a0` rather than an empty <p>, because an empty block
        // collapses.
        <p key={line}>{runs.length ? runs.map(renderRun) : "\u00a0"}</p>
      ))}
    </div>
  );
}

/**
 * Runs grouped into the lines they belong to, in order — blank lines included.
 *
 * `runs` carries only runs with text, so grouping by adjacency drew a shape
 * with a gap in it as a shape without one: three paragraphs, two lines, and
 * everything below the gap a line too high. 77 of the 768 text shapes in the
 * corpus already have a blank line, and until the applier was fixed every
 * whole-shape edit created more. `paragraph` is an index into *all* the
 * paragraphs, so the empty ones are exactly the indices no run claims.
 */
function paragraphsOf(runs: Run[], paragraphCount: number): Run[][] {
  const highest = runs.reduce((most, run) => Math.max(most, run.paragraph), -1);
  const total = Math.max(paragraphCount, highest + 1);
  const lines: Run[][] = Array.from({ length: total }, () => []);
  for (const run of runs) lines[run.paragraph]?.push(run);
  return lines;
}

function renderRun(run: Run, index: number) {
  return (
        <span
          // Runs have no stable identity in OOXML; index is the only address.
          key={index}
          style={{
            fontSize: run.size_pt ? run.size_pt * PT_TO_PX : undefined,
            fontWeight: run.bold ? 600 : undefined,
            fontStyle: run.italic ? "italic" : undefined,
            fontFamily: run.font ?? undefined,
            // Underline, strikethrough, capitals and baseline are all things a
            // reader sees and this view used to drop. ALL CAPS is the loudest:
            // it is a property of the run, so a header reading DIVIDER is
            // stored as "divider" and drew in lower case here.
            textDecorationLine: decoration(run) || undefined,
            textDecorationStyle: run.underline === "dbl" ? "double" : undefined,
            textTransform:
              run.caps === "all"
                ? "uppercase"
                : run.caps === "small"
                  ? "lowercase"
                  : undefined,
            fontVariantCaps: run.caps === "small" ? "small-caps" : undefined,
            verticalAlign: run.baseline
              ? run.baseline > 0
                ? "super"
                : "sub"
              : undefined,
            // Deliberately no colour. This view resolves no fills, so a slide
            // with white text on a dark photograph would render white on white
            // and simply vanish -- the object would look absent rather than
            // unrendered, which is the one impression this canvas must never
            // give. Colour is real and editable, so it is shown where it can be
            // shown exactly: in the change row, as before and after.
          }}
        >
          {run.text}
        </span>
  );
}

/** Underline and strikethrough together, since CSS takes them on one property. */
function decoration(run: Run): string {
  const lines = [];
  if (run.underline) lines.push("underline");
  if (run.strike) lines.push("line-through");
  return lines.join(" ");
}

function TableBody({ shape }: { shape: Shape }) {
  const rows = Array.from({ length: shape.table_rows }, (_, r) => r);
  const cols = Array.from({ length: shape.table_cols }, (_, c) => c);
  return (
    <table className="size-full border-collapse text-[10px]">
      <tbody>
        {rows.map((r) => (
          <tr key={r}>
            {cols.map((c) => (
              <td
                key={c}
                className="border border-paper-line px-1 align-middle"
                data-cell={`r${r}/c${c}`}
              >
                {shape.table_cells[`r${r}/c${c}`] ?? ""}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * An object this canvas cannot draw, drawn as itself.
 *
 * The temptation is a generic grey box. But "chart" and "picture" mean very
 * different things to someone checking their deck survived -- one is the thing
 * the engine refuses to edit (ADR-0009), the other is the thing that goes
 * missing when a tool rasterises. Naming it is the whole value.
 */
function OpaqueBody({ shape }: { shape: Shape }) {
  return (
    <div className="flex size-full items-center justify-center border border-paper-line bg-[color-mix(in_oklab,var(--color-paper),black_3%)]">
      <span className="text-evidence text-paper-ink opacity-45">{shape.kind}</span>
    </div>
  );
}
