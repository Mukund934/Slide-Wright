/**
 * The audit, tested for the two things it must never do.
 *
 * Show a score, and let a judgement look like a fix. Both are ways of making
 * findings seem more authoritative than they are, which is the failure the
 * engine is deterministic in order to avoid — and it would be undone here, in
 * the surface, without a line of engine code changing.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Audit, Observation, TidyPlan } from "../api/types";
import { AuditPanel } from "./AuditPanel";

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  api: { audit: vi.fn(), tidyPlan: vi.fn() },
}));

const { api } = await import("../api/client");

function observation(over: Partial<Observation> = {}): Observation {
  return {
    area: "narrative", slides: [], where: "deck", message: "something is true",
    suggestion: "", severity: "warning", remedy: "", is_automatable: false, ...over,
  };
}

function audit(over: Partial<Audit> = {}): Audit {
  return {
    deck: "d.pptx", slide_count: 26, word_count: 950, words_per_slide: 36.5,
    observations: [], automatable_count: 0, rendered: "",
    gate: { passed: true, error_count: 0, warning_count: 0, findings: [] },
    ...over,
  };
}

function plan(over: Partial<TidyPlan> = {}): TidyPlan {
  return {
    typefaces: 266, nudges: 6, tolerance_in: 0.02, worst_shift_in: 0.017,
    skipped: [], conforms_to: "the deck's own theme", fonts: [], ...over,
  };
}

function mount(nextAudit: Audit, nextPlan: TidyPlan = plan(), onTidy = vi.fn()) {
  vi.mocked(api.audit).mockResolvedValue(nextAudit);
  vi.mocked(api.tidyPlan).mockResolvedValue(nextPlan);
  render(
    <AuditPanel documentId="doc" busy={false} onGoToSlide={vi.fn()} onTidy={onTidy} />,
  );
}

beforeEach(() => vi.clearAllMocks());

describe("there is no score", () => {
  it("states the measurements and grades none of them", async () => {
    // A single number would compress "no title makes a claim" and "266 runs
    // hardcode a typeface" into one figure that means neither.
    mount(audit({ observations: [observation()] }));
    await screen.findByText(/something is true/);

    const panel = screen.getByText(/per slide/).parentElement as HTMLElement;
    expect(panel.textContent).toContain("26");
    expect(panel.textContent).toContain("950");
    expect(panel.textContent).not.toMatch(/\b(?:score|grade|rating|\/\s*100|[A-F][+-]?\b)/i);
  });
});

describe("fixable and advisory do not look alike", () => {
  const mixed = audit({
    automatable_count: 1,
    observations: [
      observation({
        area: "consistency", message: "266 runs hardcode a typeface",
        remedy: "conformance", is_automatable: true,
      }),
      observation({ area: "narrative", message: "26 slides have no title" }),
    ],
  });

  it("separates them into their own sections", async () => {
    mount(mixed);
    expect(await screen.findByText(/Slide-Wright can correct these/)).toBeInTheDocument();
    expect(screen.getByText(/For you to decide/)).toBeInTheDocument();
  });

  it("offers the action only where the engine said it can act", async () => {
    mount(mixed);
    const fixable = (await screen.findByText(/Slide-Wright can correct these/))
      .closest("section") as HTMLElement;
    expect(within(fixable).getByRole("button", { name: /Propose/ })).toBeInTheDocument();

    const advisory = screen.getByText(/For you to decide/).closest("section") as HTMLElement;
    expect(within(advisory).queryByRole("button", { name: /Propose/ })).toBeNull();
  });

  it("offers nothing at all when the engine can fix nothing", async () => {
    // Both halves empty. The audit alone is not enough to answer this — see
    // the test below.
    mount(
      audit({ observations: [observation({ message: "26 slides have no title" })] }),
      plan({ typefaces: 0, nudges: 0 }),
    );
    await screen.findByText(/26 slides have no title/);
    expect(screen.queryByText(/Slide-Wright can correct these/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Propose/ })).toBeNull();
  });

  it("still offers the tidy when the plan has work the audit did not name", async () => {
    /**
     * Two computations: the audit's rules decide what to report, the tidy
     * planner decides what to correct. Gated on the audit alone, the action
     * disappeared whenever they disagreed.
     *
     * Measured on `tspptx-mixed.pptx`: three corrections available, no
     * automatable finding, and therefore no way to reach them. The
     * classification bug behind that one is fixed — but the two can drift
     * again, and the reader should not be the one who pays for it.
     */
    mount(
      audit({ observations: [observation({ message: "26 slides have no title" })] }),
      plan({ typefaces: 3, nudges: 0 }),
    );
    expect(
      await screen.findByRole("button", { name: /Propose 3 correction/ }),
    ).toBeInTheDocument();
  });

  it("says plainly that the advisory ones will not be touched", async () => {
    mount(mixed);
    expect(await screen.findByText(/will not touch them/)).toBeInTheDocument();
  });
});

