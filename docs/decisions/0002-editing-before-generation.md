# ADR-0002 — Editing before generation

Status:  accepted
Date:    2026-09-04

## Context

The obvious product is "AI generates presentations." That market is saturated: Gamma, Canva, Beautiful.ai, Plus AI, Presentations.AI, SlidesAI and others, with Microsoft Copilot bundled free into the application that owns the file format.

Competitive research found the enterprise lane occupied too: Prezent.ai (~$74.3M raised, $400M valuation, $399/user/month) and Presentations.AI's shipping deck-refresh agent.

But every one of these is **generate-first**: they author into their own representation and export outward. That is lossy by construction, which is why market-leading exports damage a reported 30–40% of slides and why brand-compliance scores are poor. Reading arbitrary OOXML *back* without losing it is a harder problem that earns nothing at a $20/month price point — so nobody solved it.

We have measured that we can (ADR-0001).

## Decision

**Lead with editing existing decks.** Generation is a later capability inside the same system, not the entry point.

## Alternatives

| Option | Rejected because |
|---|---|
| Generation-first, editing later | Enters the most crowded software market against a free bundled incumbent, with no differentiator |
| Both simultaneously | Splits a small team across two products; neither gets good |
| Template-conformant generation for enterprise | Prezent occupies it with $74M and enterprise distribution |

## Consequences

- The engineering focus is fidelity, provenance and verification rather than aesthetics.
- **Cheaper unit economics**: an edit needs no narrative phase and authors only affected slices. Plausibly $0.50–3 per edit against $5–15 per generated deck.
- Narrower initial market. Accepted deliberately.
- Requires users to hand over real, often confidential decks — a genuine adoption barrier (assumption A3), partly answered by local processing.

## Reversal

If validation shows professionals will not hand over existing decks under any conditions, the wedge fails and generation becomes the only path — into a market we would then be entering with no advantage.
