/**
 * The only place this client talks to anything.
 *
 * Every call goes to the engine on the same origin. There is no second base URL
 * and no configuration for one: ADR-0008 says the document does not leave the
 * machine, and the cheapest way to keep that true is to have nowhere else to
 * send it. That is still exactly true under ADR-0011 -- a self-hosted
 * deployment serves this client from the same origin as its API, so "the same
 * origin" continues to mean "wherever this page came from" and never a second
 * host we were configured to trust.
 */

import type {
  ApplyProgress,
  Audit,
  ChangeSet,
  Deck,
  DiffResult,
  ExportTarget,
  Health,
  LockSpec,
  SetSpec,
  RefreshPlan,
  SlideDocument,
  TidyPlan,
  Verification,
} from "./types";

const BASE = "/api";

/**
 * The token for a self-hosted deployment, for this tab only.
 *
 * `sessionStorage`, not `localStorage`, and the difference is the point: a
 * shared token that outlives the browser session sits on the disk of every
 * machine that ever opened the deployment, including the borrowed one and the
 * one in the meeting room. Closing the tab is a logout, which is the behaviour
 * somebody handed a team token would expect.
 *
 * A local deployment never reaches this code: there is no token to hold,
 * because there is nothing to authenticate to (ADR-0010).
 *
 * Every access is guarded. Storage throws in a private window, and a token that
 * cannot be remembered is a reason to ask for it again, never a reason to fail
 * to start.
 */
const TOKEN = "slide-wright.token";

export function token(): string | null {
  try {
    return window.sessionStorage.getItem(TOKEN);
  } catch {
    return null;
  }
}

export function remember(value: string): void {
  try {
    window.sessionStorage.setItem(TOKEN, value);
  } catch {
    // The session still works; it just will not survive a reload.
  }
}

export function forget(): void {
  try {
    window.sessionStorage.removeItem(TOKEN);
  } catch {
    // As above.
  }
}

/**
 * A refusal the engine explained, as opposed to a transport failure.
 *
 * These are the interesting ones: "that lock forbids this change", "no such
 * change id", "verification failed and the deck will not be exported". They
 * carry a sentence written for a person, and the UI shows it verbatim rather
 * than replacing it with something friendlier and less true.
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }

  /** The engine could not reach the document at all, usually after a restart. */
  get isMissingDocument(): boolean {
    return this.status === 404;
  }
}

/**
 * What every request to this API carries.
 *
 * One function rather than an object literal per call site, because there were
 * two call sites and only one of them had the token: `applyStreaming` reads a
 * Server-Sent Events body and so cannot go through `request`, and it built its
 * own headers. On a self-hosted deployment that made *apply* -- the operation
 * the whole product exists to perform -- the one route that returned 401,
 * while everything around it worked.
 *
 * Any future call that bypasses `request` for a streaming body has the same
 * shape of bug available to it. This is the thing to spread.
 */
function headers(): Record<string, string> {
  const held = token();
  return {
    "Content-Type": "application/json",
    // Only when there is one. A local deployment has no token and must not
    // start sending an empty Authorization header to find out.
    ...(held ? { Authorization: `Bearer ${held}` } : {}),
  };
}


async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { ...headers(), ...init?.headers },
    });
  } catch {
    // A local API that is not answering is the one failure mode a user can
    // actually fix, so name it instead of saying "network error".
    throw new ApiError(0, "Slide-Wright is not running on this machine.");
  }

  if (!response.ok) {
    throw new ApiError(response.status, await readDetail(response));
  }
  return (await response.json()) as T;
}

async function readDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      // FastAPI's validation errors. Rare in practice -- they mean the client
      // sent something malformed -- but a raw array on screen helps nobody.
      return "That request was malformed. This is a bug in Slide-Wright.";
    }
  } catch {
    /* fall through to the status line */
  }
  return `The engine refused that (${response.status}).`;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });

