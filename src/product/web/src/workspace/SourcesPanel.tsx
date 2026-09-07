/**
 * Where the numbers come from.
 *
 * The recurring-deck workflow: last quarter's deck, this quarter's numbers.
 * The engine calls it the most valuable thing this product can do and the most
 * dangerous, because **a wrong number here is invisible — it looks exactly like
 * a right one.** Everything about this panel follows from that sentence.
 *
 * So all three outcomes are shown, and the middle one is the reason the panel
 * exists rather than a footnote:
 *
 *   · **Updated** — the source disagrees, and here is the cell it disagrees from.
 *   · **Confirmed** — the source *agrees*. That is not nothing: it is positive
 *     evidence that a figure is still right, and without it a reader cannot
 *     tell "checked and correct" from "never looked at".
 *   · **Not found** — the source cannot explain this figure, so it was left
 *     alone. A figure the engine cannot justify with a coordinate is one it
 *     will not change.
 *
 * Nothing here is matched by resemblance or position. A cell is refreshed only
 * when its row label and column header both match the source, and every
 * proposal carries the coordinate.
 */

import { motion } from "motion/react";
import { useState } from "react";

import { ApiError, api } from "../api/client";
import type { Match, RefreshPlan } from "../api/types";
import { Button, Empty, PanelContext, Pill } from "../design/primitives";
import { enter, stagger } from "../motion/tokens";

export function SourcesPanel({
  documentId,
  busy,
  onGoToSlide,
  onRefresh,
}: {
  documentId: string;
  busy: boolean;
  onGoToSlide: (slide: number) => void;
  onRefresh: (sources: string[]) => void;
}) {
  const [path, setPath] = useState("");
  const [sources, setSources] = useState<string[]>([]);
  const [plan, setPlan] = useState<RefreshPlan | null>(null);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const attach = async () => {
    const trimmed = path.trim().replace(/^"|"$/g, "");
    if (!trimmed || reading) return;
    const next = [...sources, trimmed];

    setReading(true);
    setError(null);
    try {
      // Previewed on attach rather than behind a second button. The question a
      // user has the moment they point at a workbook is "does this explain my
      // deck", and making them ask for the answer separately is a step that
      // exists only because it was easier to build.
      setPlan(await api.refreshPreview(documentId, next));
      setSources(next);
      setPath("");
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "That source could not be read.");
    } finally {
      setReading(false);
    }
  };

  return (
    <>
      <PanelContext
        trailing={
          sources.length > 0 ? (
            <Button
              tone="quiet"
              onClick={() => {
                setSources([]);
                setPlan(null);
                setError(null);
              }}
            >
              Clear
            </Button>
          ) : undefined
        }
      >
        {/* Once a source is attached this names it. Before that the empty
            state below explains the matching rule, and repeating it here would
            be the same sentence twice in adjacent rows. */}
        {sources.length === 0 ? "no source attached" : sources.map(basename).join(" · ")}
      </PanelContext>

      <div className="shrink-0 border-b border-line px-3 py-2">
        <div className="flex gap-2">
          <input
            value={path}
            onChange={(event) => setPath(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && void attach()}
            placeholder="C:\Users\you\comps.xlsx"
            spellCheck={false}
            aria-label="Path to a .csv or .xlsx on this machine"
            className="text-evidence min-w-0 flex-1 rounded-md border border-line-strong bg-raised px-2 py-1.5 text-ink placeholder:text-ink-faint focus:border-ink-faint"
          />
          <Button onClick={() => void attach()} busy={reading} disabled={!path.trim()}>
            Attach
          </Button>
        </div>

        {sources.length > 0 && (
          <ul className="mt-2 flex flex-wrap gap-1">
            {sources.map((source) => (
              <li key={source}>
                <Pill>
                  <span className="text-evidence">{basename(source)}</span>
                </Pill>
              </li>
            ))}
          </ul>
        )}

        <p className="mt-2 text-2xs leading-relaxed text-ink-faint">
          A path, not an upload. The numbers behind a deck are as confidential as
          the deck, and neither leaves this machine.
        </p>
      </div>

      {error && (
        <p role="alert" className="shrink-0 bg-blocked-wash px-3 py-2 text-xs leading-relaxed text-blocked">
          {error}
        </p>
      )}

      {!plan ? (
        <Empty
          title="Point at this quarter's workbook"
          detail="Figures are matched by row label and column header — never by position, and never by resemblance. Anything the source cannot explain is left alone."
        />
      ) : (
        <Plan plan={plan} busy={busy} onGoToSlide={onGoToSlide} onRefresh={() => onRefresh(sources)} />
      )}
    </>
  );
}

