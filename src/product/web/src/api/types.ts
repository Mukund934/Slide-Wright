/**
 * The wire shapes, mirroring `slide_wright_api/contracts.py`.
 *
 * Kept by hand rather than generated, for now, and that is a deliberate debt:
 * generation needs a schema step in both toolchains, and until the surface
 * stops moving the step would cost more than the drift. The API tests are what
 * actually catch a mismatch, because they assert on the same field names.
 *
 * Nothing here is a guess about engine behaviour. Every derived fact --
 * `deliverable`, `isGrounded`, `needsReview` -- arrives already decided.
 */

export type ChangeStatus = "proposed" | "approved" | "rejected" | "applied" | "failed";
export type ChangeOrigin = "user" | "source" | "model" | "rule";
export type Severity = "error" | "warning";

export type Op =
  | "set_text"
  | "set_table_cell"
  | "move"
  | "resize"
  | "set_font_size"
  | "set_font"
  | "set_color"
  | "delete_shape";

/** The guarantees a user can declare. Each is enforced at the engine, not the UI. */
export const LOCK_SCOPES = [
  "slide",
  "shape",
  "numbers",
  "wording",
  "layout",
  "formatting",
  "tables",
  "charts",
  "media",
] as const;

export type LockScope = (typeof LOCK_SCOPES)[number];

export interface Run {
  text: string;
  size_pt: number | null;
  bold: boolean;
  italic: boolean;
  font: string | null;
  color: string | null;
  /**
   * Which paragraph of the shape this run belongs to.
   *
   * Runs are a formatting split, not a line break — "Revenue grew **15%** in
   * FY25" is three runs and one line. Paragraphs are the line breaks, and the
   * canvas needs both to draw a shape the way PowerPoint does.
   */
  paragraph: number;
}

export interface Shape {
  id: string;
  name: string;
  kind: string;
  placeholder_type: string | null;
  /** EMU. The slide's own dimensions come alongside; convert at render time. */
  x: number | null;
  y: number | null;
  cx: number | null;
  cy: number | null;
  rotation_deg: number | null;
  /** The box came from the layout or master; the shape cannot be moved. */
  geometry_inherited: boolean;
  geometry: string | null;
  runs: Run[];
  table_rows: number;
  table_cols: number;
  /** Keyed "r0/c0", zero-based, matching the suffix a change target carries. */
  table_cells: Record<string, string>;
  child_count: number;
  text: string;
}

export interface Slide {
  number: number;
  part_name: string;
  layout: string | null;
  title: string | null;
  word_count: number;
  shapes: Shape[];
}

export interface Deck {
  slide_width: number;
  slide_height: number;
  theme_fonts: Record<string, string>;
  slides: Slide[];
}

export interface Change {
  id: string;
  op: Op;
  slide: number;
  target: string;
  before: unknown;
  after: unknown;
  rationale: string;
  status: ChangeStatus;
  origin: ChangeOrigin;
  citation: string;
  confidence: number;
  impact: string;
  object_kind: string;
  description: string;
  /** Traces to something checkable: a person typed it, or a cell says so. */
  is_grounded: boolean;
  /** A model invented it and cited nothing. Never auto-approved. */
  needs_review: boolean;
}

export interface Lock {
  scope: LockScope;
  target: string;
  reason: string;
}

export interface ChangeSet {
  deck: string;
  instruction: string;
  changes: Change[];
  locks: Lock[];
  proposed_count: number;
  approved_count: number;
  rejected_count: number;
  applied_count: number;
  needs_review_count: number;
}

export interface Finding {
  code: string;
  severity: Severity;
  slide: number;
  message: string;
  repair: string;
  shape_id: string;
  shape_name: string;
}

/** May this be delivered? A per-slide, pass/fail question about an edit. */
export interface Gate {
  passed: boolean;
  error_count: number;
  warning_count: number;
  findings: Finding[];
}

export type Area =
  | "structure"
  | "narrative"
  | "consistency"
  | "evidence"
  | "layout"
  | "accessibility";

/** What can correct a finding without a person deciding. "" means nothing can. */
export type Remedy = "" | "conformance" | "alignment";

export interface Observation {
  area: Area;
  slides: number[];
  where: string;
  message: string;
  suggestion: string;
  severity: Severity;
  /** The engine's answer, never recomputed here. */
  remedy: Remedy;
  is_automatable: boolean;
}

/** What should change? Asked of a deck nobody has touched yet. */
export interface Audit {
  deck: string;
  slide_count: number;
  word_count: number;
  words_per_slide: number;
  observations: Observation[];
  gate: Gate;
  automatable_count: number;
  rendered: string;
}

