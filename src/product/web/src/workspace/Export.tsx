/**
 * The last door.
 *
 * Every guarantee in this product converges here: a deck that failed
 * verification must not leave the session by any route, and this is the route.
 * So the refusal is asked *before* the field is offered rather than after it is
 * submitted. Technically identical; entirely different to be on the end of.
 * Rejecting a submission makes a verdict about the deck look like a mistake in
 * what the user typed.
 *
 * The suggested path never overwrites the file they opened. That is not a
 * nicety either — the original is what every verification compares against, and
 * it is what they fall back to when they decide they preferred it.
 */

import { motion } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "../api/client";
import type { ExportTarget } from "../api/types";
import { Button, Pill } from "../design/primitives";
import { reveal } from "../motion/tokens";

export function Export({ documentId, version }: { documentId: string; version: number }) {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<ExportTarget | null>(null);
  const [destination, setDestination] = useState("");
  const [written, setWritten] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  // Re-asked whenever the version moves. The verdict belongs to one apply, and
  // a stale "deliverable" would offer an export the engine is about to refuse.
  useEffect(() => {
    if (!open) return;
    let live = true;
    api
      .exportTarget(documentId)
      .then((next) => {
        if (!live) return;
        setTarget(next);
        setDestination(next.suggested);
      })
      .catch((cause: unknown) => {
        if (live) setError(cause instanceof ApiError ? cause.message : "Could not check.");
      });
    return () => {
      live = false;
    };
  }, [documentId, version, open]);

  // A written path describes one version. Once another apply lands it is a
  // statement about a file that is no longer current.
  useEffect(() => setWritten(null), [version]);

  // Escape closes it, and so does a click anywhere else. A popover that can
  // only be dismissed by the control that opened it is a trap for anyone who
  // opened it by accident -- and the keyboard user has no other way out.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    const onPointer = (event: PointerEvent) => {
      if (!panel.current?.contains(event.target as Node) &&
          !trigger.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  const write = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.export(documentId, destination.trim());
      setWritten(result.path);
      setOpen(false);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The export failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative">
      <Button ref={trigger} onClick={() => setOpen((was) => !was)} aria-expanded={open}>
        Export
      </Button>

      {/* Rendered plainly, not through AnimatePresence.
          An exit animation here was carrying no meaning -- a popover that
          simply goes is what every tool does and what the reader expects --
          and it was failing to unmount, leaving the panel at opacity 0 with
          its input still in the tab order. A keyboard user could tab into a
          dialog that had been closed. Motion that does no work and costs
          correctness is not a trade worth making. */}
      {open && (
        <motion.div
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={reveal}
          ref={panel}
          role="dialog"
          aria-label="Export this version"
          className="absolute right-0 top-full z-20 mt-1 w-96 max-w-[calc(100vw-1.5rem)] rounded-lg border border-line-strong bg-panel p-3 shadow-[0_8px_28px_-6px_rgba(0,0,0,0.6)]"
        >
          {target && !target.deliverable ? (
            <Blocked reasons={target.blocking_reasons} />
          ) : (
            <>
              <label
                htmlFor="export-destination"
                className="block text-2xs uppercase tracking-[0.08em] text-ink-faint"
              >
                Write this version to
              </label>
              <div className="mt-1.5 flex gap-2">
                <input
                  id="export-destination"
                  value={destination}
                  onChange={(event) => setDestination(event.target.value)}
                  onKeyDown={(event) => event.key === "Enter" && void write()}
                  spellCheck={false}
                  autoFocus
                  className="text-evidence min-w-0 flex-1 rounded-md border border-line-strong bg-raised px-2 py-1.5 text-ink focus:border-changed-dim focus:outline-none"
                />
                <Button tone="primary" onClick={() => void write()} busy={busy}>
                  Write
                </Button>
              </div>
              <p className="mt-2 text-2xs leading-relaxed text-ink-faint">
                Beside your original, never over it. Slide-Wright will not overwrite
                the file you opened — the original is what every verification
                compares against.
              </p>
            </>
          )}

          {error && (
            <p role="alert" className="mt-2 rounded-md bg-blocked-wash px-2 py-1.5 text-xs leading-relaxed text-blocked">
              {error}
            </p>
          )}
        </motion.div>
      )}

      {written && !open && (
        <motion.div
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={reveal}
          className="absolute right-0 top-full z-20 mt-1 whitespace-nowrap rounded-md bg-verified-wash px-2 py-1"
        >
          <span className="text-evidence text-verified">wrote {written}</span>
        </motion.div>
      )}

    </div>
  );
}

/**
 * A refusal, before the field rather than after it.
 *
 * Users trained by other tools read a refusal as breakage. Here it is the
 * guarantee working, and the copy says so without being smug about withholding
 * someone's deck.
 */
function Blocked({ reasons }: { reasons: string[] }) {
  return (
    <>
      <div className="mb-1.5 flex items-center gap-2">
        <Pill verdict="blocked">Not deliverable</Pill>
      </div>
      <p className="text-xs leading-relaxed text-ink">
        This version failed verification, so Slide-Wright will not write it out. Your
        original is untouched, and you can go back to any earlier version.
      </p>
      <ul className="mt-2 space-y-1">
        {reasons.map((reason) => (
          <li key={reason} className="flex gap-1.5 text-xs leading-relaxed text-blocked">
            <span aria-hidden>·</span>
            <span>{reason}</span>
          </li>
        ))}
      </ul>
    </>
  );
}
