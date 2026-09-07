/**
 * Where a request is made.
 *
 * Not a chat sidebar. The difference the UX architecture insists on: *"make
 * this less crowded"* said with slide 7 selected and said with nothing selected
 * are different requests, and the whole gap between a usable tool and a chat toy
 * is whether the interface knows that. So the scope is shown, always, as a chip
 * the user can see and change — the context is never something they have to
 * describe again in prose.
 *
 * The locks live here for the same reason. *"Polish it but do not touch a
 * single figure"* is a constraint, not prompt text, and typing it into a
 * sentence puts a guarantee at the mercy of a model. Declared here, it is
 * enforced by the engine at both the planner and the verifier.
 */

import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";

import { LOCK_SCOPES, type LockScope, type LockSpec } from "../api/types";
import { Button, Pill } from "../design/primitives";
import { enter } from "../motion/tokens";

/** The scopes worth one click. The rest are available, just not in the way. */
const QUICK_LOCKS: { scope: LockScope; label: string; hint: string }[] = [
  { scope: "numbers", label: "numbers", hint: "no figure may change" },
  { scope: "wording", label: "wording", hint: "no text may change" },
  { scope: "layout", label: "layout", hint: "nothing may move or resize" },
  { scope: "formatting", label: "formatting", hint: "no typeface, size or colour" },
];

export function CommandBar({
  scopeLabel,
  modelConfigured,
  modelName,
  busy,
  onPropose,
  onClearSelection,
}: {
  scopeLabel: string;
  modelConfigured: boolean;
  modelName: string;
  busy: boolean;
  onPropose: (instruction: string, locks: LockSpec[]) => void;
  onClearSelection: () => void;
}) {
  const [instruction, setInstruction] = useState("");
  const [locks, setLocks] = useState<LockScope[]>([]);

  const submit = () => {
    const text = instruction.trim();
    if (!text || busy) return;
    onPropose(
      text,
      locks.map((scope) => ({ scope })),
    );
    setInstruction("");
  };

  return (
    <div className="shrink-0 border-t border-line bg-panel px-3 py-2">
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
        <span className="text-2xs text-ink-faint">Scope</span>
        <button
          type="button"
          onClick={onClearSelection}
          className="rounded-full transition-opacity duration-[120ms] hover:opacity-80"
          title="Clear the selection and address the whole deck"
        >
          <Pill verdict="changed">{scopeLabel}</Pill>
        </button>

        <span className="ml-2 text-2xs text-ink-faint">Protect</span>
        {QUICK_LOCKS.map((lock) => {
          const on = locks.includes(lock.scope);
          return (
            <button
              key={lock.scope}
              type="button"
              title={lock.hint}
              aria-pressed={on}
              onClick={() =>
                setLocks((current) =>
                  on ? current.filter((s) => s !== lock.scope) : [...current, lock.scope],
                )
              }
              className={[
                "rounded-full px-2 py-0.5 text-2xs transition-colors duration-[120ms]",
                on
                  ? "bg-ink font-medium text-ground"
                  : "bg-raised text-ink-faint hover:text-ink",
              ].join(" ")}
            >
              {lock.label}
            </button>
          );
        })}
      </div>

      <div className="flex items-end gap-2">
        <textarea
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          rows={1}
          disabled={!modelConfigured || busy}
          placeholder={
            modelConfigured
              ? "What should change?"
              : "No model key configured — edit objects directly instead"
          }
          title={
            modelConfigured
              ? `Describing a change sends a structural summary of the whole deck to ${modelName}.`
              : undefined
          }
          aria-label="Describe the change you want"
          className="min-h-8 flex-1 resize-none rounded-md border border-line-strong bg-raised px-2.5 py-1.5 text-xs text-ink placeholder:text-ink-faint focus:border-changed-dim focus:outline-none disabled:opacity-50"
        />
        <Button
          tone="primary"
          onClick={submit}
          busy={busy}
          disabled={!modelConfigured || !instruction.trim()}
        >
          Propose
        </Button>
      </div>

      <AnimatePresence initial={false}>
        <motion.p
          key={modelConfigured ? "configured" : "stub"}
          variants={enter}
          initial="hidden"
          animate="shown"
          exit="gone"
          className="mt-1.5 text-2xs leading-relaxed text-ink-faint"
        >
          {modelConfigured ? (
            <>
              Proposing from a description sends a structural summary of the whole
              deck — every slide title, and the first 70 characters of every text
              object — to <span className="text-evidence">{modelName}</span>. Direct
              edits and everything else stay on this machine.
            </>
          ) : (
            <>
              No model key is set, so nothing is sent anywhere. Every deterministic
              capability — audit, verify, revert, direct edits — works exactly as it
              does with one.
            </>
          )}
        </motion.p>
      </AnimatePresence>
    </div>
  );
}

export { LOCK_SCOPES };
