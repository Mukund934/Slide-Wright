/**
 * What must not change.
 *
 * The engine has carried per-shape and per-slide locks since the change set
 * existed — its own docstring gives the example *"restyle it but leave slide 4
 * alone, the partner signed it off"* — and until now the interface could only
 * say things about the whole deck. So the guarantee people actually ask for was
 * the one guarantee they could not express.
 *
 * Three properties this has to hold, and each is the reason a lock is worth
 * more than a sentence in a prompt:
 *
 *   · **It is a constraint, not a request.** The engine refuses a change a lock
 *     forbids at the moment it is constructed, before anything is written and
 *     regardless of what any model proposed.
 *   · **It stands.** Locks live in session state, not in one request, because
 *     a guarantee that had to be re-declared each time would be forgotten on
 *     the proposal that mattered.
 *   · **It is visible.** A refused change appears in the review list marked
 *     rejected, naming the lock that stopped it. The research calls that
 *     "visible algorithmic disobedience" and it is the moment the promise stops
 *     being a claim.
 */

import type { LockScope, LockSpec, Shape } from "../api/types";
import { Pill } from "../design/primitives";

/** The deck-wide scopes worth one click. The engine accepts more. */
const DECK_SCOPES: { scope: LockScope; label: string; hint: string }[] = [
  { scope: "numbers", label: "numbers", hint: "no figure may change, anywhere" },
  { scope: "wording", label: "wording", hint: "no text may change, anywhere" },
  { scope: "layout", label: "layout", hint: "nothing may move or resize" },
  { scope: "formatting", label: "formatting", hint: "no typeface, size or colour" },
];

export function Protect({
  slide,
  shape,
  locks,
  onLock,
  onUnlock,
}: {
  slide: number;
  shape: Shape | null;
  locks: LockSpec[];
  onLock: (lock: LockSpec) => void;
  onUnlock: (lock: LockSpec) => void;
}) {
  const held = (lock: LockSpec) =>
    locks.some((l) => l.scope === lock.scope && (l.target ?? "") === (lock.target ?? ""));

  const toggle = (lock: LockSpec) => (held(lock) ? onUnlock(lock) : onLock(lock));

  const slideLock: LockSpec = { scope: "slide", target: String(slide) };
  const shapeLock: LockSpec | null = shape ? { scope: "shape", target: shape.id } : null;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-2xs text-ink-faint">Protect</span>

      {/* The specific ones first. "This slide" and "this object" are what
          someone reaches for with a deck open in front of them; the deck-wide
          scopes are the ones they set once and forget. */}
      <Chip
        on={held(slideLock)}
        hint={`nothing on slide ${slide} may change`}
        onClick={() => toggle(slideLock)}
      >
        slide {slide}
      </Chip>

      {shapeLock && shape && (
        <Chip
          on={held(shapeLock)}
          hint={`nothing about this ${shape.kind} may change`}
          onClick={() => toggle(shapeLock)}
        >
          this {shape.kind}
        </Chip>
      )}

      <span aria-hidden className="mx-0.5 h-3 w-px bg-line-strong" />

      {DECK_SCOPES.map(({ scope, label, hint }) => (
        <Chip key={scope} on={held({ scope })} hint={hint} onClick={() => toggle({ scope })}>
          {label}
        </Chip>
      ))}
    </div>
  );
}

function Chip({
  on,
  hint,
  onClick,
  children,
}: {
  on: boolean;
  hint: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={hint}
      aria-pressed={on}
      onClick={onClick}
      className={[
        "rounded-full px-2 py-0.5 text-2xs transition-colors duration-[120ms]",
        // Held locks are marked by weight and a glyph, not by hue alone: the
        // state has to survive a reader who cannot separate the colours.
        on
          ? "bg-ink font-medium text-ground"
          : "bg-raised text-ink-faint hover:text-ink",
      ].join(" ")}
    >
      {on && <span aria-hidden>· </span>}
      {children}
    </button>
  );
}

/**
 * Everything currently protected, listed where it can be undone.
 *
 * A lock the user cannot find is a lock they cannot lift, and a standing
 * guarantee they have forgotten setting is how a later proposal comes back
 * mysteriously empty.
 */
export function ProtectedList({
  locks,
  onUnlock,
}: {
  locks: LockSpec[];
  onUnlock: (lock: LockSpec) => void;
}) {
  if (locks.length === 0) return null;

  return (
    <div className="shrink-0 border-b border-line px-3 py-2">
      <p className="mb-1 text-2xs uppercase tracking-[0.08em] text-ink-faint">Protected</p>
      <ul className="flex flex-wrap gap-1">
        {locks.map((lock) => (
          <li key={`${lock.scope}-${lock.target ?? ""}`}>
            <button
              type="button"
              onClick={() => onUnlock(lock)}
              title="Stop protecting this"
              className="transition-opacity duration-[120ms] hover:opacity-70"
            >
              <Pill>
                {lock.scope}
                {lock.target && <span className="opacity-60"> {lock.target}</span>}
                <span aria-hidden className="ml-0.5 opacity-60">×</span>
              </Pill>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
