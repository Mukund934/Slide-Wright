# Architecture Decision Records

An ADR records a decision that closes off alternatives. If a choice can be reversed cheaply and nobody would ask "why is it like this?", it does not need one.

**Business and product decisions** (pricing, positioning, which customer) live in the private decision log, not here. This directory is engineering truth and is safe to share.

## Format

```
# ADR-NNNN — Title
Status:  proposed | accepted | superseded by ADR-NNNN
Date:    YYYY-MM-DD

## Context      What forced a decision. Evidence, with measurements where they exist.
## Decision     What we chose, stated plainly.
## Alternatives What we rejected, and why.
## Consequences What this costs us, including what it makes harder.
## Reversal     What evidence would make us revisit this.
```

Never delete an ADR. Supersede it and link forward — the record of changing our minds is worth keeping.

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-build-on-ppt-master.md) | Build on ppt-master rather than a new engine | Accepted |
| [0002](0002-editing-before-generation.md) | Editing before generation | Accepted |
| [0003](0003-deterministic-quality-gate.md) | The quality gate is deterministic, never a model | Accepted |
| [0004](0004-supabase-backend.md) | Supabase as the backend | Accepted |
| [0005](0005-python-engine-typescript-product.md) | Python engine, TypeScript product | Accepted |
| [0006](0006-force-native-charts-and-tables.md) | Force native charts and tables on every export | Accepted |
| [0007](0007-smartart-read-only.md) | SmartArt is read-only; the round-trip engine cannot ingest it | Accepted |
