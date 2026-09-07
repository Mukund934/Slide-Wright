/**
 * The workspace.
 *
 * Three regions, one job each, exactly as the UX architecture lays them out:
 * the filmstrip shows where the change is not, the canvas shows the slide with
 * changed regions outlined and nothing else decorated, and the right-hand panel
 * shows the contract — before application, not only after.
 *
 * There is no dashboard, and there is no route to one. The deck is the
 * interface.
 */

import { motion } from "motion/react";
import { useCallback, useEffect, useState } from "react";

import { api } from "./api/client";
import type { Health, LockSpec } from "./api/types";
import { Button, Pill } from "./design/primitives";
import { reveal } from "./motion/tokens";
import { useWorkspace } from "./state/workspace";
import { AuditPanel } from "./workspace/AuditPanel";
import { ChangeSetPanel } from "./workspace/ChangeSetPanel";
import { DiffPanel } from "./workspace/DiffPanel";
import { CommandBar } from "./workspace/CommandBar";
import { Export } from "./workspace/Export";
import { Filmstrip } from "./workspace/Filmstrip";
import { History } from "./workspace/History";
import { OpenDeck } from "./workspace/OpenDeck";
import { SlideCanvas } from "./workspace/SlideCanvas";
import { SourcesPanel } from "./workspace/SourcesPanel";
import { VerificationPanel } from "./workspace/VerificationPanel";

type RightTab = "audit" | "sources" | "changes" | "history";

