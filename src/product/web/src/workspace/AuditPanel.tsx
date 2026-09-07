/**
 * What is wrong with this deck.
 *
 * This is the first thing someone does with an inherited deck, and it is where
 * the product either earns trust or spends it. Two disciplines follow from that,
 * and both are the note's §12 and §13:
 *
 *   · **A finding must not look more authoritative than it is.** There is no
 *     score. A single number would compress "no slide title makes a claim" and
 *     "1,122 runs hardcode a typeface" into one figure that means neither, and
 *     an AI score is exactly the kind of false authority the engine is
 *     deterministic in order to avoid.
 *   · **Fixable and advisory must not look alike.** The engine attaches a
 *     remedy to a finding it can actually correct, and only those carry the
 *     action. Everything else says what it is: something a person has to decide.
 *
 * The gate is shown alongside, not merged in. It answers a different question —
 * "may this be delivered", about an edit that already happened — and flattening
 * the two would lose the distinction before the reader sees it.
 */

import { motion } from "motion/react";
import { useEffect, useState } from "react";

import { ApiError, api } from "../api/client";
import type { Area, Audit, Observation, TidyPlan } from "../api/types";
import { Button, Empty, PanelContext, Pill } from "../design/primitives";
import { enter, stagger } from "../motion/tokens";

/**
 * The engine's own areas, in the order a reader should meet them.
 *
 * Findings are grouped by whether they are fixable first — that distinction
 * matters more than the subject — and ordered by area within each group, so a
 * list of nine does not read as nine unrelated complaints.
 */
const AREA_ORDER: Area[] = [
  "narrative",
  "consistency",
  "layout",
  "evidence",
  "structure",
  "accessibility",
];

const TEMPLATE_PLACEHOLDER = "C:\Users\you\House.potx";

const AREA_LABEL: Record<Area, string> = {
  narrative: "Narrative",
  consistency: "Consistency",
  layout: "Layout",
  evidence: "Evidence",
  structure: "Structure",
  accessibility: "Accessibility",
};

export function AuditPanel({
  documentId,
  busy,
  onGoToSlide,
  onTidy,
}: {
  documentId: string;
  busy: boolean;
  onGoToSlide: (slide: number) => void;
  onTidy: (template: string) => void;
}) {
  const [audit, setAudit] = useState<Audit | null>(null);
  const [plan, setPlan] = useState<TidyPlan | null>(null);
  const [template, setTemplate] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Re-read whenever the document changes underneath — after an apply or a
  // revert the deck is a different one, and a stale audit would describe a
  // version that is no longer current.
  useEffect(() => {
    let live = true;
    setAudit(null);
    setError(null);
    Promise.all([api.audit(documentId), api.tidyPlan(documentId, template)])
      .then(([nextAudit, nextPlan]) => {
        if (!live) return;
        setAudit(nextAudit);
        setPlan(nextPlan);
      })
      .catch((cause: unknown) => {
        if (live) setError(cause instanceof ApiError ? cause.message : "The audit failed.");
      });
    return () => {
      live = false;
    };
  }, [documentId, busy, template]);

  if (error) {
    return (
      <>
        <PanelContext>could not run</PanelContext>
        <Empty title="The audit could not run" detail={error} />
      </>
    );
  }

  if (!audit) {
    return (
      <>
        <PanelContext>reading the deck…</PanelContext>
        <Empty title="Reading the deck…" />
      </>
    );
  }

  const fixable = byArea(audit.observations.filter((o) => o.is_automatable));
  const advisory = byArea(audit.observations.filter((o) => !o.is_automatable));

  return (
    <>
      <PanelContext
        trailing={
          <span className="text-evidence text-ink-muted">
            {audit.observations.length} finding
            {audit.observations.length === 1 ? "" : "s"}
          </span>
        }
      >
        {/* Facts, and deliberately not a grade. Words per slide is a
            measurement, not a verdict — a dense appendix is fine and a dense
            pitch is not, and this cannot tell them apart. */}
        <span className="text-evidence text-ink-muted">{audit.slide_count}</span> slides ·{" "}
        <span className="text-evidence text-ink-muted">{audit.word_count}</span> words ·{" "}
        <span className="text-evidence text-ink-muted">
          {Math.round(audit.words_per_slide)}
        </span>{" "}
        per slide
      </PanelContext>

      <motion.div
        className="min-h-0 flex-1 overflow-y-auto"
        initial="hidden"
        animate="shown"
        transition={stagger(audit.observations.length)}
      >
        {audit.observations.length === 0 && (
          <Empty
            title="Nothing structural to report"
            detail="Every check the engine can compute came back clean. That is a statement about structure, not about whether the argument works."
          />
        )}

        {fixable.length > 0 && (
          <Section
            title="Slide-Wright can correct these"
            note="Deterministic, and content is locked while they are applied."
          >
            {fixable.map((observation, index) => (
              <Row
                key={`${observation.area}-${index}`}
                observation={observation}
                onGoToSlide={onGoToSlide}
              />
            ))}
            {plan && (
              <TidyAction
                plan={plan}
                busy={busy}
                template={template}
                onTemplate={setTemplate}
                onTidy={() => onTidy(template)}
              />
            )}
          </Section>
        )}

        {advisory.length > 0 && (
          <Section
            title="For you to decide"
            note="Judgements about the deck's argument. Slide-Wright will not touch them."
          >
            {advisory.map((observation, index) => (
              <Row
                key={`${observation.area}-${index}`}
                observation={observation}
                onGoToSlide={onGoToSlide}
              />
            ))}
          </Section>
        )}

        {audit.gate.findings.length > 0 && <GateSection audit={audit} onGoToSlide={onGoToSlide} />}
      </motion.div>
    </>
  );
}

