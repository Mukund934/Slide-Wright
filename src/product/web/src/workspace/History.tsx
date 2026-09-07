/**
 * Every version of this deck.
 *
 * The engine's model makes this unusually simple to present honestly: nothing
 * is ever undone. Each version is a file that still exists, so going back is
 * *choosing an earlier one*, not reversing an operation. That is worth saying
 * in the interface, because "revert" in most tools means "hope the inverse
 * applied cleanly" and here it does not mean that at all.
 *
 * The discarded versions stay on disk after a revert. They are evidence, they
 * cost nothing, and their numbers are never handed out again.
 */

import { motion } from "motion/react";

import type { Version } from "../api/types";
import { Button, PanelHeading } from "../design/primitives";
import { enter, stagger } from "../motion/tokens";

export function History({
  versions,
  busy,
  onRevert,
}: {
  versions: Version[];
  busy: boolean;
  onRevert: (to: number) => void;
}) {
  return (
    <>
      <PanelHeading
        trailing={
          <span className="text-evidence text-[--color-ink-faint]">{versions.length}</span>
        }
      >
        History
      </PanelHeading>
      <motion.ol
        className="overflow-y-auto"
        initial="hidden"
        animate="shown"
        transition={stagger(versions.length)}
      >
        {[...versions].reverse().map((version) => (
          <motion.li
            key={version.number}
            variants={enter}
            className={[
              "group flex items-start gap-2 border-b border-[--color-line] px-3 py-2 last:border-b-0",
              version.is_current ? "bg-[--color-raised]" : "",
            ].join(" ")}
          >
            <span
              aria-hidden
              className={[
                "mt-1 size-1.5 shrink-0 rounded-full",
                version.is_current ? "bg-[--color-ink]" : "bg-[--color-line-strong]",
              ].join(" ")}
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-2">
                <span className="text-evidence text-[--color-ink-muted]">
                  v{String(version.number).padStart(3, "0")}
                </span>
                {version.is_original && (
                  <span className="text-2xs text-[--color-ink-faint]">
                    the file you supplied
                  </span>
                )}
                {version.is_current && (
                  <span className="text-2xs text-[--color-ink-faint]">current</span>
                )}
              </div>
              {version.note && (
                <p className="mt-0.5 truncate text-xs text-[--color-ink]" title={version.note}>
                  {version.note}
                </p>
              )}
              <p className="text-evidence mt-0.5 text-[--color-ink-faint]">
                {version.created_at}
                {version.changes.length > 0 && ` · ${version.changes.length} change`}
                {version.changes.length > 1 && "s"}
              </p>
            </div>
            {!version.is_current && (
              <Button
                tone="quiet"
                busy={busy}
                onClick={() => onRevert(version.number)}
                className="opacity-0 transition-opacity duration-[--duration-fast] focus-visible:opacity-100 group-hover:opacity-100"
              >
                Go back
              </Button>
            )}
          </motion.li>
        ))}
      </motion.ol>
    </>
  );
}
