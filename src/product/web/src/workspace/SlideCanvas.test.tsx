/**
 * The canvas, tested for restraint.
 *
 * The one rule this component can break silently is the colour rule: if
 * anything other than a changed object wears the attention ring, "nothing else
 * moved" stops being visible and the canvas is just a diagram. An earlier
 * version put the ring in the rest state of a motion variant and every object
 * on every slide wore it, which looked deliberate and was not.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Run, Shape, Slide } from "../api/types";
import { SlideCanvas } from "./SlideCanvas";

const WIDTH = 12192000;
const HEIGHT = 6858000;

function run(over: Partial<Run> = {}): Run {
  return {
    text: "hello", size_pt: 18, bold: false, italic: false, font: null,
    color: null, paragraph: 0, underline: null, strike: null, baseline: null,
    caps: null,
    ...over,
  };
}

function shape(over: Partial<Shape> = {}): Shape {
  const runs = over.runs ?? [run()];
  return {
    id: "1", name: "Rectangle 1", kind: "shape", placeholder_type: null,
    x: 914400, y: 914400, cx: 1828800, cy: 914400,
    rotation_deg: null, geometry_inherited: false, geometry: "rect",
    runs,
    // Defaults to "as many paragraphs as the runs name", so a test that cares
    // about blank lines is the only one that has to say so.
    paragraph_count: runs.reduce((most, r) => Math.max(most, r.paragraph + 1), 0),
    table_rows: 0, table_cols: 0, table_cells: {}, child_count: 0, text: "hello",
    ...over,
  };
}

function slide(shapes: Shape[]): Slide {
  return {
    number: 12, part_name: "slide12.xml", layout: "Title and Content",
    title: "Comparables", word_count: 4, shapes,
  };
}

function draw(shapes: Shape[], changed: string[] = [], extra = {}) {
  return render(
    <SlideCanvas
      slide={slide(shapes)}
      slideWidth={WIDTH}
      slideHeight={HEIGHT}
      changedShapes={new Set(changed)}
      {...extra}
    />,
  );
}

describe("the ring", () => {
  it("goes on the changed object and nowhere else", () => {
    const { container } = draw([shape({ id: "1" }), shape({ id: "2" }), shape({ id: "3" })], ["2"]);
    const ringed = container.querySelectorAll(".ring-changed");
    expect(ringed).toHaveLength(1);
    expect(ringed[0]?.getAttribute("data-shape-id")).toBe("2");
  });

  it("is absent entirely when nothing has been proposed", () => {
    const { container } = draw([shape({ id: "1" }), shape({ id: "2" })]);
    expect(container.querySelectorAll(".ring-changed")).toHaveLength(0);
  });

  it("does not follow the selection", () => {
    // Selecting is not changing. Wearing the attention colour would say it was.
    const { container } = draw([shape({ id: "1" })], [], { selectedShape: "1" });
    expect(container.querySelectorAll(".ring-changed")).toHaveLength(0);
    expect(container.querySelectorAll(".ring-selected")).toHaveLength(1);
  });
});

describe("what it draws", () => {
  it("places an object at the exact position and size the file gives", () => {
    const { container } = draw([shape({ x: 914400, y: 457200, cx: 1828800, cy: 914400 })]);
    const box = container.querySelector("[data-shape-id]") as HTMLElement;
    // 914400 EMU is one inch, which is 96px at 96 DPI.
    expect(box.style.left).toBe("96px");
    expect(box.style.top).toBe("48px");
    expect(box.style.width).toBe("192px");
    expect(box.style.height).toBe("96px");
  });

  it("omits a shape with no geometry at all rather than dropping it at the origin", () => {
    // Drawing it at 0,0 would look like a defect in the user's deck.
    const { container } = draw([shape({ x: null, y: null, cx: null, cy: null })]);
    expect(container.querySelectorAll("[data-shape-id]")).toHaveLength(0);
  });

  it("names an object it cannot draw instead of greying it out", () => {
    // "chart" and "picture" mean very different things to someone checking
    // their deck survived.
    const { container } = draw([shape({ kind: "chart", runs: [], text: "" })]);
    expect(container.textContent).toContain("chart");
  });

  it("renders a table as a table, cell by cell", () => {
    const { container } = draw([
      shape({
        kind: "table", runs: [], text: "",
        table_rows: 2, table_cols: 2,
        table_cells: { "r0/c0": "Company", "r0/c1": "EV/EBITDA", "r1/c0": "Alpha", "r1/c1": "9.4x" },
      }),
    ]);
    expect(container.querySelectorAll("td")).toHaveLength(4);
    expect(container.querySelector('[data-cell="r1/c1"]')?.textContent).toBe("9.4x");
  });

  it("does not colour text, because it cannot resolve what is behind it", () => {
    // White text on a dark photograph would render white on white and vanish,
    // which reads as the object being absent rather than unrendered.
    const { container } = draw([
      shape({
        runs: [run({ text: "hi", color: "FFFFFF" })],
      }),
    ]);
    const span = container.querySelector("[data-shape-id] span") as HTMLElement;
    expect(span.style.color).toBe("");
  });
});

describe("layout", () => {
  it("claims the scaled size in layout, not the slide's own size", () => {
    // `transform: scale` does not change the space an element occupies, so
    // without this the canvas overflows the stage in both directions.
    const { container } = draw([shape()]);
    const outer = container.firstElementChild as HTMLElement;
    expect(outer.style.width).toContain("var(--canvas-scale");
    expect(outer.style.height).toContain("var(--canvas-scale");
  });

  it("labels itself with the slide it is showing", () => {
    const { container } = draw([shape()]);
    expect(container.querySelector('[role="group"]')?.getAttribute("aria-label")).toBe(
      "Slide 12: Comparables",
    );
  });
});


describe("a shape's lines", () => {
  /**
   * Runs are a formatting split; paragraphs are the line breaks.
   *
   * The runs were laid out directly inside a column flex container, so each one
   * became its own flex item and every run drew on its own line. Measured in
   * Chrome: "Revenue grew " / "15%" / " in FY25" rendered at tops 1, 17 and 33
   * where there should have been one line. jsdom does no layout and could not
   * have shown that, so what is asserted here is the structure the layout
   * follows from: runs of one paragraph share one block, and a new paragraph
   * starts a new one.
   */
  const sentence = (text: string, paragraph: number, bold = false) => run({
    text,
    bold,
    paragraph,
  });

  it("keeps one sentence on one line however many runs it is", () => {
    const { container } = draw([
      shape({
        runs: [
          sentence("Revenue grew ", 0),
          sentence("15%", 0, true),
          sentence(" in FY25", 0),
        ],
      }),
    ]);
    const lines = [...container.querySelectorAll("[data-shape-id] p")];
    expect(lines).toHaveLength(1);
    expect(lines[0]!.querySelectorAll("span")).toHaveLength(3);
    expect(lines[0]!.textContent).toBe("Revenue grew 15% in FY25");
  });

  it("starts a new line at a paragraph boundary", () => {
    const { container } = draw([
      shape({
        runs: [
          sentence("Margin held", 0),
          sentence("Headcount fell", 1),
          sentence("Cash stable", 2),
        ],
      }),
    ]);
    const lines = container.querySelectorAll("[data-shape-id] p");
    expect(lines).toHaveLength(3);
    expect([...lines].map((l) => l.textContent)).toEqual([
      "Margin held",
      "Headcount fell",
      "Cash stable",
    ]);
  });

  it("groups runs by the paragraph they name, not by how many there are", () => {
    const { container } = draw([
      shape({
        runs: [
          sentence("Total ", 0),
          sentence("42", 0, true),
          sentence("as at Q3", 1),
        ],
      }),
    ]);
    const lines = container.querySelectorAll("[data-shape-id] p");
    expect([...lines].map((l) => l.querySelectorAll("span").length)).toEqual([2, 1]);
  });
});