export const api = {
  /**
   * Whether this deployment wants a token, asked without holding one.
   *
   * The only route that answers unauthenticated. It says that and nothing
   * else -- `health` reports the engine version and how many decks are open,
   * which is a description of somebody's activity, so it is behind the token.
   */
  ping: () => request<{ ok: boolean; auth: "required" | "none" }>("/ping"),
  health: () => request<Health>("/health"),

  /** Open a deck already on this machine. A path, never an upload. */
  open: (path: string) => post<SlideDocument>("/documents", { path }),

  read: (id: string) => request<SlideDocument>(`/documents/${id}`),

  /** The structure of one version. Both sides of a comparison come from here. */
  deck: (id: string, version?: number) =>
    request<Deck>(
      `/documents/${id}/deck${version === undefined ? "" : `?version=${version}`}`,
    ),

  close: (id: string) => request<{ closed: boolean }>(`/documents/${id}`, { method: "DELETE" }),

  audit: (id: string) => request<Audit>(`/documents/${id}/audit`),

  /** What a tidy would change. Reads only. */
  tidyPlan: (id: string, template = "") =>
    request<TidyPlan>(
      `/documents/${id}/tidy${template ? `?template=${encodeURIComponent(template)}` : ""}`,
    ),

  /** What a refresh would do. Reads the sources; writes nothing. */
  refreshPreview: (id: string, sources: string[]) =>
    post<RefreshPlan>(`/documents/${id}/refresh/preview`, { sources }),

  /** Propose the figures a source explains. Approves and applies nothing. */
  refresh: (id: string, sources: string[], locks: LockSpec[] = []) =>
    post<ChangeSet>(`/documents/${id}/refresh`, { sources, locks }),

  /** Propose the corrections a tidy would make. Approves and applies nothing. */
  tidy: (id: string, template = "", locks: LockSpec[] = []) =>
    post<ChangeSet>(`/documents/${id}/tidy`, { template, locks }),

  /** Work out what would change. Writes nothing. */
  propose: (id: string, body: { instruction?: string; sets?: SetSpec[]; locks?: LockSpec[] }) =>
    post<ChangeSet>(`/documents/${id}/propose`, body),

  review: (
    id: string,
    body: {
      approve?: string[];
      reject?: string[];
      approve_all?: boolean;
      include_unreviewed?: boolean;
    },
  ) => post<ChangeSet>(`/documents/${id}/review`, body),

  /** Apply and verify in one call. Used when there is nothing to narrate. */
  apply: (id: string, note = "") => post<Verification>(`/documents/${id}/apply`, { note }),

  revert: (id: string, to: number) => post<SlideDocument>(`/documents/${id}/revert`, { to }),

  diff: (id: string, source: number, output?: number) => {
    const query = new URLSearchParams({ source: String(source) });
    if (output !== undefined) query.set("output", String(output));
    return request<DiffResult>(`/documents/${id}/diff?${query}`);
  },

  /** Where an export would go, and whether the engine will allow one. */
  exportTarget: (id: string) => request<ExportTarget>(`/documents/${id}/export`),

  /** Write the current version out. Empty destination means the suggestion. */
  export: (id: string, destination = "") =>
    post<{ path: string }>(`/documents/${id}/export`, { destination }),
};

/**
 * Apply, narrated.
 *
 * The stages are real transitions the engine reports as they happen. Nothing
 * here interpolates a percentage, because nothing here knows how long a stage
 * will take, and a progress bar that is guessing is the exact dishonesty this
 * product is built against.
 *
 * `onStage` may be called after the promise settles is impossible by
 * construction: the server puts the verdict on the same queue as the stages, so
 * ordering is preserved end to end.
 */
export async function applyStreaming(
  id: string,
  note: string,
  onStage: (progress: ApplyProgress) => void,
  signal?: AbortSignal,
): Promise<Verification> {
  const response = await fetch(`${BASE}/documents/${id}/apply/stream`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ note }),
    signal,
  });

  if (!response.ok) throw new ApiError(response.status, await readDetail(response));
  if (!response.body) throw new ApiError(0, "The engine sent no progress to read.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: Verification | null = null;
  let failure: string | null = null;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line. Anything after the last one is
    // a partial frame and must stay in the buffer -- parsing it would drop the
    // stage it belongs to.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const event = parseFrame(frame);
      if (!event) continue;
      if (event.kind === "stage") onStage(event.data as ApplyProgress);
      else if (event.kind === "result") result = event.data as Verification;
      else if (event.kind === "error") failure = (event.data as { message: string }).message;
    }
  }

  if (failure) throw new ApiError(422, failure);
  if (!result) {
    // The stream ended without a verdict. Refusing to invent one is the whole
    // point: a missing verification must never read as a passing one.
    throw new ApiError(0, "The apply ended without reporting a result. The deck was not changed.");
  }
  return result;
}

function parseFrame(frame: string): { kind: string; data: unknown } | null {
  let kind = "";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event: ")) kind = line.slice(7);
    else if (line.startsWith("data: ")) data = line.slice(6);
  }
  if (!kind || !data) return null;
  try {
    return { kind, data: JSON.parse(data) as unknown };
  } catch {
    return null;
  }
}
