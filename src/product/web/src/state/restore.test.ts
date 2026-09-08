/**
 * Coming back to the deck that was open.
 *
 * The workspace on disk survives a page reload and the client did not: a
 * refresh mid-review returned the user to an empty box to retype an absolute
 * path, with every version and change set still on disk under the deck they had
 * been reading.
 *
 * Two things have to hold and neither is obvious from the happy path. What is
 * stored is the opaque document id and never the deck's path — a filename is
 * the one thing about a deck that is confidential before you open it. And an id
 * that no longer resolves has to land the user on the first-run screen rather
 * than an error, because "the app was restarted" is not a failure they asked
 * about.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SlideDocument } from "../api/types";
import { useWorkspace } from "./workspace";

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  api: { open: vi.fn(), read: vi.fn() },
}));

const { api } = await import("../api/client");

const REMEMBERED = "slide-wright.document";

function document(id: string): SlideDocument {
  return {
    id,
    name: "Pitchbook.pptx",
    workspace: "C:/decks/.slidewright/Pitchbook",
    versions: [],
    deck: { slides: [], slide_width: 12192000, slide_height: 6858000 },
    changeset: null,
    verification: null,
  } as unknown as SlideDocument;
}

beforeEach(() => {
  window.localStorage.clear();
  vi.resetAllMocks();
});

describe("returning to an open document", () => {
  it("remembers the id when a deck is opened", async () => {
    vi.mocked(api.open).mockResolvedValue(document("abc123"));
    const { result } = renderHook(() => useWorkspace());

    await act(() => result.current.open("C:/decks/Pitchbook.pptx"));

    expect(window.localStorage.getItem(REMEMBERED)).toBe("abc123");
  });

  it("stores the id and never the path", async () => {
    vi.mocked(api.open).mockResolvedValue(document("abc123"));
    const { result } = renderHook(() => useWorkspace());

    await act(() => result.current.open("C:/decks/Q3 Pitchbook FINAL.pptx"));

    const stored = JSON.stringify({ ...window.localStorage });
    expect(stored).not.toContain("Pitchbook");
    expect(stored).not.toContain("C:/decks");
  });

  it("reconnects to the remembered document", async () => {
    window.localStorage.setItem(REMEMBERED, "abc123");
    vi.mocked(api.read).mockResolvedValue(document("abc123"));
    const { result } = renderHook(() => useWorkspace());

    await act(() => result.current.restore());

    expect(api.read).toHaveBeenCalledWith("abc123");
    await waitFor(() => expect(result.current.document?.id).toBe("abc123"));
  });

  it("asks for nothing when there is nothing remembered", async () => {
    const { result } = renderHook(() => useWorkspace());

    await act(() => result.current.restore());

    expect(api.read).not.toHaveBeenCalled();
    expect(result.current.phase).toBe("empty");
  });

  it("forgets an id that no longer resolves, without reporting an error", async () => {
    window.localStorage.setItem(REMEMBERED, "gone");
    vi.mocked(api.read).mockRejectedValue(new Error("404"));
    const { result } = renderHook(() => useWorkspace());

    await act(() => result.current.restore());

    await waitFor(() => expect(result.current.phase).toBe("empty"));
    expect(result.current.document).toBeNull();
    expect(result.current.error).toBeNull();
    expect(window.localStorage.getItem(REMEMBERED)).toBeNull();
  });
});
