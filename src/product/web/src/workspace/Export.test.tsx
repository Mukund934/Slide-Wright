/**
 * The last door, tested for the one thing it must never open.
 *
 * A deck that failed verification must not leave the session by any route. The
 * engine refuses; this checks that the surface does not quietly provide a way
 * around, and that the refusal arrives before the user types a path rather than
 * after — a verdict about the deck must not read as a mistake in their typing.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ExportTarget } from "../api/types";
import { Export } from "./Export";

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  api: { exportTarget: vi.fn(), export: vi.fn() },
}));

const { api } = await import("../api/client");

function target(over: Partial<ExportTarget> = {}): ExportTarget {
  return {
    suggested: "C:\\decks\\Pitchbook-v001.pptx",
    deliverable: true,
    blocking_reasons: [],
    version: 1,
    ...over,
  };
}

async function open(next: ExportTarget = target()) {
  vi.mocked(api.exportTarget).mockResolvedValue(next);
  render(<Export documentId="doc" version={next.version} />);
  await userEvent.click(screen.getByRole("button", { name: "Export" }));
}

beforeEach(() => vi.clearAllMocks());

describe("a deliverable version", () => {
  it("offers the suggestion already filled in", async () => {
    await open();
    await waitFor(() =>
      expect(screen.getByLabelText(/Write this version to/)).toHaveValue(
        "C:\\decks\\Pitchbook-v001.pptx",
      ),
    );
  });

  it("says it will not overwrite the original, and why", async () => {
    await open();
    expect(await screen.findByText(/never over it/)).toBeInTheDocument();
    expect(screen.getByText(/every verification compares against/)).toBeInTheDocument();
  });

  it("writes where asked and reports the path", async () => {
    vi.mocked(api.export).mockResolvedValue({ path: "C:\\out\\final.pptx" });
    await open();
    await screen.findByLabelText(/Write this version to/);
    await userEvent.click(screen.getByRole("button", { name: "Write" }));

    await waitFor(() => expect(screen.getByText(/wrote C:\\out\\final\.pptx/)).toBeInTheDocument());
  });
});

describe("a version that failed verification", () => {
  const blocked = target({
    deliverable: false,
    blocking_reasons: ["unrequested changes on slide(s) 4", "native object loss: tables: 6 -> 4"],
  });

  it("refuses before offering the field, not after taking it", async () => {
    // Rejecting a submission would be technically identical and read as a typo.
    await open(blocked);
    expect(await screen.findByText("Not deliverable")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Write this version to/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Write" })).toBeNull();
  });

  it("says the original is untouched, because that is the reassurance", async () => {
    await open(blocked);
    expect(await screen.findByText(/Your\s+original is untouched/)).toBeInTheDocument();
  });

  it("repeats the engine's reasons verbatim", async () => {
    await open(blocked);
    expect(await screen.findByText("unrequested changes on slide(s) 4")).toBeInTheDocument();
    expect(screen.getByText("native object loss: tables: 6 -> 4")).toBeInTheDocument();
  });

  it("never calls export at all", async () => {
    await open(blocked);
    await screen.findByText("Not deliverable");
    expect(vi.mocked(api.export)).not.toHaveBeenCalled();
  });
});

describe("when the engine refuses the write itself", () => {
  it("shows the refusal verbatim rather than a friendlier untruth", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(api.export).mockRejectedValue(
      new ApiError(422, "That is the file you opened. Slide-Wright will not overwrite your original"),
    );
    await open();
    await screen.findByLabelText(/Write this version to/);
    await userEvent.click(screen.getByRole("button", { name: "Write" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/will not overwrite your original/),
    );
  });
});

describe("closed by default", () => {
  it("shows nothing until asked", () => {
    render(<Export documentId="doc" version={1} />);
    expect(screen.queryByLabelText(/Write this version to/)).toBeNull();
    // And it does not ask the engine anything either.
    expect(vi.mocked(api.exportTarget)).not.toHaveBeenCalled();
  });
});