export default function App() {
  const workspace = useWorkspace();
  const [health, setHealth] = useState<Health | null>(null);
  const [tab, setTab] = useState<RightTab>("audit");

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  // A settled apply is the moment the review panel stops being the thing to
  // look at, so the panel follows the work rather than making the user find it.
  useEffect(() => {
    if (workspace.phase === "settled") setTab("changes");
  }, [workspace.phase]);

  if (!workspace.document) {
    return (
      <OpenDeck
        busy={workspace.phase === "opening"}
        error={workspace.error}
        onOpen={workspace.open}
      />
    );
  }

  return (
    <div className="flex h-full flex-col bg-ground">
      <TopBar
        name={workspace.document.name}
        workspacePath={workspace.document.workspace}
        health={health}
        documentId={workspace.document.id}
        version={workspace.document.versions.at(-1)?.number ?? 0}
      />

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-36 shrink-0 flex-col border-r border-line bg-panel lg:w-44 xl:w-56">
          <Filmstrip
            slides={workspace.document.deck.slides}
            selected={workspace.selectedSlide}
            changed={workspace.changedSlides}
            onSelect={(n) => workspace.select(n, null)}
          />
          <Untouched
            total={workspace.document.deck.slides.length}
            changed={workspace.changedSlides.size}
          />
        </aside>

        {/* The canvas is what needs width, so the canvas is what goes — but
            later than it used to. At 1024 the threshold was costing the canvas
            to every window that was not close to full screen, including the
            ordinary half-of-a-laptop case. The measurement that settled it: at
            768 the two panels take 400px, leaving ~370 for the slide, which is
            enough to see *where* a change landed — the only thing this canvas
            claims to show.
            Below that the three regions cannot all be useful at once, and
            cramming them makes all three useless rather than one absent. What
            survives is the review, which is the part someone is most likely to
            be doing on a small screen anyway. */}
        <main className="hidden min-w-0 flex-1 flex-col md:flex">
          <Stage workspace={workspace} />
        </main>

        <aside className="flex min-w-0 flex-1 flex-col border-l border-line bg-panel md:w-64 md:flex-none lg:w-72 xl:w-80">
          {!workspace.comparison && <Tabs tab={tab} onChange={setTab} />}

          <div
            id="deck-panel"
            role={workspace.comparison ? undefined : "tabpanel"}
            aria-labelledby={workspace.comparison ? undefined : `tab-${tab}`}
            className="flex min-h-0 flex-1 flex-col"
          >
            {workspace.comparison ? (
              <DiffPanel
                comparison={workspace.comparison}
                onGoTo={(delta) => workspace.select(delta.slide, delta.shape_id, true)}
                onFlip={workspace.flip}
                onClose={workspace.stopComparing}
              />
            ) : tab === "audit" ? (
              <AuditPanel
                documentId={workspace.document.id}
                busy={workspace.phase === "proposing" || workspace.phase === "applying"}
                onGoToSlide={(n) => workspace.select(n, null)}
                onTidy={(template) => {
                  // Land the reviewer where the decision is. Proposing and then
                  // leaving them on the audit would hide the thing they now
                  // have to approve.
                  void workspace.tidy(template);
                  setTab("changes");
                }}
              />
            ) : tab === "sources" ? (
              <SourcesPanel
                documentId={workspace.document.id}
                busy={workspace.phase === "proposing" || workspace.phase === "applying"}
                onGoToSlide={(n) => workspace.select(n, null)}
                onRefresh={(sources) => {
                  // Same as tidy: land the reviewer where the decision is.
                  void workspace.refresh(sources);
                  setTab("changes");
                }}
              />
            ) : tab === "changes" ? (
              <ChangeSetPanel
                changeset={workspace.changeset}
                busy={workspace.phase === "applying"}
                onGoTo={workspace.goToChange}
                onApprove={(id) => workspace.review({ approve: [id] })}
                onReject={(id) => workspace.review({ reject: [id] })}
                onApproveAll={(includeUnreviewed) =>
                  workspace.review({
                    approve_all: true,
                    include_unreviewed: includeUnreviewed,
                  })
                }
              />
            ) : (
              <History
                versions={workspace.document.versions}
                busy={workspace.phase === "applying"}
                onRevert={workspace.revert}
                onCompare={workspace.compare}
              />
            )}
          </div>

          {!workspace.comparison && (
            <VerificationPanel
            verification={workspace.verification}
            progress={workspace.progress}
            applying={workspace.phase === "applying"}
            onGoToSlide={(n) => workspace.select(n, null)}
            />
          )}

          {workspace.canApply && !workspace.comparison && (
            <div className="shrink-0 border-t border-line p-2">
              <Button
                tone="primary"
                className="w-full"
                busy={workspace.phase === "applying"}
                onClick={() => workspace.apply(workspace.changeset?.instruction ?? "")}
              >
                Apply {workspace.approved} approved change
                {workspace.approved === 1 ? "" : "s"}
              </Button>
            </div>
          )}
        </aside>
      </div>

      <CommandBar
        scopeLabel={scopeLabel(workspace)}
        modelConfigured={health?.model_configured ?? false}
        modelName={health?.model ?? "none"}
        busy={workspace.phase === "proposing" || workspace.phase === "applying"}
        onPropose={(instruction: string, locks: LockSpec[]) =>
          workspace.propose({ instruction, locks })
        }
        onClearSelection={() => workspace.select(workspace.selectedSlide, null)}
      />

      <ErrorBar message={workspace.error} onDismiss={workspace.dismissError} />
    </div>
  );
}

function TopBar({
  name,
  workspacePath,
  health,
  documentId,
  version,
}: {
  name: string;
  workspacePath: string;
  health: Health | null;
  documentId: string;
  version: number;
}) {
  return (
    <header className="flex h-10 shrink-0 items-center justify-between border-b border-line bg-panel px-3">
      <div className="flex min-w-0 items-baseline gap-2">
        {/* The deck is what this document is about, so it is the h1. Without
            one the outline started at h3 and a screen-reader user arrived with
            no idea what they were looking at. */}
        <h1 className="truncate text-xs font-medium text-ink">{name}</h1>
        <span className="text-evidence truncate text-ink-faint" title={workspacePath}>
          {workspacePath}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {health && !health.model_configured && <Pill>offline · deterministic only</Pill>}
        <span className="text-evidence hidden text-ink-faint lg:inline">
          engine {health?.engine ?? "…"}
        </span>
        <Export documentId={documentId} version={version} />
      </div>
    </header>
  );
}

