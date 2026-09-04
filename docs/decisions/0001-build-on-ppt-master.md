# ADR-0001 — Build on ppt-master rather than a new engine

Status:  accepted
Date:    2026-09-04

## Context

The product requires reading arbitrary PowerPoint files, reasoning about them, editing them and emitting native, editable output without fidelity loss. This is a large and unglamorous problem.

`hugohe3/ppt-master` (MIT) is 174,647 lines of Python solving it. Measured on this machine, 4 Sep 2026:

- A real 19-slide deck (105 parts, 6 native tables): **105/105 parts byte-identical** through a full round-trip
- A real 17-slide deck (407 shapes): **76/76 byte-identical**
- One slide title edited: **exactly 1 of 105 parts changed**, native table preserved, 33/33 text runs intact
- Output is genuinely native: 11 shapes, **0 pictures**, live editable text runs
- The quality gate emits machine-actionable repairs, not just errors

The maintainer has written **"Won't do: … SaaS web service"** into the project roadmap, so the product layer will not be built upstream.

## Decision

Build on ppt-master, **tracked upstream and unmodified**. Do not fork.

## Alternatives

| Option | Rejected because |
|---|---|
| Write our own OOXML engine | Quarters of work to reach a capability that already exists free, with no differentiation gained |
| python-pptx only | Good library, but no fidelity-preserving round-trip and no quality gate |
| LibreOffice headless conversion | Lossy; not designed for object-level preservation |
| Commercial SDK (Aspose etc.) | Cost per seat/server, and still no semantic layer or gate |
| Fork ppt-master immediately | Converts the largest asset into maintenance debt for 174k lines with no CI |

## Consequences

- Dependency on a single maintainer (risk R4). Mitigated: MIT permits forking, and we pin a version at first paying customer.
- Upstream has **no CI** for 174k lines producing binary output. We must build our own regression corpus regardless.
- Two known defects inherited and must be handled: `wrap="none"` overflow (see gate design) and the export flag defect (ADR-0006).
- Licence hygiene items to clear before commercial use: delete the Gemini watermark remover; gate the bundled brand-logo set; attribute CHUNK Icons (CC BY 4.0).

## Reversal

Fork if upstream is abandoned. Abandon the approach entirely only if fidelity fails on adversarial enterprise decks (risk R2) — in which case the differentiator never existed.