function Plan({
  plan,
  busy,
  onGoToSlide,
  onRefresh,
}: {
  plan: RefreshPlan;
  busy: boolean;
  onGoToSlide: (slide: number) => void;
  onRefresh: () => void;
}) {
  return (
    <motion.div
      className="min-h-0 flex-1 overflow-y-auto"
      initial="hidden"
      animate="shown"
      transition={stagger(plan.updates.length + plan.confirmed.length)}
    >
      <div className="border-b border-line px-3 py-2">
        <p className="text-2xs leading-relaxed text-ink-faint">
          <span className="text-evidence text-ink-muted">{plan.tables}</span> table
          {plan.tables === 1 ? "" : "s"} read ·{" "}
          <span className="text-evidence text-changed">{plan.updates.length}</span> to
          update ·{" "}
          <span className="text-evidence text-verified">{plan.confirmed.length}</span>{" "}
          already correct ·{" "}
          <span className="text-evidence text-ink-muted">{plan.unmatched.length}</span> not
          found
          {plan.refused.length > 0 && (
            <>
              {" · "}
              <span className="text-evidence text-ink-muted">
                {plan.refused.length}
              </span>{" "}
              not safe to write
            </>
          )}
        </p>
      </div>

      {plan.updates.length > 0 && (
        <Section
          title="The source disagrees"
          note="Each carries the cell it came from. Nothing is matched by position."
        >
          {plan.updates.map((match, index) => (
            <Row key={`u${index}`} match={match} onGoToSlide={onGoToSlide} />
          ))}
          <div className="border-b border-line bg-raised px-3 py-2.5">
            <Button tone="primary" onClick={onRefresh} busy={busy}>
              Propose {plan.updates.length} figure
              {plan.updates.length === 1 ? "" : "s"}
            </Button>
            <p className="mt-1.5 text-2xs leading-relaxed text-ink-faint">
              Proposes only. You review each figure against its source before
              anything is written.
            </p>
          </div>
        </Section>
      )}

      {plan.confirmed.length > 0 && (
        <Section
          title="The source agrees"
          note="Checked against the source and already correct. Not the same as unchecked."
        >
          {plan.confirmed.map((match, index) => (
            <Row key={`c${index}`} match={match} onGoToSlide={onGoToSlide} confirmed />
          ))}
        </Section>
      )}

      {/* Above "not found", because it is the more alarming of the two: the
          source was found, it holds a different figure, and the engine is
          declining to write it. A reviewer who reads no further should read
          this one. */}
      {plan.refused.length > 0 && (
        <Section
          title="The source disagrees, and this cannot be written"
          note="Writing these would change what the cell says, not what it reports — a spreadsheet stores 12.3% as 0.123, and a blank cell is missing data rather than a value of nothing."
        >
          {plan.refused.map((line) => (
            <p
              key={line}
              className="border-b border-line px-3 py-2 text-2xs leading-relaxed text-ink last:border-b-0"
            >
              {line}
            </p>
          ))}
        </Section>
      )}

      {plan.unmatched.length > 0 && (
        <Section
          title="Not found in the source"
          note="Left untouched. A figure the engine cannot justify with a coordinate is one it will not change."
        >
          {plan.unmatched.map((line) => (
            <p key={line} className="border-b border-line px-3 py-2 text-2xs leading-relaxed text-ink-faint last:border-b-0">
              {line}
            </p>
          ))}
        </Section>
      )}
    </motion.div>
  );
}

function Section({
  title,
  note,
  children,
}: {
  title: string;
  note: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="sticky top-0 z-10 border-b border-line bg-panel px-3 py-1.5">
        <h2 className="text-2xs font-medium uppercase tracking-[0.08em] text-ink-muted">
          {title}
        </h2>
        <p className="mt-0.5 text-2xs leading-relaxed text-ink-faint">{note}</p>
      </div>
      {children}
    </section>
  );
}

function Row({
  match,
  onGoToSlide,
  confirmed = false,
}: {
  match: Match;
  onGoToSlide: (slide: number) => void;
  confirmed?: boolean;
}) {
  return (
    <motion.div variants={enter} className="border-b border-line px-3 py-2 last:border-b-0">
      <button
        type="button"
        onClick={() => onGoToSlide(match.slide)}
        aria-label={`Go to slide ${match.slide}: ${match.current}`}
        className="flex w-full items-baseline gap-2 text-left"
      >
        <span className="text-evidence shrink-0 text-ink-faint">{match.slide}</span>
        <span className="flex-1 text-xs leading-snug text-ink">
          {confirmed ? (
            <span className="text-evidence">{match.current}</span>
          ) : (
            <>
              <span className="text-evidence line-through opacity-60">{match.current}</span>
              <span className="mx-1.5 text-ink-faint">→</span>
              <span className="text-evidence text-changed">{match.proposed}</span>
            </>
          )}
        </span>
      </button>
      <Pill className="mt-1 ml-6" verdict="verified">
        source <span className="text-evidence">{match.citation}</span>
      </Pill>
    </motion.div>
  );
}

function basename(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}
