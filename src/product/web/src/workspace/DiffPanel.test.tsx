/**
 * The visual diff, tested for the distinction it exists to make.
 *
 * Verification and diff answer different questions, and the product's value
 * depends on them staying apart. "104 of 105 parts identical" is true and tells
 * you nothing about whether a figure moved; "0 changes what the deck says" is
 * the sentence someone actually needs. If this panel ever presents a formatting
 * change and a rewritten number as the same kind of thing, the wedge is gone.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Deck, Delta } from "../api/types";
import type { Comparison } from "../state/workspace";
import { DiffPanel } from "./DiffPanel";

const EMPTY_DECK: Deck = {
  slide_width: 12192000, slide_height: 6858000, theme_fonts: {}, slides: [],
};

function delta(over: Partial<Delta> = {}): Delta {
  return {
    slide: 3, shape_id: "5", kind: "formatting",
    description: "Title 1 (id=3) run 1 font 'Century Gothic' -> '+mn-lt'",
    is_content: false, changes_figures: false, ...over,
  };
}

function comparison(deltas: Delta[], over: Partial<Comparison> = {}): Comparison {
  return {
    from: 0, to: 1, before: EMPTY_DECK, after: EMPTY_DECK, deltas,
    showing: "after", blend: 1, ...over,
  };
}

const handlers = { onGoTo: vi.fn(), onFlip: vi.fn(), onBlend: vi.fn(), onClose: vi.fn() };

describe("the two questions are kept apart", () => {
  const mixed = comparison([
    delta({ kind: "text", is_content: true, changes_figures: true,
            description: "Table 2 text 9.4 -> 11.8" }),
    delta({ kind: "formatting", is_content: false }),
    delta({ kind: "geometry", is_content: false, description: "Box moved 0.01in right" }),
  ]);

  it("leads with the figures, then counts content and presentation", () => {
    // "No figure changed" is the sharpest claim the product makes and the one
    // someone asking for a formatting pass actually wants. The summary
    // interpolates its counts, so it is matched on the assembled text.
    const { container } = render(<DiffPanel {...handlers} comparison={mixed} />);
    const summary = container.textContent?.replace(/\s+/g, " ") ?? "";
    expect(summary).toContain("1 figure changed");
    expect(summary).toContain("1 in what it says");
    expect(summary).toContain("2 in how it looks");
  });

  it("says no figure changed rather than leaving a zero to be inferred", () => {
    // A zero here is the whole point. A reader who has to notice an absence
    // has not been told anything.
    const noFigures = comparison([
      delta({ kind: "formatting", is_content: false, changes_figures: false }),
      delta({ kind: "text", is_content: true, changes_figures: false,
              description: "teh -> the" }),
    ]);
    const { container } = render(<DiffPanel {...handlers} comparison={noFigures} />);
    expect(container.textContent).toContain("no figure changed");
  });

  it("marks a row whose figure moved, in words", () => {
    render(
      <DiffPanel
        {...handlers}
        comparison={comparison([
          delta({ kind: "text", is_content: true, changes_figures: true,
                  description: "9.4 -> 11.8" }),
        ])}
      />,
    );
    expect(screen.getByText("figure")).toBeInTheDocument();
  });

  it("puts them in different sections", () => {
    render(<DiffPanel {...handlers} comparison={mixed} />);
    const said = screen.getByText(/Changes what the deck says/).closest("section") as HTMLElement;
    expect(within(said).getByText(/9\.4 -> 11\.8/)).toBeInTheDocument();
    expect(within(said).queryByText(/Century Gothic/)).toBeNull();
  });

  it("says plainly that a content change means a tidy failed", () => {
    render(<DiffPanel {...handlers} comparison={mixed} />);
    expect(screen.getByText(/A tidy that produced one of these has failed/))
      .toBeInTheDocument();
  });

  it("shows only the section that has anything in it", () => {
    render(<DiffPanel {...handlers} comparison={comparison([delta()])} />);
    expect(screen.getByText(/Changes how it looks/)).toBeInTheDocument();
    expect(screen.queryByText(/Changes what the deck says/)).toBeNull();
  });

  it("does not decide for itself what counts as content", () => {
    // `is_content` is the engine's answer. A surface with its own opinion would
    // be a second verdict on the only claim the product makes.
    const surprising = comparison([
      delta({ kind: "formatting", is_content: true, description: "engine says content" }),
    ]);
    render(<DiffPanel {...handlers} comparison={surprising} />);
    const said = screen.getByText(/Changes what the deck says/).closest("section") as HTMLElement;
    expect(within(said).getByText(/engine says content/)).toBeInTheDocument();
  });
});

describe("two versions that read the same", () => {
  it("says so without claiming their bytes match", () => {
    // Byte equality is verification's question, not this one, and conflating
    // them would have the diff answering for the guarantee.
    render(<DiffPanel {...handlers} comparison={comparison([])} />);
    expect(screen.getByText(/These two versions read the same/)).toBeInTheDocument();
    expect(screen.getByText(/Their bytes may still differ/)).toBeInTheDocument();
  });
});

describe("the flip", () => {
  it("names the versions rather than 'before' and 'after'", () => {
    render(<DiffPanel {...handlers} comparison={comparison([delta()])} />);
    expect(screen.getByRole("button", { name: "v000" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "v001" })).toBeInTheDocument();
  });

  it("marks which side is showing", () => {
    render(<DiffPanel {...handlers} comparison={comparison([delta()], { showing: "before" })} />);
    expect(screen.getByRole("button", { name: "v000" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "v001" })).toHaveAttribute("aria-pressed", "false");
  });

  it("flips when the other side is chosen", async () => {
    const onFlip = vi.fn();
    render(<DiffPanel {...handlers} onFlip={onFlip} comparison={comparison([delta()])} />);
    await userEvent.click(screen.getByRole("button", { name: "v000" }));
    expect(onFlip).toHaveBeenCalledOnce();
  });

  it("does nothing when the side already showing is chosen again", async () => {
    const onFlip = vi.fn();
    render(<DiffPanel {...handlers} onFlip={onFlip} comparison={comparison([delta()])} />);
    await userEvent.click(screen.getByRole("button", { name: "v001" }));
    expect(onFlip).not.toHaveBeenCalled();
  });
});

describe("navigation", () => {
  it("makes every delta a way to the object it describes", async () => {
    const onGoTo = vi.fn();
    const target = delta({ slide: 12, shape_id: "9" });
    render(<DiffPanel {...handlers} onGoTo={onGoTo} comparison={comparison([target])} />);

    await userEvent.click(screen.getByRole("button", { name: /Go to slide 12/ }));
    expect(onGoTo).toHaveBeenCalledWith(target);
  });

  it("orders deltas by slide so the list reads in deck order", () => {
    render(
      <DiffPanel
        {...handlers}
        comparison={comparison([
          delta({ slide: 9, description: "on nine" }),
          delta({ slide: 2, description: "on two" }),
        ])}
      />,
    );
    const rows = screen.getAllByRole("button", { name: /Go to slide/ });
    expect(rows[0]).toHaveAccessibleName(/slide 2/);
    expect(rows[1]).toHaveAccessibleName(/slide 9/);
  });
});

describe("leaving", () => {
  it("can be closed", async () => {
    const onClose = vi.fn();
    render(<DiffPanel {...handlers} onClose={onClose} comparison={comparison([delta()])} />);
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe("the blend", () => {
  it("is a control the reader drags, not a transition the interface plays", () => {
    // An automatic crossfade hides a difference: the eye follows the fade
    // instead of the change. A slider lets them rock over the one spot they
    // are unsure about, at whatever rate finds it.
    render(<DiffPanel {...handlers} comparison={comparison([delta()])} />);
    const slider = screen.getByRole("slider");
    expect(slider).toHaveValue("1");
    expect(slider).toHaveAccessibleName(/v000 and v001/);
  });

  it("reports the mixture in words for a screen reader", () => {
    render(
      <DiffPanel {...handlers} comparison={comparison([delta()], { blend: 0.4 })} />,
    );
    expect(screen.getByRole("slider")).toHaveAttribute("aria-valuetext", "40% v001");
  });

  it("hands each new value upward", async () => {
    const onBlend = vi.fn();
    render(<DiffPanel {...handlers} onBlend={onBlend} comparison={comparison([delta()])} />);
    fireEvent.change(screen.getByRole("slider"), { target: { value: "0.3" } });
    expect(onBlend).toHaveBeenCalledWith(0.3);
  });
});
