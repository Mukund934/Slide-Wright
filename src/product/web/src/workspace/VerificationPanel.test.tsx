/**
 * The trust surface, tested for what it must never say.
 *
 * One rule under all of these: **no green state unless the engine said
 * deliverable.** If this component can be made to show a pass on a blocked
 * result — by a truthy-looking field, an empty reasons array, a high fidelity
 * score — then the product's only real claim is decorative.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Verification } from "../api/types";
import { VerificationPanel } from "./VerificationPanel";

function verification(over: Partial<Verification> = {}): Verification {
  return {
    deliverable: true, identical_parts: 104, total_parts: 105, fidelity_score: 99.05,
    changed_parts: ["ppt/slides/slide5.xml"], changed_slides: [5], untouched_slides: 17,
    unrequested_slides: [], unrequested_parts: [], blocking_reasons: [],
    requested: [{ slide: 5, description: "set text", target: "3" }],
    census: [
      { label: "native tables", source: 6, output: 6, intact: true },
      { label: "chart parts", source: 2, output: 2, intact: true },
    ],
    rendered: "",
    ...over,
  };
}

const inert = { progress: [], applying: false, onGoToSlide: vi.fn() };

describe("a delivered result", () => {
  it("states the part count in the engine's own terms", () => {
    render(<VerificationPanel {...inert} verification={verification()} />);
    expect(screen.getByText(/104 of 105/)).toBeInTheDocument();
    expect(screen.getByText(/99\.05%/)).toBeInTheDocument();
  });

  it("says how much was left alone, not only what changed", () => {
    render(<VerificationPanel {...inert} verification={verification()} />);
    expect(screen.getByText(/17 slides untouched/)).toBeInTheDocument();
    expect(screen.getByText(/0 unexpected/)).toBeInTheDocument();
  });

  it("shows Verified", () => {
    render(<VerificationPanel {...inert} verification={verification()} />);
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.queryByText("Not delivered")).not.toBeInTheDocument();
  });
});

describe("a blocked result", () => {
  const blocked = verification({
    deliverable: false,
    blocking_reasons: ["unrequested changes on slide(s) 4, 9"],
    unrequested_slides: [4, 9],
  });

  it("never shows a pass, however good the other numbers look", () => {
    render(<VerificationPanel {...inert} verification={blocked} />);
    expect(screen.getByText("Not delivered")).toBeInTheDocument();
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();
  });

  it("says the original is untouched, because that is the reassurance", () => {
    render(<VerificationPanel {...inert} verification={blocked} />);
    expect(screen.getByText(/Your original is untouched/)).toBeInTheDocument();
  });

  it("repeats the engine's reason verbatim rather than paraphrasing it", () => {
    render(<VerificationPanel {...inert} verification={blocked} />);
    expect(screen.getByText("unrequested changes on slide(s) 4, 9")).toBeInTheDocument();
  });

  it("offers the unaccounted-for slides as somewhere to look", () => {
    render(<VerificationPanel {...inert} verification={blocked} />);
    expect(screen.getByRole("button", { name: "slide 4" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "slide 9" })).toBeInTheDocument();
  });

  it("still shows a blocked verdict when no reason came back", () => {
    // Defensive: an empty reasons list must not fall through to the delivered
    // branch. `deliverable` is the verdict; the reasons are the explanation.
    render(<VerificationPanel {...inert} verification={verification({ deliverable: false })} />);
    expect(screen.getByText("Not delivered")).toBeInTheDocument();
  });
});

describe("the census", () => {
  it("marks a native object loss and nothing else", () => {
    const lossy = verification({
      deliverable: false,
      blocking_reasons: ["native object loss: tables: 6 -> 4"],
      census: [
        { label: "native tables", source: 6, output: 4, intact: false },
        { label: "chart parts", source: 2, output: 2, intact: true },
      ],
    });
    render(<VerificationPanel {...inert} verification={lossy} />);
    expect(screen.getByText("6 → 4")).toBeInTheDocument();
    expect(screen.getByText("2 → 2")).toBeInTheDocument();
  });
});

describe("while applying", () => {
  it("narrates the stages the engine reported", () => {
    render(
      <VerificationPanel
        {...inert}
        applying
        verification={null}
        progress={[
          { stage: "applying", detail: "", slides: [5] },
          { stage: "verifying", detail: "" },
        ]}
      />,
    );
    expect(screen.getByText(/Writing the approved changes/)).toBeInTheDocument();
    expect(screen.getByText(/Comparing every part against your original/)).toBeInTheDocument();
  });

  it("shows no percentage anywhere", () => {
    // The engine does not know how long a stage takes. A bar would be invented,
    // and invented confidence is the thing this product replaces.
    const { container } = render(
      <VerificationPanel
        {...inert}
        applying
        verification={null}
        progress={[{ stage: "applying", detail: "" }]}
      />,
    );
    expect(container.textContent).not.toMatch(/\d+%/);
    expect(container.querySelector("progress")).toBeNull();
  });

  it("says a large deck takes minutes rather than implying it is nearly done", () => {
    render(
      <VerificationPanel {...inert} applying verification={null} progress={[]} />,
    );
    expect(screen.getByText(/takes minutes/)).toBeInTheDocument();
  });
});

describe("before anything has been applied", () => {
  it("renders nothing at all", () => {
    const { container } = render(<VerificationPanel {...inert} verification={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
