/**
 * The token screen, at every point in the interaction rather than at rest.
 *
 * The defect worth guarding against is not a styling one. It is accepting a
 * token without checking it: the obvious implementation stores whatever was
 * typed and lets the *next* call fail, which produces a workspace that loads
 * and then breaks on the first action — the worst possible place to discover a
 * wrong token, because by then the user believes they are in.
 *
 * So the interesting assertions here are about what is stored and when.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import { Unlock } from "./Unlock";

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, api: { health: vi.fn() } };
});

const { api, token } = await import("../api/client");

beforeEach(() => {
  window.sessionStorage.clear();
  vi.resetAllMocks();
});

async function type(value: string) {
  await userEvent.type(screen.getByLabelText(/access token/i), value);
}

describe("unlocking a shared deployment", () => {
  it("asks for the token and nothing else", () => {
    render(<Unlock onUnlocked={vi.fn()} />);

    expect(screen.getByLabelText(/access token/i)).toBeInTheDocument();
    // No accounts exist here, and implying one would promise an identity the
    // product cannot show.
    expect(screen.queryByText(/sign in|log in|username|email/i)).toBeNull();
  });

  it("will not submit an empty token", async () => {
    render(<Unlock onUnlocked={vi.fn()} />);
    expect(screen.getByRole("button", { name: /unlock/i })).toBeDisabled();
  });

  it("lets the caller in once a real request has succeeded with the token", async () => {
    vi.mocked(api.health).mockResolvedValue({} as never);
    const unlocked = vi.fn();
    render(<Unlock onUnlocked={unlocked} />);

    await type("a-token-that-works");
    await userEvent.click(screen.getByRole("button", { name: /unlock/i }));

    await waitFor(() => expect(unlocked).toHaveBeenCalled());
    expect(token()).toBe("a-token-that-works");
  });

  it("does not keep a token the server rejected", async () => {
    // The point of the whole screen. A rejected token left in storage is sent
    // again on the next reload and fails again, with no field on screen to
    // correct it.
    vi.mocked(api.health).mockRejectedValue(new ApiError(401, "nope"));
    const unlocked = vi.fn();
    render(<Unlock onUnlocked={unlocked} />);

    await type("wrong");
    await userEvent.click(screen.getByRole("button", { name: /unlock/i }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/not accepted/i));
    expect(token()).toBeNull();
    expect(unlocked).not.toHaveBeenCalled();
  });

  it("tells a rejection apart from a server that did not answer", async () => {
    // "Check it with whoever runs this server" sends somebody hunting for a
    // credential. If the box is simply down, that is a wasted errand.
    vi.mocked(api.health).mockRejectedValue(new ApiError(0, "not running"));
    render(<Unlock onUnlocked={vi.fn()} />);

    await type("probably-fine");
    await userEvent.click(screen.getByRole("button", { name: /unlock/i }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/did not answer/i),
    );
  });

  it("clears the complaint as soon as the token is edited", async () => {
    // The rejection described the previous attempt. Leaving it up while
    // somebody retypes reads as though the new value is already wrong.
    vi.mocked(api.health).mockRejectedValue(new ApiError(401, "nope"));
    render(<Unlock onUnlocked={vi.fn()} />);

    await type("wrong");
    await userEvent.click(screen.getByRole("button", { name: /unlock/i }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/not accepted/i));

    await type("2");
    expect(screen.getByRole("status")).toHaveTextContent("");
  });

  it("announces the outcome to a screen reader", async () => {
    vi.mocked(api.health).mockRejectedValue(new ApiError(401, "nope"));
    render(<Unlock onUnlocked={vi.fn()} />);

    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");

    await type("wrong");
    await userEvent.click(screen.getByRole("button", { name: /unlock/i }));
    await waitFor(() =>
      expect(screen.getByLabelText(/access token/i)).toHaveAttribute("aria-invalid", "true"),
    );
  });

  it("does not send the token twice while the first attempt is in flight", async () => {
    // A double-click on a slow link would otherwise make two requests, and the
    // second one lands after the first has already decided.
    let release: () => void = () => {};
    vi.mocked(api.health).mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve({} as never);
      }),
    );
    render(<Unlock onUnlocked={vi.fn()} />);

    await type("slow");
    const button = screen.getByRole("button", { name: /unlock/i });
    await userEvent.click(button);

    expect(button).toBeDisabled();
    expect(api.health).toHaveBeenCalledTimes(1);
    release();
  });

  it("does not put the token in the DOM in clear text", async () => {
    render(<Unlock onUnlocked={vi.fn()} />);
    expect(screen.getByLabelText(/access token/i)).toHaveAttribute("type", "password");
  });
});
