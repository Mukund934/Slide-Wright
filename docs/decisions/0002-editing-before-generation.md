# ADR-0002 — Editing before generation

Status:  accepted
Date:    2026-09-04

## Context

The obvious product is "AI generates presentations." Established tools already do that, and the application that owns the file format bundles it free.

Architecturally, they share one property: they are **generate-first**. Each authors into its own internal representation and exports outward. Outward conversion is lossy by construction, which is why exports from these tools commonly damage slides and lose template conformance.

Reading arbitrary OOXML *back in* without losing it is the harder problem, and it is the one we have measured that we can solve (ADR-0001):

- a real 19-slide deck round-trips at 100% of parts byte-identical
- a targeted edit changes exactly one package part
- native tables, charts and embedded workbooks survive an edit

*(Market sizing and competitive positioning live in the private decision log, not here. An ADR should record the technical decision and its consequences.)*

## Decision

**Lead with editing existing decks.** Generation is a later capability inside the same system, not the entry point.

## Alternatives

| Option | Rejected because |
|---|---|
| Generation-first, editing later | Enters the most crowded software market against a free bundled incumbent, with no differentiator |
| Both simultaneously | Splits a small team across two products; neither gets good |
| Template-conformant generation for enterprise | Occupied by a well-funded incumbent with enterprise distribution |

## Consequences

- The engineering focus is fidelity, provenance and verification rather than aesthetics.
- **Cheaper unit economics**: an edit needs no narrative phase and authors only affected slices. Plausibly $0.50–3 per edit against $5–15 per generated deck.
- Narrower initial market. Accepted deliberately.
- Requires users to hand over real, often confidential decks — a genuine adoption barrier (assumption A3), partly answered by local processing.

## Reversal

If validation shows professionals will not hand over existing decks under any conditions, the wedge fails and generation becomes the only path — into a market we would then be entering with no advantage.