describe("the tidy action", () => {
  const fixable = audit({
    automatable_count: 1,
    observations: [
      observation({ area: "layout", remedy: "alignment", is_automatable: true,
                    message: "6 shapes sit within 0.02in of an edge others share" }),
    ],
  });

  it("shows the bound alongside the movement, so it can be checked", async () => {
    // Alignment only ever moves a shape onto a line its neighbours share, so
    // the largest correction is bounded by construction. Showing both is the
    // difference between "trust us" and "check it".
    mount(fixable, plan({ worst_shift_in: 0.017, tolerance_in: 0.02 }));
    expect(await screen.findByText("0.017in")).toBeInTheDocument();
    expect(screen.getByText("0.02in")).toBeInTheDocument();
  });

  it("says it proposes only, and that review comes first", async () => {
    mount(fixable);
    expect(await screen.findByText(/You review each one before anything is written/))
      .toBeInTheDocument();
    expect(screen.getByText(/content is locked throughout/)).toBeInTheDocument();
  });

  it("counts both kinds of correction in the button", async () => {
    mount(fixable, plan({ typefaces: 266, nudges: 6 }));
    expect(await screen.findByRole("button", { name: /Propose 272 corrections/ }))
      .toBeInTheDocument();
  });

  it("hands the decision upward rather than acting on its own", async () => {
    const onTidy = vi.fn();
    mount(fixable, plan(), onTidy);
    await userEvent.click(await screen.findByRole("button", { name: /Propose/ }));
    expect(onTidy).toHaveBeenCalledOnce();
  });

  it("offers no action when there is nothing to correct", async () => {
    mount(fixable, plan({ typefaces: 0, nudges: 0 }));
    await screen.findByText(/6 shapes sit within/);
    expect(screen.queryByRole("button", { name: /Propose/ })).toBeNull();
  });
});

describe("the delivery gate", () => {
  it("is shown apart from the audit, because it answers another question", async () => {
    mount(
      audit({
        observations: [observation()],
        gate: {
          passed: false, error_count: 1, warning_count: 0,
          findings: [{
            code: "bounds.horizontal", severity: "error", slide: 4,
            message: "extends 0.21in past the right edge",
            repair: "reduce width to at most 9.10in", shape_id: "7", shape_name: "Rectangle 4",
          }],
        },
      }),
    );
    const gate = (await screen.findByText(/Delivery gate/)).closest("section") as HTMLElement;
    expect(within(gate).getByText(/extends 0.21in past the right edge/)).toBeInTheDocument();
    expect(within(gate).queryByText(/something is true/)).toBeNull();
  });

  it("is absent when it found nothing", async () => {
    mount(audit({ observations: [observation()] }));
    await screen.findByText(/something is true/);
    expect(screen.queryByText(/Delivery gate/)).toBeNull();
  });
});

describe("navigation", () => {
  it("makes a finding's slides a way to get to them", async () => {
    const onGoToSlide = vi.fn();
    vi.mocked(api.audit).mockResolvedValue(
      audit({ observations: [observation({ slides: [8, 9], where: "slide 8, 9" })] }),
    );
    vi.mocked(api.tidyPlan).mockResolvedValue(plan());
    render(
      <AuditPanel documentId="doc" busy={false} onGoToSlide={onGoToSlide} onTidy={vi.fn()} />,
    );

    await userEvent.click(await screen.findByRole("button", { name: /Go to slide 8/ }));
    expect(onGoToSlide).toHaveBeenCalledWith(8);
  });

  it("offers no link for a deck-wide finding", async () => {
    mount(audit({ observations: [observation({ slides: [], where: "deck" })] }));
    await screen.findByText("deck");
    expect(screen.queryByRole("button", { name: /Go to slide/ })).toBeNull();
  });
});

describe("when the audit cannot run", () => {
  it("says why rather than showing an empty deck", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(api.audit).mockRejectedValue(new ApiError(404, "that document is not open"));
    vi.mocked(api.tidyPlan).mockRejectedValue(new ApiError(404, "that document is not open"));
    render(
      <AuditPanel documentId="doc" busy={false} onGoToSlide={vi.fn()} onTidy={vi.fn()} />,
    );

    await waitFor(() =>
      expect(screen.getByText("that document is not open")).toBeInTheDocument(),
    );
  });
});

describe("which standard is being conformed to", () => {
  const fixable = audit({
    automatable_count: 1,
    observations: [
      observation({ area: "consistency", remedy: "conformance", is_automatable: true,
                    message: "266 runs hardcode a typeface" }),
    ],
  });

  it("names the authority rather than implying it", async () => {
    // "the deck's own theme" and "House.potx" produce very different sets of
    // changes. A reviewer approving 266 corrections needs to know which.
    mount(fixable, plan({ conforms_to: "House.potx", fonts: ["Calibri", "Georgia"] }));
    expect(await screen.findByText("House.potx")).toBeInTheDocument();
    // Separated with the middot the rest of the app uses between facts.
    expect(screen.getByText(/Calibri · Georgia/)).toBeInTheDocument();
  });

  it("defaults to the deck's own theme and offers a template", async () => {
    mount(fixable);
    expect(await screen.findByText("the deck's own theme")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /use a template/ })).toBeInTheDocument();
  });

  it("re-plans against the template it is given", async () => {
    mount(fixable);
    await userEvent.click(await screen.findByRole("button", { name: /use a template/ }));
    await userEvent.type(
      screen.getByLabelText(/Path to a \.potx/),
      "C:\House.potx",
    );
    await userEvent.click(screen.getByRole("button", { name: "Use" }));

    await waitFor(() =>
      expect(vi.mocked(api.tidyPlan)).toHaveBeenLastCalledWith("doc", "C:\House.potx"),
    );
  });

  it("says nothing departs from the standard rather than hiding the answer", async () => {
    // A deck that already conforms is a real and useful result. Showing nothing
    // at all leaves the reader unable to tell it from a check that never ran.
    mount(fixable, plan({ typefaces: 0, nudges: 0, conforms_to: "House.potx" }));
    expect(await screen.findByText(/Nothing departs from/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Propose/ })).toBeNull();
  });
});
