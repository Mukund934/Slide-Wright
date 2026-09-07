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

import { motion } from "motion/react";
import { useState } from "react";

import { LOCK_SCOPES, type LockScope, type LockSpec } from "../api/types";
import { Button, Pill } from "../design/primitives";
import { reveal } from "../motion/tokens";

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
  const [detail, setDetail] = useState(false);

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

      {/* One line, not a paragraph.
          This was two lines of permanent body text — 36px of a window that had
          137px left for content, restating the same fact on every screen. It is
          a disclosure, and a disclosure has to be *findable and true*, not
          unavoidable. The full sentence is a click away and the standing line
          says the part that changes behaviour. */}
      <div className="mt-1 flex items-baseline gap-1.5">
        <p className="min-w-0 truncate text-2xs text-ink-faint">
          {modelConfigured ? (
            <>
              describing a change sends a deck summary to{" "}
              <span className="text-evidence">{modelName}</span>
            </>
          ) : (
            "no model key — nothing is sent anywhere"
          )}
        </p>
        <button
          type="button"
          onClick={() => setDetail((was) => !was)}
          aria-expanded={detail}
          className="shrink-0 text-2xs text-ink-faint underline decoration-dotted underline-offset-2 transition-colors duration-[120ms] hover:text-ink"
        >
          {detail ? "less" : "what exactly?"}
        </button>
      </div>

      {detail && (
          <motion.p
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={reveal}
            className="mt-1 text-2xs leading-relaxed text-ink-faint"
          >
            {modelConfigured ? (
              <>
                A structural summary of the whole deck — every slide title, and the
                first 70 characters of every text object — goes to{" "}
                <span className="text-evidence">{modelName}</span>. Editing objects
                directly, auditing, verifying, tidying, refreshing and reverting send
                nothing.
              </>
            ) : (
              <>
                Every deterministic capability — audit, tidy, refresh, verify, revert,
                direct edits — works exactly as it does with a key. Only proposing from
                a written description needs one.
              </>
            )}
          </motion.p>
      )}
    </div>
  );
}

export { LOCK_SCOPES };
