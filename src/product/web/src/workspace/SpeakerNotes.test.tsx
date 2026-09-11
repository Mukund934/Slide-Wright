/**
 * The words under the slide, and the one thing this component must not do:
 * let a reviewer conclude a slide is untouched when its script was rewritten.
 *
 * The canvas outlines changed shapes. Notes belong to no shape, so a rewritten
 * script would otherwise sit under an unmarked slide looking exactly like an
 * untouched one — which is the failure the whole surface exists to prevent,
 * arriving through the one part of a slide the canvas cannot draw.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SpeakerNotes } from "./SpeakerNotes";

describe("SpeakerNotes", () => {
  it("draws nothing when the slide has no script", () => {
    const { container } = render(<SpeakerNotes notes="" />);
    expect(container).toBeEmptyDOMElement();
  });

  // A JSX attribute string does not process escapes, so these pass the real
  // newline through braces. The first version of this file did not, and the
  // whitespace case was asserting about a literal backslash.
  it("draws nothing when the script is only whitespace", () => {
    const { container } = render(<SpeakerNotes notes={"   \n  "} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows what the presenter wrote", () => {
    render(<SpeakerNotes notes={"Tashi:\nIn this section we explain the data."} />);
    expect(screen.getByText(/In this section we explain the data/)).toBeInTheDocument();
  });

  it("keeps the lines apart, because a script is paragraphs", () => {
    const { container } = render(<SpeakerNotes notes={"First line\nSecond line"} />);
    const lines = [...container.querySelectorAll("p")].map((p) => p.textContent);
    expect(lines).toEqual(["First line", "Second line"]);
  });

  it("says so when the script changed in this version", () => {
    render(<SpeakerNotes notes="Rewritten." changed />);
    expect(screen.getByText(/changed in this version/)).toBeInTheDocument();
  });

  it("says nothing about change when nothing changed", () => {
    render(<SpeakerNotes notes="As written." />);
    expect(screen.queryByText(/changed in this version/)).not.toBeInTheDocument();
  });

  it("is a landmark, so a screen reader can reach it without hunting", () => {
    render(<SpeakerNotes notes="Anything at all." />);
    expect(screen.getByRole("region", { name: /speaker notes/i })).toBeInTheDocument();
  });

  it("bounds itself and scrolls, rather than pushing the slide around", () => {
    const long = Array.from({ length: 40 }, (_, i) => `Line ${i}`).join("\n");
    const { container } = render(<SpeakerNotes notes={long} />);
    const region = container.querySelector("section")!;
    expect(region.className).toContain("overflow-y-auto");
    expect(region.className).toMatch(/max-h-/);
  });
});