/**
 * The count that is the product's actual argument.
 *
 * Placed under the filmstrip rather than in the report, because it is true
 * before the apply as well as after: this is what is *not* going to be touched.
 */
function Untouched({ total, changed }: { total: number; changed: number }) {
  const untouched = total - changed;
  return (
    <div className="shrink-0 border-t border-line px-3 py-2">
      <p className="text-2xs text-ink-faint">
        <span className="text-evidence text-ink-muted">{untouched}</span> of{" "}
        <span className="text-evidence text-ink-muted">{total}</span> slides
        untouched
      </p>
    </div>
  );
}

const TABS = ["audit", "sources", "changes", "history"] as const;

function Tabs({ tab, onChange }: { tab: RightTab; onChange: (t: RightTab) => void }) {
  return (
    <div
      role="tablist"
      aria-label="Deck panels"
      className="flex h-9 shrink-0 items-stretch border-b border-line"
      onKeyDown={(event) => {
        // Arrow keys move between tabs, which is what the role promises. A
        // tablist that only responds to clicks is a row of buttons wearing a
        // costume.
        const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
        if (!step) return;
        event.preventDefault();
        const next = TABS[(TABS.indexOf(tab) + step + TABS.length) % TABS.length];
        if (next) onChange(next);
      }}
    >
      {TABS.map((value) => (
        <button
          key={value}
          id={`tab-${value}`}
          role="tab"
          aria-selected={tab === value}
          aria-controls="deck-panel"
          // Only the selected tab is in the tab order; arrows move within.
          tabIndex={tab === value ? 0 : -1}
          onClick={() => onChange(value)}
          className={[
            "relative flex-1 truncate px-1 text-2xs font-medium uppercase tracking-[0.06em]",
            "transition-colors duration-[120ms]",
            tab === value
              ? "text-ink"
              : "text-ink-faint hover:text-ink-muted",
          ].join(" ")}
        >
          {value}
          {tab === value && (
            // One shared element sliding between tabs, rather than two
            // fading. The movement is the continuity: it says these are the
            // same control in two positions.
            <motion.span
              layoutId="tab-underline"
              className="absolute inset-x-3 bottom-0 h-px bg-ink"
              transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
            />
          )}
        </button>
      ))}
    </div>
  );
}