describe("what a run actually looks like", () => {
  /**
   * The canvas claims accuracy and nothing else, and it was drawing five of a
   * run's ten attributes. A struck-through line came back un-struck, an
   * underlined one un-underlined, a footnote marker sitting on the baseline,
   * and — loudest of all — a header reading DIVIDER drawn as "divider",
   * because ALL CAPS is a property of the run and not of the text.
   *
   * Measured across the 26 real decks: 50 underlined runs, 24 with a baseline,
   * 6 struck through. `cap` is in the corpus 363 times and every one is the off
   * state, which is why the adversarial deck had to be given a run that is
   * genuinely capitalised before any of this could be checked.
   */
  const styled = (over: Partial<Run>) =>
    draw([shape({ runs: [run({ text: "Section divider", ...over })] })])
      .container.querySelector("[data-shape-id] span") as HTMLElement;

  it("draws ALL CAPS in capitals", () => {
    expect(styled({ caps: "all" }).style.textTransform).toBe("uppercase");
  });

  it("leaves a run with no capitals setting alone", () => {
    expect(styled({}).style.textTransform).toBe("");
  });

  it("draws an underline", () => {
    expect(styled({ underline: "sng" }).style.textDecorationLine).toContain("underline");
  });

  it("tells a double underline from a single one", () => {
    expect(styled({ underline: "dbl" }).style.textDecorationStyle).toBe("double");
  });

  it("draws a strikethrough", () => {
    expect(styled({ strike: "sngStrike" }).style.textDecorationLine).toContain(
      "line-through",
    );
  });

  it("draws both at once, since CSS takes them on one property", () => {
    const decoration = styled({ underline: "sng", strike: "sngStrike" })
      .style.textDecorationLine;
    expect(decoration).toContain("underline");
    expect(decoration).toContain("line-through");
  });

  it("lifts a superscript and drops a subscript", () => {
    expect(styled({ baseline: 30000 }).style.verticalAlign).toBe("super");
    expect(styled({ baseline: -25000 }).style.verticalAlign).toBe("sub");
  });
});

describe("a line with nothing on it", () => {
  /**
   * `runs` carries only runs with text, so grouping by adjacency drew a shape
   * with a gap in it as a shape without one: everything below the gap moved a
   * line up. 77 of the 768 text shapes in the corpus already have a blank line,
   * and until the applier was fixed every whole-shape edit created more.
   */
  it("keeps the gap a deck has", () => {
    const { container } = draw([
      shape({
        runs: [run({ text: "Above", paragraph: 0 }), run({ text: "Below", paragraph: 2 })],
        paragraph_count: 3,
      }),
    ]);
    const lines = [...container.querySelectorAll("[data-shape-id] p")];
    expect(lines).toHaveLength(3);
    expect(lines.map((l) => l.textContent)).toEqual(["Above", "\u00a0", "Below"]);
  });

  it("keeps a blank line at the end, which no run can imply", () => {
    const { container } = draw([
      shape({ runs: [run({ text: "Only", paragraph: 0 })], paragraph_count: 3 }),
    ]);
    expect(container.querySelectorAll("[data-shape-id] p")).toHaveLength(3);
  });

  it("draws nothing extra when the shape has no blank lines", () => {
    const { container } = draw([
      shape({
        runs: [run({ text: "One", paragraph: 0 }), run({ text: "Two", paragraph: 1 })],
        paragraph_count: 2,
      }),
    ]);
    expect(container.querySelectorAll("[data-shape-id] p")).toHaveLength(2);
  });
});
