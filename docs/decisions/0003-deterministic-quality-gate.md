# ADR-0003 — The quality gate is deterministic, never a model

Status:  accepted
Date:    2026-09-04

## Context

Output quality must be enforced somewhere. The tempting design is an LLM critic that reviews slides and decides whether they are good.

Two observations argue against it.

First, a model asked to grade its own output will report success in the direction that makes it look useful. The Comp AI CRM codebase states the general principle well: *"no tool accepts a confidence score, because a model asked to grade its own certainty will, and it will be wrong in the direction that makes it look useful."*

Second, the failure mode we actually observed is **geometric, not aesthetic**. With `wrap="none"`, a mis-measured string produced a text box extending **2.85 inches beyond a 13.33-inch slide**, permanently. That is arithmetic. A vision model asked "does this look right?" is a worse detector than a bounding-box comparison, and vastly more expensive.

The upstream gate already demonstrates the right shape, emitting repair instructions addressed to a machine:

```
overflow horizontal 14.8%; ≈11.8 px per Latin char at 20px; ≈108 chars fit in 1280 px
```

## Decision

The quality gate is **deterministic and computed**. It decides pass/fail. A multimodal critic may later *advise* on taste, but never gates.

## Alternatives

| Option | Rejected because |
|---|---|
| LLM-as-judge gate | Self-grading is unreliable; expensive; non-reproducible |
| Multimodal vision gate | Worse than arithmetic at geometry, and cannot be trusted with pass/fail |
| Human review only | Does not scale, and is what we are selling relief from |

## Consequences

- Every check must be expressible as a computation: overflow, collision, contrast, font-size floor, brand-token conformance, native-object preservation, change-set conformance.
- Checks are free, fast, reproducible and never flaky.
- Genuinely aesthetic judgement is out of scope for the gate. Accepted — most complaints in this category are geometric.
- The gate must return **repair instructions**, not just failures. That is what closes the loop with the authoring model.

## Reversal

None foreseen for pass/fail. A vision model may be added in an advisory role without changing this decision.