function byArea(observations: Observation[]): Observation[] {
  const rank = (o: Observation) => {
    const index = AREA_ORDER.indexOf(o.area);
    // An area added to the engine later sorts last rather than first, so a new
    // rule never silently takes the top of the list.
    return index === -1 ? AREA_ORDER.length : index;
  };
  return [...observations].sort((a, b) => rank(a) - rank(b));
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
  observation,
  onGoToSlide,
}: {
  observation: Observation;
  onGoToSlide: (slide: number) => void;
}) {
  const first = observation.slides[0];

  return (
    <motion.article
      variants={enter}
      className="border-b border-line px-3 py-2 last:border-b-0"
    >
      <div className="mb-1 flex items-baseline gap-2">
        <Pill verdict={observation.severity === "error" ? "blocked" : "neutral"}>
          {AREA_LABEL[observation.area] ?? observation.area}
        </Pill>
        {first === undefined ? (
          <span className="text-evidence text-ink-faint">{observation.where}</span>
        ) : (
          <button
            type="button"
            onClick={() => onGoToSlide(first)}
            aria-label={`Go to slide ${first}: ${observation.message}`}
            className="text-evidence text-ink-faint underline decoration-dotted underline-offset-2 transition-colors duration-[120ms] hover:text-ink"
          >
            {observation.where}
          </button>
        )}
      </div>
      <p className="text-xs leading-snug text-ink">{observation.message}</p>
      {observation.suggestion && (
        <p className="mt-1 text-2xs leading-relaxed text-ink-faint">{observation.suggestion}</p>
      )}
    </motion.article>
  );
}

/**
 * The action, stated in the numbers it will actually act on.
 *
 * `worst_shift_in` against `tolerance_in` is the part worth showing. Alignment
 * only ever moves a shape onto a line its neighbours already sit on, so the
 * largest correction is bounded by construction — and a bound the reader can
 * see is the difference between "trust us" and "check it".
 */
function TidyAction({
  plan,
  busy,
  template,
  onTemplate,
  onTidy,
}: {
  plan: TidyPlan;
  busy: boolean;
  template: string;
  onTemplate: (path: string) => void;
  onTidy: () => void;
}) {
  const total = plan.typefaces + plan.nudges;

  return (
    <div className="border-b border-line bg-raised px-3 py-2.5">
      <Authority plan={plan} template={template} onTemplate={onTemplate} />
      {total === 0 && (
        <p className="text-2xs leading-relaxed text-ink-faint">
          Nothing departs from {plan.conforms_to}.
        </p>
      )}
      {/* Two lines, each one fact. The authority is stated once, above, rather
          than repeated inside every count -- "1,122 runs re-linked to the
          deck's own theme" said the same thing twice in a 288px column and
          wrapped to three lines doing it. */}
      <dl className={total === 0 ? "hidden" : "mb-2 space-y-1"}>
        {plan.typefaces > 0 && (
          <div className="flex items-baseline gap-2">
            <dt className="text-evidence w-10 shrink-0 text-right text-ink">
              {plan.typefaces}
            </dt>
            <dd className="text-2xs leading-relaxed text-ink-muted">
              run{plan.typefaces === 1 ? "" : "s"} re-linked to the theme
            </dd>
          </div>
        )}
        {plan.nudges > 0 && (
          <div className="flex items-baseline gap-2">
            <dt className="text-evidence w-10 shrink-0 text-right text-ink">
              {plan.nudges}
            </dt>
            <dd className="text-2xs leading-relaxed text-ink-muted">
              shape{plan.nudges === 1 ? "" : "s"} snapped onto a line their neighbours
              share
              <span className="mt-0.5 block text-ink-faint">
                largest movement{" "}
                <span className="text-evidence">{plan.worst_shift_in.toFixed(3)}in</span>{" "}
                of a <span className="text-evidence">{plan.tolerance_in.toFixed(2)}in</span>{" "}
                bound
              </span>
            </dd>
          </div>
        )}
      </dl>
      {total > 0 && (
        <>
          <Button tone="primary" onClick={onTidy} busy={busy}>
            Propose {total} correction{total === 1 ? "" : "s"}
          </Button>
          <p className="mt-1.5 text-2xs leading-relaxed text-ink-faint">
            Proposes only. You review each one before anything is written, and content
            is locked throughout.
          </p>
        </>
      )}
    </div>
  );
}