function Stage({ workspace }: { workspace: ReturnType<typeof useWorkspace> }) {
  const { slide, document: doc } = workspace;
  const [zoom, setZoom] = useState(1);

  // Fit the slide to the stage on mount and on resize. Doing it with a CSS
  // variable rather than React state keeps a resize from re-rendering every
  // shape on the canvas.
  const stageRef = useCallback(
    (node: HTMLDivElement | null) => {
      if (!node || !doc) return;
      const fit = () => {
        const width = doc.deck.slide_width / 9525 || 960;
        const height = doc.deck.slide_height / 9525 || 540;
        const scale = Math.min(
          (node.clientWidth - 64) / width,
          (node.clientHeight - 64) / height,
          1.5,
        );
        node.style.setProperty("--canvas-scale", String(Math.max(scale, 0.1) * zoom));
      };
      fit();
      const observer = new ResizeObserver(fit);
      observer.observe(node);
      return () => observer.disconnect();
    },
    [doc, zoom],
  );

  // Clear the carry once it has played, so re-selecting the same shape can
  // play it again.
  useEffect(() => {
    if (!workspace.carriedShape) return;
    const timer = setTimeout(workspace.arrived, 500);
    return () => clearTimeout(timer);
  }, [workspace.carriedShape, workspace.arrived]);

  return (
    <div
      ref={stageRef}
      className="relative flex min-h-0 flex-1 items-center justify-center overflow-auto p-8"
    >
      {/* Keyed, and rendered plainly.
          This was an AnimatePresence with mode="wait", which gates the incoming
          slide on the outgoing one finishing its exit. The exit did not finish,
          so the canvas stuck: the filmstrip moved to slide 10 and the canvas
          went on showing slide 1, permanently. On the surface whose entire job
          is proving what did and did not change, that is the worst defect the
          application could have — a reviewer would have been checking a change
          against the wrong slide.

          A slide swap needs no exit. Changing the key remounts, the entrance
          plays, and clicking a slide shows that slide with nothing to wait for. */}
      {slide && doc && (
        <motion.div
          key={slide.number}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          // Fast enough that moving through a filmstrip never feels gated on an
          // animation, present enough that the swap is not a jump cut.
          transition={{ duration: 0.12 }}
        >
          <SlideCanvas
            slide={slide}
            slideWidth={doc.deck.slide_width}
            slideHeight={doc.deck.slide_height}
            changedShapes={workspace.changedShapes}
            carriedShape={workspace.carriedShape}
            selectedShape={workspace.selectedShape}
            onSelectShape={(id) => workspace.select(workspace.selectedSlide, id)}
          />
        </motion.div>
      )}

      <div className="absolute bottom-3 right-3 flex items-center gap-1">
        {workspace.comparison && (
          // Which version is on screen, said on the canvas itself. The flip
          // control lives in the other panel, and a reader who has just
          // flipped needs to know what they are looking at without moving
          // their eyes back across the window.
          <span className="text-evidence mr-2 rounded-sm bg-changed-wash px-1.5 py-0.5 text-changed">
            showing v
            {String(
              workspace.comparison.showing === "before"
                ? workspace.comparison.from
                : workspace.comparison.to,
            ).padStart(3, "0")}
          </span>
        )}
        <StructuralNote />
        <Button tone="quiet" onClick={() => setZoom((z) => Math.max(0.5, z - 0.1))}>
          −
        </Button>
        <span className="text-evidence w-9 text-center text-ink-faint">
          {Math.round(zoom * 100)}%
        </span>
        <Button tone="quiet" onClick={() => setZoom((z) => Math.min(2, z + 0.1))}>
          +
        </Button>
      </div>
    </div>
  );
}

/**
 * What this canvas is, said where someone might otherwise assume.
 *
 * It draws every object where the OOXML says it is. It is not a PowerPoint
 * render, and a user comparing it against the real thing deserves to know that
 * before they conclude the engine moved something.
 */
function StructuralNote() {
  return (
    <span
      className="text-evidence mr-2 cursor-help text-ink-faint"
      title="Objects are drawn at the exact position and size the file specifies. Text colour, fills, effects, picture content and PowerPoint's line breaking are not reproduced — this is a structural view, not a render."
    >
      structural view
    </span>
  );
}

function scopeLabel(workspace: ReturnType<typeof useWorkspace>): string {
  if (workspace.selectedShape) {
    const shape = workspace.slide?.shapes.find((s) => s.id === workspace.selectedShape);
    return shape ? `${shape.kind} on slide ${workspace.selectedSlide}` : "one object";
  }
  return `slide ${workspace.selectedSlide}`;
}

function ErrorBar({ message, onDismiss }: { message: string | null; onDismiss: () => void }) {
  return (
    // Rendered plainly. An exit animation on an alert left it in the DOM at
    // opacity 0, and opacity does not hide anything from a screen reader --
    // a dismissed error would still have been announced.
    message && (
      <motion.div
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={reveal}
        role="alert"
        className="flex shrink-0 items-start gap-3 border-t border-blocked bg-blocked-wash px-3 py-2"
      >
        <p className="flex-1 text-xs leading-relaxed text-blocked">{message}</p>
        <Button tone="quiet" onClick={onDismiss}>
          Dismiss
        </Button>
      </motion.div>
    )
  );
}

export { scopeLabel };
