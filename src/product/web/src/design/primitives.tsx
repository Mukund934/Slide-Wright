/**
 * The primitives. Deliberately few.
 *
 * Each one owns its whole lifecycle -- rest, hover, focus, press, disabled,
 * busy -- because styling only the default state is what makes an interface
 * feel unfinished no matter how good the default looks.
 *
 * The colour rule from `styles.css` is enforced here by omission: no primitive
 * accepts an arbitrary colour, and none of them can be made to use the attention
 * colour. That colour belongs to "this changed", and a button wearing it would
 * spend the one signal the product cannot afford to dilute.
 */

import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";

type Tone = "default" | "primary" | "quiet" | "danger";

const TONES: Record<Tone, string> = {
  default:
    "bg-raised text-ink border-line-strong " +
    "hover:bg-[color-mix(in_oklab,var(--color-raised),white_6%)]",
  // "Primary" is weight, not colour: the important action is the solid one.
  primary:
    "bg-ink text-ground border-transparent font-medium " +
    "hover:bg-[color-mix(in_oklab,var(--color-ink),var(--color-ground)_12%)]",
  quiet:
    "bg-transparent text-ink-muted border-transparent " +
    "hover:bg-raised hover:text-ink",
  danger:
    "bg-transparent text-blocked border-[color-mix(in_oklab,var(--color-blocked),transparent_65%)] " +
    "hover:bg-blocked-wash",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  tone?: Tone;
  busy?: boolean;
  children: ReactNode;
  // React 19 passes ref as an ordinary prop, so no forwardRef wrapper.
  ref?: Ref<HTMLButtonElement>;
}

export function Button({
  ref,
  tone = "default",
  busy = false,
  disabled,
  className = "",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      ref={ref}
      type="button"
      // `busy` disables as well as announces. A button that is working and
      // still clickable is how a deck gets edited twice.
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={[
        "inline-flex items-center justify-center gap-1.5 rounded-md border",
        "px-2.5 py-1.5 text-xs transition-colors duration-[120ms]",
        "active:translate-y-px disabled:pointer-events-none disabled:opacity-40",
        TONES[tone],
        className,
      ].join(" ")}
      {...rest}
    >
      {busy && <Spinner />}
      {children}
    </button>
  );
}

function Spinner() {
  return (
    <span
      aria-hidden
      className="size-3 animate-spin rounded-full border border-current border-t-transparent opacity-70"
    />
  );
}

/**
 * A verdict, worn as a pill.
 *
 * `verified` and `blocked` are reserved for things the engine actually
 * determined. Nothing decorative may use them: the moment a green pill means
 * "nice" somewhere, it stops meaning "checked" everywhere.
 */
export type Verdict = "verified" | "blocked" | "review" | "changed" | "neutral";

const VERDICTS: Record<Verdict, string> = {
  verified: "bg-verified-wash text-verified",
  blocked: "bg-blocked-wash text-blocked",
  review: "bg-review-wash text-review",
  changed: "bg-changed-wash text-changed",
  neutral: "bg-raised text-ink-faint",
};

export function Pill({
  verdict = "neutral",
  children,
  className = "",
}: {
  verdict?: Verdict;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={[
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-2xs font-medium",
        VERDICTS[verdict],
        className,
      ].join(" ")}
    >
      {children}
    </span>
  );
}

/** A region heading. Small, quiet, and always in the same place. */
export function PanelHeading({
  children,
  trailing,
}: {
  children: ReactNode;
  trailing?: ReactNode;
}) {
  return (
    <div className="flex h-9 shrink-0 items-center justify-between border-b border-line px-3">
      <h2 className="text-2xs font-medium uppercase tracking-[0.08em] text-ink-faint">
        {children}
      </h2>
      {trailing}
    </div>
  );
}

/**
 * The strip under a tab: what this panel is *about*, not what it is called.
 *
 * A panel inside a labelled tab does not need a heading repeating the label.
 * That was costing two rows of a window that had 137px left for content — and
 * the second row said "AUDIT" under a tab that said "AUDIT".
 *
 * So the space goes to the facts instead: how many slides, how many findings,
 * how many approved. Actions sit on the right where the heading's trailing slot
 * used to be, so nothing moved for the reader.
 */
export function PanelContext({
  children,
  trailing,
}: {
  children?: ReactNode;
  trailing?: ReactNode;
}) {
  return (
    <div className="flex min-h-7 shrink-0 items-center justify-between gap-2 border-b border-line px-3 py-1">
      <p className="min-w-0 truncate text-2xs text-ink-faint">{children}</p>
      {trailing && <div className="flex shrink-0 items-center gap-1">{trailing}</div>}
    </div>
  );
}

/**
 * An empty state that answers "what can I do here?".
 *
 * The UX architecture calls first upload the only onboarding that matters, so
 * these carry an action rather than an illustration.
 */
export function Empty({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
      <p className="text-sm text-ink-muted">{title}</p>
      {detail && <p className="max-w-xs text-xs leading-relaxed text-ink-faint">{detail}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/**
 * A count with a label, set in tabular figures.
 *
 * These are the product's evidence. `intact` false is the only thing that gets
 * the blocked colour -- an unremarkable number must look unremarkable, or the
 * one that matters stops standing out.
 */
export function Stat({
  label,
  value,
  intact = true,
}: {
  label: string;
  value: ReactNode;
  intact?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-xs text-ink-faint">{label}</span>
      <span
        className={[
          "text-evidence",
          intact ? "text-ink-muted" : "text-blocked",
        ].join(" ")}
      >
        {value}
      </span>
    </div>
  );
}
