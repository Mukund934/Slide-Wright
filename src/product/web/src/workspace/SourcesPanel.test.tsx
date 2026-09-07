/**
 * Refresh, tested for the outcome most tools do not have.
 *
 * A wrong number here is invisible — it looks exactly like a right one — so the
 * panel's job is not to report what it changed. It is to make three states
 * distinguishable: the source disagreed, the source agreed, the source could
 * not explain this at all. Collapse those and the user cannot tell "checked and
 * correct" from "never looked at", which is the only thing a refresh is for.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Match, RefreshPlan } from "../api/types";
import { SourcesPanel } from "./SourcesPanel";

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  api: { refreshPreview: vi.fn() },
}));

const { api } = await import("../api/client");

function match(over: Partial<Match> = {}): Match {
  return {
    slide: 3, target: "5/r1/c1", current: "9.4x", proposed: "11.8x",
    citation: "comps.csv!B2", ...over,
  };
}

function plan(over: Partial<RefreshPlan> = {}): RefreshPlan {
  return {
    sources: ["comps.csv"], tables: 1,
    updates: [match()],
    confirmed: [match({ current: "22.1%", proposed: "", citation: "comps.csv!C2" })],
    unmatched: ["slide 4: 'Delta Ltd' — no row in the source"],
    rendered: "", ...over,
  };
}

const handlers = { busy: false, onGoToSlide: vi.fn(), onRefresh: vi.fn() };

async function attach(next: RefreshPlan = plan(), path = "C:\\q4.csv") {
  vi.mocked(api.refreshPreview).mockResolvedValue(next);
  render(<SourcesPanel documentId="doc" {...handlers} />);
  await userEvent.type(screen.getByLabelText(/Path to a \.csv/), path);
  await userEvent.click(screen.getByRole("button", { name: "Attach" }));
  return next;
}

beforeEach(() => vi.clearAllMocks());

describe("before a source is attached", () => {
  it("says how figures are matched, because that is the whole guarantee", () => {
    render(<SourcesPanel documentId="doc" {...handlers} />);
    expect(screen.getByText(/matched by row label and column header/)).toBeInTheDocument();
    expect(screen.getByText(/never by resemblance/)).toBeInTheDocument();
  });

  it("says a source is a path, not an upload", () => {
    render(<SourcesPanel documentId="doc" {...handlers} />);
    expect(screen.getByText(/as confidential as/)).toBeInTheDocument();
  });

  it("offers nothing to propose", () => {
    render(<SourcesPanel documentId="doc" {...handlers} />);
    expect(screen.queryByRole("button", { name: /Propose/ })).toBeNull();
  });
});

describe("the three outcomes stay apart", () => {
  it("counts each of them", async () => {
    const { container } = { container: document.body };
    await attach();
    await screen.findByText(/The source disagrees/i);
    const summary = container.textContent?.replace(/\s+/g, " ") ?? "";
    expect(summary).toContain("1 to update");
    expect(summary).toContain("1 already correct");
    expect(summary).toContain("1 not found");
  });

  it("gives each its own section", async () => {
    await attach();
    expect(await screen.findByText(/The source disagrees/i)).toBeInTheDocument();
    expect(screen.getByText(/The source agrees/i)).toBeInTheDocument();
    expect(screen.getByText(/Not found in the source/i)).toBeInTheDocument();
  });

  it("says a confirmed figure was checked, not skipped", async () => {
    await attach();
    expect(await screen.findByText(/already correct\. Not the same as unchecked/i))
      .toBeInTheDocument();
  });

  it("says an unmatched figure was left alone", async () => {
    await attach();
    expect(await screen.findByText(/Left untouched/i)).toBeInTheDocument();
    expect(screen.getByText(/no row in the source/)).toBeInTheDocument();
  });

  it("hides a section that has nothing in it", async () => {
    await attach(plan({ confirmed: [], unmatched: [] }));
    await screen.findByText(/The source disagrees/i);
    expect(screen.queryByText(/The source agrees/i)).toBeNull();
    expect(screen.queryByText(/Not found in the source/i)).toBeNull();
  });
});

describe("every figure carries its coordinate", () => {
  it("shows the cell an update came from", async () => {
    await attach();
    const updates = (await screen.findByText(/The source disagrees/i))
      .closest("section") as HTMLElement;
    expect(within(updates).getByText("comps.csv!B2")).toBeInTheDocument();
  });

  it("shows one for a confirmed figure too", async () => {
    // The coordinate is what makes "already correct" mean anything.
    await attach();
    const agrees = (await screen.findByText(/The source agrees/i))
      .closest("section") as HTMLElement;
    expect(within(agrees).getByText("comps.csv!C2")).toBeInTheDocument();
  });

  it("shows before and after for an update, and only the value for a confirmation", async () => {
    await attach();
    const agrees = (await screen.findByText(/The source agrees/i))
      .closest("section") as HTMLElement;
    // A confirmed cell proposes nothing; an arrow to an identical value would
    // read as a change nobody asked for.
    expect(within(agrees).queryByText("→")).toBeNull();
  });
});

describe("proposing", () => {
  it("offers the count and says it proposes only", async () => {
    await attach();
    expect(await screen.findByRole("button", { name: /Propose 1 figure/ }))
      .toBeInTheDocument();
    expect(screen.getByText(/You review each figure against its source/))
      .toBeInTheDocument();
  });

  it("offers nothing when the source disagrees with nothing", async () => {
    await attach(plan({ updates: [] }));
    await screen.findByText(/The source agrees/i);
    expect(screen.queryByRole("button", { name: /Propose/ })).toBeNull();
  });

  it("hands the sources upward rather than acting on its own", async () => {
    const onRefresh = vi.fn();
    vi.mocked(api.refreshPreview).mockResolvedValue(plan());
    render(<SourcesPanel documentId="doc" {...handlers} onRefresh={onRefresh} />);
    await userEvent.type(screen.getByLabelText(/Path to a \.csv/), "C:\\q4.csv");
    await userEvent.click(screen.getByRole("button", { name: "Attach" }));
    await userEvent.click(await screen.findByRole("button", { name: /Propose/ }));

    expect(onRefresh).toHaveBeenCalledWith(["C:\\q4.csv"]);
  });
});

describe("when a source cannot be read", () => {
  it("says why and keeps the previous state", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(api.refreshPreview).mockRejectedValue(
      new ApiError(422, "weather.csv could not be read: no header row"),
    );
    render(<SourcesPanel documentId="doc" {...handlers} />);
    await userEvent.type(screen.getByLabelText(/Path to a \.csv/), "C:\\weather.csv");
    await userEvent.click(screen.getByRole("button", { name: "Attach" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("no header row"),
    );
    // The bad path is not silently added to the attached list.
    expect(screen.queryByText("weather.csv")).toBeNull();
  });
});
