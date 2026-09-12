/**
 * Putting a deck down.
 *
 * Every part of this existed and none of them were joined up. The API has had
 * `DELETE /api/documents/{id}` throughout; `api.close` sat in the service layer
 * with no caller; and the `closed` action was dispatched from exactly one
 * place -- the *failure* branch of `restore`, where a document that no longer
 * resolves lands the user on the first-run screen.
 *
 * So there was no way to open a second deck. No control closed the first one,
 * and a reload did not help: the id is remembered on purpose, so the workspace
 * survives a refresh. Opening the wrong file was a dead end until the server
 * forgot the document.
 *
 * Found by driving the built app in a browser rather than by reading it, which
 * is the only way this class of gap shows up: a capability nobody tries to
 * offer is one nobody has checked.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SlideDocument } from "../api/types";
import { useWorkspace } from "./workspace";

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  api: { open: vi.fn(), read: vi.fn(), close: vi.fn() },
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

async function opened(id = "abc123") {
  vi.mocked(api.open).mockResolvedValue(document(id));
  const rendered = renderHook(() => useWorkspace());
  await act(() => rendered.result.current.open("C:/decks/Pitchbook.pptx"));
  return rendered;
}

beforeEach(() => {
  window.localStorage.clear();
  vi.resetAllMocks();
  vi.mocked(api.close).mockResolvedValue({ closed: true });
});

describe("closing a deck", () => {
  it("returns to the screen a deck is opened from", async () => {
    const { result } = await opened();
    expect(result.current.document).not.toBeNull();

    await act(() => result.current.close());

    expect(result.current.document).toBeNull();
    expect(result.current.phase).toBe("empty");
  });

  it("stops remembering it, so a reload does not bring it back", async () => {
    const { result } = await opened("abc123");
    expect(window.localStorage.getItem(REMEMBERED)).toBe("abc123");

    await act(() => result.current.close());

    // The whole reason there was no way out: `restore` reads this on every
    // load, so a deck that is closed but still remembered is a deck that
    // reappears on the next refresh.
    expect(window.localStorage.getItem(REMEMBERED)).toBeNull();
  });

  it("tells the server, so the document does not outlive the screen", async () => {
    const { result } = await opened("abc123");

    await act(() => result.current.close());

    await waitFor(() => expect(api.close).toHaveBeenCalledWith("abc123"));
  });

  it("leaves anyway when the server has already forgotten it", async () => {
    // Restarting the API forgets open documents. Refusing to leave the screen
    // because the leaving failed would be the same dead end with an error on
    // it -- and the state being asked for is the state that already holds.
    const { result } = await opened();
    vi.mocked(api.close).mockRejectedValue(new Error("404"));

    await act(() => result.current.close());

    expect(result.current.document).toBeNull();
    expect(result.current.error).toBeNull();
    expect(window.localStorage.getItem(REMEMBERED)).toBeNull();
  });

  it("clears the screen before waiting on the network", async () => {
    // The deck is gone the moment it is asked for. A close that waits on a
    // round trip looks broken on a slow call, and there is nothing the answer
    // could say that would change the outcome.
    const { result } = await opened();
    let release: (value: { closed: boolean }) => void = () => {};
    vi.mocked(api.close).mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );

    let finished = false;
    await act(async () => {
      void result.current.close().then(() => {
        finished = true;
      });
    });

    expect(result.current.document).toBeNull();
    expect(finished).toBe(false);

    await act(async () => {
      release({ closed: true });
    });
  });

  it("forgets the locks with the deck", async () => {
    // Locks are session state about *this* deck. Carrying "nothing on slide 4
    // may change" onto the next one would be a guarantee the user never made,
    // silently applied to a document they have not seen.
    const { result } = await opened();
    act(() => result.current.lock({ scope: "slide", target: "4", reason: "" }));
    expect(result.current.locks).toHaveLength(1);

    await act(() => result.current.close());

    expect(result.current.locks).toHaveLength(0);
  });
});