/**
 * Where an export would go, and whether one is allowed at all.
 *
 * `deliverable` is asked before the user types a path. Offering the field and
 * rejecting the submission would be technically identical and much worse: it
 * makes a verdict about the deck look like a mistake in what they typed.
 */
export interface ExportTarget {
  suggested: string;
  deliverable: boolean;
  blocking_reasons: string[];
  version: number;
}

/** One deck cell a source row and column pair explains. */
export interface Match {
  slide: number;
  target: string;
  current: string;
  /** Empty for a confirmed cell: the source and the deck already agree. */
  proposed: string;
  citation: string;
}

/**
 * What a refresh would do, before anything is proposed.
 *
 * Three outcomes, all shown. `confirmed` is positive evidence a figure is still
 * right; without it a reader cannot tell "checked and correct" from "never
 * looked at". `unmatched` is what the source could not explain and what was
 * therefore left alone.
 */
export interface RefreshPlan {
  sources: string[];
  tables: number;
  updates: Match[];
  confirmed: Match[];
  /**
   * The source was found, holds a different figure, and writing it would change
   * what the cell says rather than what it reports — a percentage against a raw
   * decimal, or a blank cell. Shown, never applied, and deliberately not mixed
   * in with `unmatched`: "the source disagrees" is not "no match".
   */
  refused: string[];
  unmatched: string[];
  rendered: string;
}

/** What a tidy pass would change, before anything is proposed. */
export interface TidyPlan {
  typefaces: number;
  nudges: number;
  tolerance_in: number;
  /** Can never exceed the tolerance: alignment only moves onto an existing line. */
  worst_shift_in: number;
  skipped: string[];
  /** "the deck's own theme", or the name of the template supplied. */
  conforms_to: string;
  fonts: string[];
}

export interface CensusRow {
  label: string;
  source: number;
  output: number;
  intact: boolean;
}

export interface RequestedChange {
  slide: number;
  description: string;
  target: string;
}

export interface Verification {
  /** The engine's fail-closed verdict. Never recomputed here. */
  deliverable: boolean;
  identical_parts: number;
  total_parts: number;
  fidelity_score: number;
  changed_parts: string[];
  changed_slides: number[];
  untouched_slides: number;
  /** Slides that moved without a change-set entry authorising it. */
  unrequested_slides: number[];
  unrequested_parts: string[];
  blocking_reasons: string[];
  requested: RequestedChange[];
  census: CensusRow[];
  rendered: string;
}

export interface Version {
  number: number;
  created_at: string;
  note: string;
  changes: string[];
  is_original: boolean;
  is_current: boolean;
}

export interface SlideDocument {
  id: string;
  name: string;
  workspace: string;
  deck: Deck;
  versions: Version[];
  changeset: ChangeSet | null;
  verification: Verification | null;
}

export interface Health {
  ok: boolean;
  api: string;
  engine: string;
  /** "gemini" | "anthropic" | "stub". Stub means no key: the deterministic
   *  half of the product is unaffected, and the UI says so rather than
   *  offering a box that silently proposes nothing. */
  model: string;
  model_configured: boolean;
  open_documents: number;
}

export interface Delta {
  slide: number;
  shape_id: string;
  kind: string;
  description: string;
  /**
   * The change with the location removed: `font 'Century Gothic' -> '+mn-lt'`
   * rather than `TextBox 4 (id=5) run 62 font 'Century Gothic' -> '+mn-lt'`.
   * The grouping key, computed by the engine that wrote the sentence.
   */
  summary: string;
  /** True when it changes what the deck *says*, not how it looks. */
  is_content: boolean;
  /** True when a number moved — the sharper half of `is_content`. */
  changes_figures: boolean;
}

export interface DiffResult {
  source_version: number;
  output_version: number;
  changed: boolean;
  /** The count nobody wants to be non-zero after a formatting pass. */
  figures_changed: number;
  slides_added: number[];
  slides_removed: number[];
  deltas: Delta[];
  rendered: string;
}

/** One edit the user made directly. Origin is USER; `before` is read server-side. */
export interface SetSpec {
  slide: number;
  target: string;
  op: Op;
  after: unknown;
  rationale?: string;
}

export interface LockSpec {
  scope: LockScope;
  target?: string;
  reason?: string;
}

/** The stages an apply actually passes through. No interpolated percentage. */
export type ApplyStage =
  | "applying"
  | "applied"
  | "verifying"
  | "verified"
  | "blocked"
  | "refused";

export interface ApplyProgress {
  stage: ApplyStage;
  detail: string;
  slides?: number[];
  version?: number;
}
