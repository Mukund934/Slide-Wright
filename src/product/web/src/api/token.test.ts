/**
 * Every request carries the token, including the one that does not use `request`.
 *
 * There are two `fetch` call sites in this client and for a while only one of
 * them sent the token. `applyStreaming` reads a Server-Sent Events body, so it
 * cannot go through `request`, and it built its own headers object -- which
 * made *apply*, the operation the entire product exists to perform, the single
 * route that returned 401 on a self-hosted deployment while everything around
 * it worked.
 *
 * Nothing in the mocked component tests could have caught that: they mock this
 * module, which is the layer the bug was in. So these tests mock `fetch`
 * instead and read what was actually put on the wire.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, applyStreaming, forget, remember, token } from "./client";

function sent(call: number = 0): Record<string, string> {
  const made = vi.mocked(fetch).mock.calls[call];
  // A missing call means the request under test never happened, which would
  // otherwise surface as a confusing "cannot destructure undefined".
  if (!made) throw new Error(`fetch was not called ${call + 1} time(s)`);
  const [, init] = made;
  return (init?.headers ?? {}) as Record<string, string>;
}

function jsonOnce(body: unknown = {}) {
  vi.mocked(fetch).mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => body,
    body: null,
  } as unknown as Response);
}

beforeEach(() => {
  window.sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the token on the wire", () => {
  it("is absent when there is none, rather than sent empty", async () => {
    // A local deployment has no token. Sending `Bearer ` to find out would be
    // a request that looks like a failed authentication in somebody's logs.
    jsonOnce();
    await api.health();
    expect(sent()).not.toHaveProperty("Authorization");
  });

  it("is sent on an ordinary request once it is held", async () => {
    remember("a-real-token");
    jsonOnce();
    await api.health();
    expect(sent().Authorization).toBe("Bearer a-real-token");
  });

  it("is sent on the apply stream, which does not go through request()", async () => {
    remember("a-real-token");
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () => ({ done: true, value: undefined }),
        }),
      },
    } as unknown as Response);

    await applyStreaming("doc", "note", () => {}).catch(() => {
      // The stream ends with no result, which this rejects. Irrelevant here --
      // what is being asserted is what the request carried.
    });

    expect(sent().Authorization).toBe("Bearer a-real-token");
  });

  it("goes when it is forgotten", async () => {
    remember("a-real-token");
    forget();
    expect(token()).toBeNull();

    jsonOnce();
    await api.health();
    expect(sent()).not.toHaveProperty("Authorization");
  });

  it("is kept for the tab and not for the machine", () => {
    // sessionStorage, not localStorage. A shared team token that outlives the
    // browser session sits on the disk of every machine that ever opened the
    // deployment, including the borrowed one.
    remember("a-real-token");
    expect(window.sessionStorage.getItem("slide-wright.token")).toBe("a-real-token");
    expect(window.localStorage.getItem("slide-wright.token")).toBeNull();
  });

  it("survives storage being unavailable", () => {
    // A private window throws on access. A token that cannot be remembered is
    // a reason to ask for it again, never a reason to fail to start.
    const broken = () => {
      throw new Error("denied");
    };
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(broken);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(broken);

    expect(() => remember("x")).not.toThrow();
    expect(token()).toBeNull();
    vi.restoreAllMocks();
  });
});
