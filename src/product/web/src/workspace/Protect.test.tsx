/**
 * Protection, tested for the promise it makes.
 *
 * A lock is worth more than a sentence in a prompt only if it is a constraint
 * the engine enforces, it stands across proposals, and its refusal is visible.
 * The interface's share of that is narrow but total: it must offer the specific
 * scopes, report what is held accurately, and never claim something is
 * protected that was not sent.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { LockSpec, Shape } from "../api/types";
import { Protect, ProtectedList } from "./Protect";

function shape(over: Partial<Shape> = {}): Shape {
  return {
    id: "7", name: "Comparables", kind: "table", placeholder_type: null,
    x: 0, y: 0, cx: 100, cy: 100, rotation_deg: null,
    geometry_inherited: false, geometry: null, runs: [],
    table_rows: 2, table_cols: 2, table_cells: {}, child_count: 0, text: "",
    ...over,
  };
}

function mount(locks: LockSpec[] = [], target: Shape | null = shape()) {
  const onLock = vi.fn();
  const onUnlock = vi.fn();
  render(
    <Protect slide={4} shape={target} locks={locks} onLock={onLock} onUnlock={onUnlock} />,
  );
  return { onLock, onUnlock };
}

describe("the scopes people actually ask for", () => {
  it("offers this slide and this object, not only the deck", () => {
    // The engine's own Lock docstring gives "leave slide 4 alone, the partner
    // signed it off" as the motivating example, and for a long time that was
    // the one guarantee the interface could not express.
    mount();
    expect(screen.getByRole("button", { name: /slide 4/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /this table/ })).toBeInTheDocument();
  });

  it("names the object by what it is", () => {
    mount([], shape({ kind: "chart" }));
    expect(screen.getByRole("button", { name: /this chart/ })).toBeInTheDocument();
  });

  it("offers no object scope when nothing is selected", () => {
    mount([], null);
    expect(screen.queryByRole("button", { name: /^· ?this/ })).toBeNull();
    expect(screen.getByRole("button", { name: /slide 4/ })).toBeInTheDocument();
  });

  it("still offers the deck-wide scopes", () => {
    mount();
    for (const scope of ["numbers", "wording", "layout", "formatting"]) {
      expect(screen.getByRole("button", { name: new RegExp(scope) })).toBeInTheDocument();
    }
  });
});

describe("locking and unlocking", () => {
  it("locks the slide it is looking at, by number", async () => {
    const { onLock } = mount();
    await userEvent.click(screen.getByRole("button", { name: /slide 4/ }));
    expect(onLock).toHaveBeenCalledWith({ scope: "slide", target: "4" });
  });

  it("locks the selected object, by id", async () => {
    const { onLock } = mount([], shape({ id: "9" }));
    await userEvent.click(screen.getByRole("button", { name: /this table/ }));
    expect(onLock).toHaveBeenCalledWith({ scope: "shape", target: "9" });
  });

  it("lifts a lock that is already held rather than adding a second", async () => {
    const { onLock, onUnlock } = mount([{ scope: "numbers" }]);
    await userEvent.click(screen.getByRole("button", { name: /numbers/ }));
    expect(onUnlock).toHaveBeenCalledWith({ scope: "numbers" });
    expect(onLock).not.toHaveBeenCalled();
  });

  it("distinguishes a lock on this object from one on another", () => {
    mount([{ scope: "shape", target: "999" }], shape({ id: "7" }));
    expect(screen.getByRole("button", { name: /this table/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });
});

describe("a held lock is legible without colour", () => {
  it("marks state with aria-pressed and a glyph, not hue alone", () => {
    // WCAG 1.4.1, and the research names colour independence a procurement
    // requirement for the buyers this product is aimed at.
    mount([{ scope: "wording" }]);
    const held = screen.getByRole("button", { name: /wording/ });
    expect(held).toHaveAttribute("aria-pressed", "true");
    expect(held.textContent).toMatch(/·/);

    const loose = screen.getByRole("button", { name: /layout/ });
    expect(loose).toHaveAttribute("aria-pressed", "false");
    expect(loose.textContent).not.toMatch(/·/);
  });
});

describe("what is currently held", () => {
  it("lists every lock so it can be found and lifted", () => {
    // A lock nobody can find is a lock nobody can lift, and a forgotten
    // guarantee is how a later proposal comes back mysteriously empty.
    const onUnlock = vi.fn();
    render(
      <ProtectedList
        locks={[{ scope: "numbers" }, { scope: "slide", target: "4" }]}
        onUnlock={onUnlock}
      />,
    );
    const list = screen.getByRole("list");
    expect(within(list).getByText(/numbers/)).toBeInTheDocument();
    expect(within(list).getByText(/slide/)).toBeInTheDocument();
    expect(within(list).getByText("4")).toBeInTheDocument();
  });

  it("lifts the one that is clicked", async () => {
    const onUnlock = vi.fn();
    render(<ProtectedList locks={[{ scope: "slide", target: "4" }]} onUnlock={onUnlock} />);
    await userEvent.click(screen.getByRole("button"));
    expect(onUnlock).toHaveBeenCalledWith({ scope: "slide", target: "4" });
  });

  it("shows nothing at all when nothing is protected", () => {
    const { container } = render(<ProtectedList locks={[]} onUnlock={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
