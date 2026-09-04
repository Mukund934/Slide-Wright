# ADR-0005 — Python engine, TypeScript product

Status:  accepted
Date:    2026-09-04

## Context

The deck engine is 174,647 lines of Python (ADR-0001) and is not portable in any realistic timeframe. The product surface — API and web client — is better served by TypeScript.

## Decision

- **Python**: ingest, IR, quality gate, render, verify. Everything that touches deck bytes.
- **TypeScript**: API, web client.
- **Boundary**: a job queue. The API enqueues; the Python worker executes.

## Alternatives

| Option | Rejected because |
|---|---|
| Python everywhere | Weaker frontend story for the interface quality we need |
| TypeScript everywhere | Would require reimplementing 174k lines of engine. Not a real option. |
| Rust engine | Reimplementation with no path to the existing capability |

## Consequences

- Two toolchains, two dependency sets, two CI paths. A real cost, accepted because the alternative is reimplementing the engine.
- The boundary is narrow and explicit, which keeps the seam manageable.
- **Only the worker touches deck bytes**, which usefully shrinks the security surface (see security architecture).
- Shared types across the boundary need a single source of truth — likely JSON Schema generated into both languages.

## Reversal

None expected. The engine choice would have to change first.
