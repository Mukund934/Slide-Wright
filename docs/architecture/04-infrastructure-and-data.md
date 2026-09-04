# Infrastructure and Data Model

**Status:** Proposed · **Updated:** 2026-09-04

> **Nothing here is provisioned during Phase 0.** Phase 0 is a folder, a CLI and a spreadsheet. This document exists so that MVP 2 does not get designed in a hurry.

---

## The backend decision: Supabase

**Chosen: Supabase.** Rejected: Firebase, raw PostgreSQL, Convex, PlanetScale.

### Why

**1. The data is relational, and deeply so.** Users → organisations → projects → decks → versions → jobs → changes → audit events. Versioned documents with per-change attribution is a join-heavy workload with strong integrity requirements. Firestore's document model fights this: no joins, denormalisation everywhere, and manual consistency exactly where correctness matters most.

**2. Row Level Security is tenant isolation at the layer that cannot be bypassed.** R7 says a cross-tenant deck leak is existential. Postgres RLS enforces isolation *in the database*, so an application bug cannot leak another customer's pitchbook. Firestore security rules are less expressive and live further from the data.

**3. It bundles exactly what we need and nothing we don't** — Postgres, auth, object storage with signed URLs, row-level policies. Assembling these separately is weeks of work for the same result.

**4. The escape hatch is real.** Supabase is Postgres. If we outgrow it, we take a `pg_dump` and go. Firebase is a one-way door — Firestore's model has no equivalent elsewhere.

**5. Audit logging is a first-class requirement**, and append-only audit tables with strong constraints are a relational problem.

### Why not the alternatives

| Option | Rejected because |
|---|---|
| **Firebase** | Document model is wrong for versioned relational data; weaker isolation guarantees; vendor lock-in with no migration path; audit/reporting queries become painful |
| **Raw PostgreSQL** (Neon/RDS) | Same database, but auth, storage and RLS tooling must be built. Right choice *later* if we outgrow Supabase — the migration is a dump and restore. |
| **Convex / PlanetScale** | Fine technology, no advantage for this workload, smaller ecosystem |

**Confidence: high.** The relational shape and the isolation requirement both point the same way.

### Trigger to revisit

If deck volume makes Postgres large-object handling awkward, move blob storage to S3-compatible object storage and keep Postgres for metadata. That is a storage swap, not a backend migration.

---

## Data model (initial)

```
organisations         id, name, created_at
users                 id, email, name
memberships           user_id, org_id, role                  [owner|admin|member]

projects              id, org_id, name, template_deck_id?

decks                 id, project_id, org_id, filename,
                      source_hash, storage_path, uploaded_by, created_at
                      ── immutable. never updated in place ──

deck_versions         id, deck_id, parent_version_id, storage_path,
                      output_hash, created_by, job_id, created_at

jobs                  id, deck_id, org_id, kind,             [edit|transform|create|audit]
                      status, instruction, change_set jsonb,
                      token_cost, wall_ms, created_by, created_at

changes               id, job_id, slide_index, object_ref,
                      change_type, before jsonb, after jsonb,
                      requested boolean                       ── false ⇒ job failed
                      ── one row per applied mutation; drives the change report ──

verifications         id, job_id, parts_total, parts_identical,
                      unattributed_changes, native_objects_before jsonb,
                      native_objects_after jsonb, passed boolean

locks                 id, deck_id, scope,                    [slide|object]
                      target_ref, reason, created_by
                      ── user-declared frozen regions ──

audit_events          id, org_id, actor_id, action, target, metadata jsonb, at
                      ── append-only ──

usage                 org_id, period, decks_processed, tokens_in, tokens_out, cost
```

**Notes.** `org_id` is denormalised onto every tenant-scoped table so a single RLS policy shape covers them all — that redundancy is deliberate and is the isolation mechanism. `decks` is immutable; edits create `deck_versions`. `changes.requested = false` on any row means the verifier caught an unattributed mutation and the job must not deliver.

---

## Infrastructure, and when each piece is justified

Nothing is added before its trigger.

| Component | Choice | Justified when | Phase |
|---|---|---|---|
| Database | Supabase Postgres | Multiple users exist | MVP 2 |
| Auth | Supabase Auth | Multiple users exist | MVP 2 |
| Object storage | Supabase Storage → S3-compatible | Decks are uploaded by anyone but us | MVP 2 |
| Job queue | Postgres-backed queue | >1 concurrent job | MVP 2 |
| Worker | Long-running container | Jobs exceed request timeouts (they will — generation is 10–20 min) | MVP 2 |
| Error tracking | Sentry | Anyone but us runs a job | MVP 2 |
| Redis | — | Only when Postgres queueing measurably hurts | Later |
| Vector DB | — | Only when there is retrieval to do | Later |
| CDN | — | Only when serving assets at volume | Later |
| GPU | — | **Never, on current design.** No self-hosted models planned. | — |
| Kubernetes | — | Not at this scale. A container on a managed host is enough. | — |

**Deliberately absent from MVP:** Redis, vector database, microservices, Kubernetes, GraphQL, event bus. Each is a real answer to a real problem we do not have.

### Job execution

Deck jobs run **10–20 minutes**. That rules out serverless request handlers as the execution surface.

```
API (stateless)  →  enqueue job  →  worker (long-running container)
                                       ↓
                                    progress events → client
```

The worker owns the whole pipeline: ingest → plan → author → gate → render → verify. It is the only component that touches deck bytes, which keeps the security surface small.

---

## Language split

| Layer | Language | Why |
|---|---|---|
| Deck engine (ingest, gate, render, verify) | **Python** | The engine we build on is 174,647 lines of Python. Reimplementing in another language is not a real option. |
| API / web | **TypeScript** | Better fit for the product surface and the frontend; talks to the worker over a queue. |

Two languages is a real cost. It is justified here because the engine is not portable and the frontend genuinely benefits from TypeScript. The boundary is narrow and well-defined: the queue.
