# ADR-0010 — The product surface is a local application over the Python engine

Status:  accepted
Date:    2026-09-07
Amends:  ADR-0005 (Python engine, TypeScript product) — narrows where its API half applies
Follows: ADR-0008 (the document does not leave the machine)

## Context

ADR-0005 split the system as *Python engine, TypeScript API and client, job queue
between them*. That split was drawn when a hosted multi-tenant web application was
the assumed first surface. ADR-0008 then removed that assumption: the material this
engine exists to edit is confidential by default, the upload is the failure point,
and **local execution is the primary delivery model**.

Under a local model the TypeScript API layer stops paying for itself and starts
costing:

- It puts a second runtime on the analyst's machine to forward calls to the first.
- The job queue exists because hosted jobs cannot live in a request. A local
  single-user session has no such constraint — it has one user, one deck, and a
  process that can stream progress directly.
- Every deck byte would cross an extra process boundary, widening the surface that
  ADR-0005 itself was pleased to have kept narrow ("only the worker touches deck
  bytes").

Meanwhile the *client* half of ADR-0005 is unaffected and still correct. The
interface quality this product needs is a TypeScript job.

## Decision

**One local process serves the product: a Python HTTP API in front of the existing
engine, and a TypeScript client that talks to it over loopback.**

1. **API in Python** (`src/product/api`), importing `slide_wright` directly. No
   subprocess, no queue, no serialisation of deck bytes across a language boundary.
2. **Client in TypeScript** (`src/product/web`) — ADR-0005's client decision, kept.
3. **Bound to `127.0.0.1` only.** Not configurable to a public interface. A product
   whose governing constraint is that the file does not leave the machine must not
   ship a flag that lets it.
4. **No accounts, no persistence beyond the session workspace the engine already
   writes.** History and revert are already local files; that stays the whole
   storage model.
5. **Long work streams.** Ingest and apply take minutes on a real deck. Progress is
   narrated over Server-Sent Events rather than hidden behind a spinner, because the
   UX architecture requires honest progress and a request/response API cannot give it.
6. **ADR-0005 stands for a hosted surface**, if one is ever built. Its queue and its
   TypeScript API are the right shape for that. This ADR does not delete that path;
   it declines to pay for it before there is a hosted product to pay for.

## Alternatives

| Option | Rejected because |
|---|---|
| TypeScript API forwarding to a Python worker, as ADR-0005 specifies | Two runtimes to install, a queue with one consumer, and deck bytes crossing an extra boundary — all cost, no benefit, on a single-user machine |
| No API at all; render the UI from CLI output | The change set, the filmstrip and the verification report are structured data. Scraping them back out of formatted text would make the terminal renderer a load-bearing wire format |
| Desktop shell (Electron/Tauri) now | The packaging problem is real (ADR-0008 accepts it) but it is a distribution decision, not an architecture one. A local server plus a browser proves the product first and can be wrapped later without changing this seam |
| Rewrite the engine to WebAssembly and run entirely in the browser | Already rejected in ADR-0008 for the same reason: the guarantee is byte-level and the engine is 174k lines of Python |

## Consequences

- The web client can never assume a server it does not control. Everything it shows
  must be derivable from one machine's filesystem.
- Two toolchains remain (ADR-0005's accepted cost), but only one of them is a
  *runtime dependency* for the user. The client builds to static files.
- Streaming makes the API stateful in a way a REST surface is not. Session state
  lives in the API process keyed by deck; the durable state stays on disk in the
  engine's workspace, so a restart loses a view, never a version.
- If a hosted surface is ever built, this API is not reusable as-is — it assumes one
  trusted local user. That is a real cost and is accepted, because building for two
  deployment models before either has a customer is how the previous 33 studio
  campaigns died.

## Reversal

A paying customer who needs a hosted surface. At that point ADR-0005's original
shape returns for that surface, and this one continues to serve local installs.