/**
 * Which standard is being conformed to.
 *
 * With no template the deck's own theme is the authority, which is right for
 * something assembled from several sources: it already has a visual system, and
 * the pasted-in slides are what departs from it. With a template the house
 * standard becomes the authority instead — the other real workflow, an
 * inherited deck that has to end up looking like ours.
 *
 * The two produce very different sets of changes, so the authority is named
 * rather than implied. A reviewer approving two hundred typeface corrections
 * needs to know which standard they are approving against.
 */
function Authority({
  plan,
  template,
  onTemplate,
}: {
  plan: TidyPlan;
  template: string;
  onTemplate: (path: string) => void;
}) {
  const [draft, setDraft] = useState(template);
  const [open, setOpen] = useState(Boolean(template));

  return (
    <div className="mb-2.5">
      {/* A labelled row rather than a sentence. Three facts -- the authority,
          its typefaces, and the way to change it -- ran together in one line
          that wrapped to three in a narrow column and read as mush. */}
      <p className="text-2xs uppercase tracking-[0.08em] text-ink-faint">
        Conforming to
      </p>
      <div className="mt-0.5 flex items-baseline justify-between gap-2">
        <span className="text-evidence min-w-0 truncate text-ink" title={plan.conforms_to}>
          {plan.conforms_to}
        </span>
        {!open && (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="shrink-0 text-2xs text-ink-faint underline decoration-dotted underline-offset-2 transition-colors duration-[120ms] hover:text-ink"
          >
            use a template
          </button>
        )}
      </div>
      {plan.fonts.length > 0 && (
        <p className="text-evidence mt-0.5 truncate text-ink-faint" title={plan.fonts.join(", ")}>
          {plan.fonts.join(" · ")}
        </p>
      )}

      {open && (
        <div className="mt-1.5 flex gap-1.5">
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && onTemplate(draft.trim())}
            placeholder={TEMPLATE_PLACEHOLDER}
            spellCheck={false}
            aria-label="Path to a .potx or .pptx to conform to"
            className="text-evidence min-w-0 flex-1 rounded-md border border-line-strong bg-panel px-2 py-1 text-ink placeholder:text-ink-faint focus:border-changed-dim focus:outline-none"
          />
          <Button onClick={() => onTemplate(draft.trim())}>Use</Button>
          {template && (
            <Button
              tone="quiet"
              onClick={() => {
                setDraft("");
                onTemplate("");
                setOpen(false);
              }}
            >
              Clear
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * The delivery gate, kept separate.
 *
 * It answers "may this be delivered" — about an edit that already happened —
 * where everything above answers "what should change". Merging them would put a
 * blocking error next to a note about slide titles as though they were the same
 * kind of thing.
 */
function GateSection({
  audit,
  onGoToSlide,
}: {
  audit: Audit;
  onGoToSlide: (slide: number) => void;
}) {
  return (
    <Section
      title="Delivery gate"
      note="Whether an edited copy of this deck could be handed over."
    >
      {audit.gate.findings.map((finding, index) => (
        <motion.article
          key={`${finding.code}-${index}`}
          variants={enter}
          className="border-b border-line px-3 py-2 last:border-b-0"
        >
          <div className="mb-1 flex items-baseline gap-2">
            <Pill verdict={finding.severity === "error" ? "blocked" : "neutral"}>
              {finding.severity}
            </Pill>
            <button
              type="button"
              onClick={() => onGoToSlide(finding.slide)}
              aria-label={`Go to slide ${finding.slide}: ${finding.message}`}
              className="text-evidence text-ink-faint underline decoration-dotted underline-offset-2 transition-colors duration-[120ms] hover:text-ink"
            >
              slide {finding.slide}
              {finding.shape_name && ` · ${finding.shape_name}`}
            </button>
          </div>
          <p className="text-xs leading-snug text-ink">{finding.message}</p>
          {finding.repair && (
            <p className="mt-1 text-2xs leading-relaxed text-ink-faint">fix: {finding.repair}</p>
          )}
        </motion.article>
      ))}
    </Section>
  );
}
