/**
 * The token screen for a self-hosted deployment.
 *
 * Only ever reached when `/api/ping` says a token is required (ADR-0011), which
 * a local install never does. Somebody who installed this on their own laptop
 * will not see it exist.
 *
 * Two decisions worth stating, because both are about honesty rather than
 * polish.
 *
 * **It does not pretend to be a login.** There are no accounts here and no user
 * to be. One shared token, issued by whoever runs the server, is what this
 * deployment has, and calling it "sign in" would imply an identity the product
 * does not have and cannot show. It says what it is.
 *
 * **It verifies before it accepts.** The obvious implementation stores whatever
 * is typed and lets the next call fail, which produces a workspace that loads
 * and then breaks on the first action — the worst place to discover a wrong
 * token. So the token is stored, `health` is called, and a rejection puts the
 * field back with the reason. Nothing else in the app runs until a real request
 * has succeeded with it.
 */

import { motion } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { ApiError, api, forget, remember } from "../api/client";
import { Button } from "../design/primitives";
import { enter } from "../motion/tokens";

type Phase = "idle" | "checking" | "rejected" | "unreachable";

export function Unlock({ onUnlocked }: { onUnlocked: () => void }) {
  const [value, setValue] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const field = useRef<HTMLInputElement>(null);

  // The only thing on the screen, so it is where the cursor belongs. A user
  // who has just pasted a token from a password manager should be able to
  // press Enter without first clicking anything.
  useEffect(() => field.current?.focus(), []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const offered = value.trim();
    if (!offered || phase === "checking") return;

    setPhase("checking");
    remember(offered);
    try {
      await api.health();
      onUnlocked();
    } catch (error) {
      // Storing then unstoring rather than checking first, because the token
      // has to be in place for the request to carry it. A rejected token must
      // not be left behind: the next reload would send it and fail again with
      // no field on screen to correct it.
      forget();
      const rejected = error instanceof ApiError && error.status === 401;
      setPhase(rejected ? "rejected" : "unreachable");
      field.current?.select();
    }
  }

  const busy = phase === "checking";

  return (
    <motion.main
      variants={enter}
      initial="hidden"
      animate="shown"
      className="mx-auto flex h-full max-w-md flex-col justify-center px-6"
    >
      <p className="text-2xs tracking-[0.2em] text-ink-faint uppercase">Slide-Wright</p>
      <h1 className="mt-3 text-lg font-medium text-ink">This deployment is shared.</h1>
      <p className="mt-1 text-xs leading-relaxed text-ink-muted">
        It is running on a server rather than on your own machine, so it asks for
        the access token whoever set it up gave you.
      </p>

      <form onSubmit={submit} className="mt-6">
        <label
          htmlFor="token"
          className="text-2xs tracking-wider text-ink-faint uppercase"
        >
          Access token
        </label>
        <div className="mt-1.5 flex gap-2">
          <input
            id="token"
            ref={field}
            type="password"
            value={value}
            autoComplete="current-password"
            spellCheck={false}
            disabled={busy}
            onChange={(e) => {
              setValue(e.target.value);
              // The rejection described the last attempt, not this one. Leaving
              // it up while somebody retypes reads as though the new value is
              // already wrong.
              if (phase !== "idle") setPhase("idle");
            }}
            aria-invalid={phase === "rejected" || undefined}
            aria-describedby={phase === "idle" ? undefined : "token-problem"}
            className={[
              "min-w-0 flex-1 rounded-md border bg-ground px-2.5 py-1.5",
              "text-xs text-ink placeholder:text-ink-faint",
              "focus:outline-2 focus:outline-offset-1 focus:outline-ink-faint",
              phase === "rejected" ? "border-blocked" : "border-line",
            ].join(" ")}
          />
          <Button type="submit" busy={busy} disabled={!value.trim()}>
            {busy ? "Checking" : "Unlock"}
          </Button>
        </div>

        {/* `aria-live` because the outcome of pressing a button has to reach
            somebody who cannot see the field turn red. */}
        <p
          id="token-problem"
          role="status"
          aria-live="polite"
          className="mt-2 min-h-4 text-2xs leading-relaxed text-ink-muted"
        >
          {phase === "rejected" && (
            <span className="text-blocked">
              That token was not accepted. Check it with whoever runs this server.
            </span>
          )}
          {phase === "unreachable" &&
            "The server did not answer. It may be restarting — try again in a moment."}
        </p>
      </form>

      <p className="mt-8 border-t border-line pt-4 text-2xs leading-relaxed text-ink-faint">
        The token is kept for this tab only and is forgotten when you close it.
        Your decks are read from the server's filesystem and are not uploaded by
        this page.
      </p>
    </motion.main>
  );
}
