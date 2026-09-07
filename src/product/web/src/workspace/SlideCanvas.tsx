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

import type { Shape, Slide } from "../api/types";
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
  carried,
  selected,
  onSelect,
}: {
  shape: Shape;
  changed: boolean;
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
        outlineStyle: "solid",
        outlineOffset: 0,
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
    <div className="flex size-full flex-col justify-start overflow-hidden px-1 leading-tight">
      {shape.runs.map((run, index) => (
        <span
          // Runs have no stable identity in OOXML; index is the only address.
          key={index}
          style={{
            fontSize: run.size_pt ? run.size_pt * PT_TO_PX : undefined,
            fontWeight: run.bold ? 600 : undefined,
            fontStyle: run.italic ? "italic" : undefined,
            fontFamily: run.font ?? undefined,
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
      ))}
    </div>
  );
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
