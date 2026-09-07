/**
 * The filmstrip, tested as a keyboard surface.
 *
 * It is the longest list in the product — one row per slide, no pagination on
 * purpose — which makes it the place where a tab-order mistake costs the most.
 * It held a `<button>` inside every `role="option"`: invalid, because an
 * option's children must be presentational, and expensive, because each one was
 * a tab stop. Reaching the canvas by keyboard meant tabbing past every slide
 * first. Twenty-seven stops on a 26-slide deck; fifty-three on the 52-slide
 * fixture.
 *
 * The listbox itself already had the right pattern — one tab stop,
 * `aria-activedescendant`, arrows and Home/End. This is what stops the two
 * halves drifting apart again.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Slide } from "../api/types";
import { Filmstrip } from "./Filmstrip";

function slides(count: number): Slide[] {
  return Array.from({ length: count }, (_, i) => ({
    number: i + 1,
    title: `Slide ${i + 1}`,
    shapes: [],
    part_name: `ppt/slides/slide${i + 1}.xml`,
    word_count: 0,
  })) as unknown as Slide[];
}

function show(over: Partial<Parameters<typeof Filmstrip>[0]> = {}) {
  const onSelect = vi.fn();
  render(
    <Filmstrip
      slides={slides(26)}
      selected={1}
      changed={new Set<number>()}
      onSelect={onSelect}
      {...over}
    />,
  );
  return { onSelect };
}

describe("the whole list is one tab stop", () => {
  it("does not put every slide in the tab order", () => {
    show();
    const stops = screen
      .getByRole("listbox")
      .querySelectorAll('a[href],button,input,[tabindex]:not([tabindex="-1"])');
    expect(stops).toHaveLength(0);
  });

  it("is reachable by keyboard itself", async () => {
    show();
    await userEvent.tab();
    expect(screen.getByRole("listbox")).toHaveFocus();
  });

  it("has no interactive descendants inside an option", () => {
    // An option's children must be presentational. This is the invalid half,
    // asserted separately from the tab-order half, because a fix that only set
    // tabindex="-1" would pass the test above and still be wrong.
    show();
    for (const option of screen.getAllByRole("option")) {
      expect(option.querySelector("button, a, input")).toBeNull();
    }
  });
});

describe("moving through it", () => {
  it("selects the next slide on ArrowDown", async () => {
    const { onSelect } = show({ selected: 3 });
    screen.getByRole("listbox").focus();
    await userEvent.keyboard("{ArrowDown}");
    expect(onSelect).toHaveBeenCalledWith(4);
  });

  it("selects the previous one on ArrowUp", async () => {
    const { onSelect } = show({ selected: 3 });
    screen.getByRole("listbox").focus();
    await userEvent.keyboard("{ArrowUp}");
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it("goes to the ends on Home and End", async () => {
    const { onSelect } = show({ selected: 10 });
    screen.getByRole("listbox").focus();
    await userEvent.keyboard("{Home}");
    expect(onSelect).toHaveBeenCalledWith(1);
    await userEvent.keyboard("{End}");
    expect(onSelect).toHaveBeenCalledWith(26);
  });

  it("stops at the ends rather than wrapping", async () => {
    // Wrapping in a list this long loses the reader's place, and the list is
    // the one thing telling them how much of the deck is untouched.
    const { onSelect } = show({ selected: 1 });
    screen.getByRole("listbox").focus();
    await userEvent.keyboard("{ArrowUp}");
    expect(onSelect).toHaveBeenCalledWith(1);
  });

  it("still selects on a click", async () => {
    const { onSelect } = show();
    await userEvent.click(screen.getAllByRole("option")[6]!);
    expect(onSelect).toHaveBeenCalledWith(7);
  });
});

describe("what it announces", () => {
  it("marks the selected slide and only that one", () => {
    show({ selected: 4 });
    const selected = screen
      .getAllByRole("option")
      .filter((o) => o.getAttribute("aria-selected") === "true");
    expect(selected).toHaveLength(1);
    expect(selected[0]).toHaveTextContent("4");
  });

  it("points the listbox at its active option", () => {
    show({ selected: 9 });
    expect(screen.getByRole("listbox")).toHaveAttribute(
      "aria-activedescendant",
      "slide-9",
    );
  });

  it("names itself, because a bare list of numbers is not a name", () => {
    show();
    expect(screen.getByRole("listbox", { name: "Slides" })).toBeInTheDocument();
  });
});
