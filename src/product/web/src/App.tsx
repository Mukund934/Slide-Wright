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

import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useState } from "react";

import { api } from "./api/client";
import type { Health, LockSpec } from "./api/types";
import { Button, Pill } from "./design/primitives";
import { enter } from "./motion/tokens";
import { useWorkspace } from "./state/workspace";
import { AuditPanel } from "./workspace/AuditPanel";
import { ChangeSetPanel } from "./workspace/ChangeSetPanel";
import { CommandBar } from "./workspace/CommandBar";
import { Filmstrip } from "./workspace/Filmstrip";
import { History } from "./workspace/History";
import { OpenDeck } from "./workspace/OpenDeck";
import { SlideCanvas } from "./workspace/SlideCanvas";
import { VerificationPanel } from "./workspace/VerificationPanel";

type RightTab = "audit" | "changes" | "history";

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
      />

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-44 shrink-0 flex-col border-r border-line bg-panel xl:w-56">
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

        {/* The canvas is what needs width, so the canvas is what goes.
            Below a laptop the three regions cannot all be useful at once, and
            cramming them makes all three useless rather than one of them
            absent. What survives is the review — the change set, the verdict
            and the filmstrip — which is the part someone is most likely to be
            doing on a smaller screen anyway. */}
        <main className="hidden min-w-0 flex-1 flex-col lg:flex">
          <Stage workspace={workspace} />
        </main>

        <aside className="flex min-w-0 flex-1 flex-col border-l border-line bg-panel lg:w-72 lg:flex-none xl:w-80">
          <Tabs tab={tab} onChange={setTab} />

          <div className="flex min-h-0 flex-1 flex-col">
            {tab === "audit" ? (
              <AuditPanel
                documentId={workspace.document.id}
                busy={workspace.phase === "proposing" || workspace.phase === "applying"}
                onGoToSlide={(n) => workspace.select(n, null)}
                onTidy={() => {
                  // Land the reviewer where the decision is. Proposing and then
                  // leaving them on the audit would hide the thing they now
                  // have to approve.
                  void workspace.tidy();
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
              />
            )}
          </div>

          <VerificationPanel
            verification={workspace.verification}
            progress={workspace.progress}
            applying={workspace.phase === "applying"}
            onGoToSlide={(n) => workspace.select(n, null)}
          />

          {workspace.canApply && (
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
}: {
  name: string;
  workspacePath: string;
  health: Health | null;
}) {
  return (
    <header className="flex h-10 shrink-0 items-center justify-between border-b border-line bg-panel px-3">
      <div className="flex min-w-0 items-baseline gap-2">
        <span className="truncate text-xs font-medium text-ink">{name}</span>
        <span className="text-evidence truncate text-ink-faint" title={workspacePath}>
          {workspacePath}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {health && !health.model_configured && <Pill>offline · deterministic only</Pill>}
        <span className="text-evidence text-ink-faint">
          engine {health?.engine ?? "…"}
        </span>
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

function Tabs({ tab, onChange }: { tab: RightTab; onChange: (t: RightTab) => void }) {
  return (
    <div
      role="tablist"
      className="flex h-9 shrink-0 items-stretch border-b border-line"
    >
      {(["audit", "changes", "history"] as const).map((value) => (
        <button
          key={value}
          role="tab"
          aria-selected={tab === value}
          onClick={() => onChange(value)}
          className={[
            "relative flex-1 text-2xs font-medium uppercase tracking-[0.08em]",
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
      <AnimatePresence mode="wait">
        {slide && doc && (
          <motion.div
            key={slide.number}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            // Fast enough that moving through a filmstrip does not feel gated
            // on an animation, present enough that the swap is not a jump cut.
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
      </AnimatePresence>

      <div className="absolute bottom-3 right-3 flex items-center gap-1">
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
    <AnimatePresence>
      {message && (
        <motion.div
          variants={enter}
          initial="hidden"
          animate="shown"
          exit="gone"
          role="alert"
          className="flex shrink-0 items-start gap-3 border-t border-blocked bg-blocked-wash px-3 py-2"
        >
          <p className="flex-1 text-xs leading-relaxed text-blocked">{message}</p>
          <Button tone="quiet" onClick={onDismiss}>
            Dismiss
          </Button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export { scopeLabel };
