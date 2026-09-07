/**
 * The change set: the contract, shown before anything is written.
 *
 * Everything about this panel follows from one requirement — a reviewer must be
 * able to answer, from a row alone: what is changing, where, why, who proposed
 * it, how sure are they, and what else might it affect. The engine's `Change`
 * carries exactly those fields, which is not a coincidence; the record was
 * designed to be reviewable and this is where that is cashed in.
 *
 * Two disciplines the note asks for and the engine already enforces:
 *
 *   · A model's proposal with no citation is marked and is never approved by
 *     "approve all" unless the user says so explicitly. Grounded and invented
 *     changes must not look equally actionable.
 *   · A change a lock rejected is shown as rejected, with the lock named. It
 *     did not fail; a guarantee the user asked for stopped it.
 */

import { AnimatePresence, motion } from "motion/react";

import type { Change, ChangeSet } from "../api/types";
import { Button, Empty, PanelHeading, Pill } from "../design/primitives";
import { enter, stagger } from "../motion/tokens";

export function ChangeSetPanel({
  changeset,
  busy,
  onGoTo,
  onApprove,
  onReject,
  onApproveAll,
}: {
  changeset: ChangeSet | null;
  busy: boolean;
  onGoTo: (change: Change) => void;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  onApproveAll: (includeUnreviewed: boolean) => void;
}) {
  if (!changeset || changeset.changes.length === 0) {
    return (
      <>
        <PanelHeading>Changes</PanelHeading>
        <Empty
          title="Nothing proposed"
          detail="Ask for a change and Slide-Wright will write down exactly what it intends to do, before it touches the file."
        />
      </>
    );
  }

  const { changes, needs_review_count: needsReview } = changeset;
  const undecided = changes.filter((c) => c.status === "proposed");

  return (
    <>
      <PanelHeading
        trailing={
          <span className="text-evidence text-[--color-ink-faint]">
            {changeset.approved_count}/{changes.length} approved
          </span>
        }
      >
        Changes
      </PanelHeading>

      {changeset.locks.length > 0 && <Locks changeset={changeset} />}

      <motion.ul
        className="flex-1 overflow-y-auto"
        initial="hidden"
        animate="shown"
        transition={stagger(changes.length)}
      >
        <AnimatePresence initial={false}>
          {changes.map((change) => (
            <ChangeRow
              key={change.id}
              change={change}
              busy={busy}
              onGoTo={onGoTo}
              onApprove={onApprove}
              onReject={onReject}
            />
          ))}
        </AnimatePresence>
      </motion.ul>

      {undecided.length > 0 && (
        <div className="flex shrink-0 items-center gap-2 border-t border-[--color-line] px-3 py-2">
          <Button
            onClick={() => onApproveAll(false)}
            disabled={busy || undecided.length === needsReview}
          >
            Approve {undecided.length - needsReview} grounded
          </Button>
          {needsReview > 0 && (
            <Button tone="quiet" onClick={() => onApproveAll(true)} disabled={busy}>
              Include {needsReview} unreviewed
            </Button>
          )}
        </div>
      )}
    </>
  );
}

function Locks({ changeset }: { changeset: ChangeSet }) {
  return (
    <div className="shrink-0 border-b border-[--color-line] px-3 py-2">
      <p className="mb-1 text-2xs uppercase tracking-[0.08em] text-[--color-ink-faint]">
        Protected
      </p>
      <ul className="flex flex-wrap gap-1">
        {changeset.locks.map((lock, index) => (
          <li key={`${lock.scope}-${lock.target}-${index}`}>
            <Pill>
              {lock.scope}
              {lock.target && <span className="opacity-60"> · {lock.target}</span>}
            </Pill>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ChangeRow({
  change,
  busy,
  onGoTo,
  onApprove,
  onReject,
}: {
  change: Change;
  busy: boolean;
  onGoTo: (change: Change) => void;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
}) {
  const decided = change.status !== "proposed";

  return (
    <motion.li
      layout="position"
      variants={enter}
      initial="hidden"
      animate="shown"
      exit="gone"
      className={[
        "group border-b border-[--color-line] px-3 py-2 last:border-b-0",
        "transition-colors duration-[--duration-fast]",
        change.status === "rejected" ? "opacity-45" : "",
        "hover:bg-[color-mix(in_oklab,var(--color-panel),white_3%)]",
      ].join(" ")}
    >
      <button
        type="button"
        onClick={() => onGoTo(change)}
        className="mb-1 flex w-full items-baseline gap-2 text-left"
        // The whole row is the jump target: "click a change, land on the object"
        // is the interaction that makes a change set feel like a map rather
        // than a log.
        title={`Go to slide ${change.slide}`}
      >
        <span className="text-evidence shrink-0 text-[--color-ink-faint]">
          {change.slide}
        </span>
        <span className="flex-1 text-xs leading-snug text-[--color-ink]">
          {change.description}
        </span>
        <StatusMark change={change} />
      </button>

      <div className="flex flex-wrap items-center gap-1.5 pl-6">
        <Provenance change={change} />
      </div>

      {change.rationale && (
        <p className="mt-1 pl-6 text-2xs leading-relaxed text-[--color-ink-faint]">
          {change.rationale}
        </p>
      )}

      {change.impact && (
        <p className="mt-1 pl-6 text-2xs leading-relaxed text-[--color-review]">
          may also affect: {change.impact}
        </p>
      )}

      {!decided && (
        // Actions appear on hover or keyboard focus. Forty rows each carrying
        // two permanently visible buttons is a wall; forty rows that reveal
        // them where the pointer is, is a list.
        <div className="mt-1.5 flex gap-1.5 pl-6 opacity-0 transition-opacity duration-[--duration-fast] focus-within:opacity-100 group-hover:opacity-100">
          <Button tone="primary" onClick={() => onApprove(change.id)} disabled={busy}>
            Approve
          </Button>
          <Button tone="quiet" onClick={() => onReject(change.id)} disabled={busy}>
            Reject
          </Button>
        </div>
      )}
    </motion.li>
  );
}

function StatusMark({ change }: { change: Change }) {
  if (change.status === "approved") return <Pill verdict="changed">approved</Pill>;
  if (change.status === "applied") return <Pill verdict="verified">applied</Pill>;
  if (change.status === "rejected") return <Pill>rejected</Pill>;
  if (change.status === "failed") return <Pill verdict="blocked">failed</Pill>;
  return null;
}

/**
 * Where a change came from, and how far it can be trusted.
 *
 * The distinction the product turns on: a cited spreadsheet cell is checkable,
 * a model's suggestion is not. Both can be right; only one can be verified
 * without a person, and the badge is what stops them reading alike.
 */
function Provenance({ change }: { change: Change }) {
  const items = [];

  if (change.citation) {
    items.push(
      <Pill key="cite" verdict="verified">
        <span className="text-evidence">{change.citation}</span>
      </Pill>,
    );
  } else if (change.origin === "model") {
    items.push(
      <Pill key="origin" verdict="review">
        model{change.confidence < 1 ? ` · ${Math.round(change.confidence * 100)}%` : ""}
      </Pill>,
    );
  } else if (change.origin === "rule") {
    items.push(<Pill key="origin">rule</Pill>);
  }

  if (change.needs_review) {
    items.push(
      <Pill key="review" verdict="review">
        needs review
      </Pill>,
    );
  }

  if (change.object_kind) {
    items.push(<Pill key="kind">{change.object_kind}</Pill>);
  }

  return <>{items}</>;
}
