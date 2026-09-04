# ADR-0004 — Supabase as the backend

Status:  accepted
Date:    2026-09-04

## Context

The product needs users, organisations, projects, decks, versions, jobs, per-change records, locks, audit events and usage. A cross-tenant leak of a live M&A pitchbook is existential (risk R7).

Note this is a decision for MVP 2. **Phase 0 provisions no backend at all.**

## Decision

**Supabase** — Postgres, Auth, Storage, Row Level Security.

## Alternatives

| Option | Rejected because |
|---|---|
| **Firebase / Firestore** | Document model fights deeply relational versioned data; no joins; manual consistency exactly where correctness matters; weaker isolation expressiveness; one-way door with no migration path |
| **Raw PostgreSQL** (Neon/RDS) | Same database, but auth, storage and policy tooling must be built. The right destination *later* — migration is a dump and restore. |
| Convex / PlanetScale | Capable, but no advantage for this workload |

## Consequences

- **RLS enforces tenant isolation in the database**, so an application bug cannot leak another customer's deck. This is the decisive factor.
- Auth and signed-URL storage come free.
- Escape hatch is real: it is Postgres.
- Accepts a managed-service dependency, and Postgres large-object handling may become awkward at deck volume.

## Reversal

If blob volume strains Postgres, move blobs to S3-compatible storage and keep Postgres for metadata. That is a storage swap, not a backend migration.
