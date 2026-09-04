# AI Architecture

**Status:** Proposed · **Updated:** 2026-09-04

## Selection criteria

Ranked for *this* product specifically:

1. **Long context** — a 60-slide deck IR plus a 5,090-line authoring contract is large, and coherence is a whole-deck property.
2. **Instruction adherence under a strict contract** — the engine fails closed; a model that improvises produces rejected output and burns money on retries.
3. **Structured output** — the IR is typed. Free-form prose is useless here.
4. **Zero-retention terms** — customer decks are confidential (R7/R8). This is a hard filter, not a preference.
5. **Multimodal** — the visual critic reads rendered slides.
6. Cost, latency, fallback availability.

## Selections

| Role | Choice | Reasoning | Confidence |
|---|---|---|---|
| **Primary reasoning** — planning, authoring, transformation | **Claude Opus 5** (`claude-opus-5`) — $5/MTok in, $25/MTok out, 1M context | Long context is the binding constraint. The upstream engine's own guidance recommends an Opus-class model at ~1M context, and reports visible style drift when the window forces split execution. | High |
| **Narrow / high-volume tasks** — fact extraction, classification, labelling | **Claude Sonnet 5** (`claude-sonnet-5`) — $2/$10 | Schema-bound work with a verifier downstream. Opus here is waste. | High |
| **Visual critic** | A vision-capable model over rendered slides | Advisory only — never decides pass/fail | Medium |
| **Batch / non-interactive** | Batch API, **−50%** | Overnight recurring-deck jobs are not latency-sensitive | High |
| **Embeddings** | Deferred | No retrieval requirement yet. Do not add a vector DB before there is something to retrieve. | High |
| **OCR** | Deferred | Modern models read PDFs natively. PaddleOCR only if genuine image-only scans appear at volume. | High |
| **Image generation** | Deferred | Irrelevant to the editing wedge. | High |

## Effort and thinking

- Adaptive thinking (`thinking: {type: "adaptive"}`) throughout.
- **`effort: "high"`** for planning and authoring — correctness dominates cost here.
- **`effort: "low"`** for extraction and classification.
- **Prompt caching is load-bearing**: the authoring contract and reference docs are large and stable. They belong at the front of the prefix, ahead of anything per-request. Verify with `cache_read_input_tokens` — a zero there means a silent invalidator, and the cost model breaks.

## Provider abstraction

Mandatory (R8), but kept thin — a heavy abstraction that flattens every provider to a common denominator costs more than it saves.

```
core/llm/
  client.py        # request/response types, retries, timeouts
  providers/
    anthropic.py   # primary
    <other>.py     # added only when a second provider is actually needed
  budget.py        # per-job token ceilings, hard stop
  cache.py         # prefix construction, cache-hit assertions
```

Rules: no provider SDK types leak past `core/llm/`; every call carries a job id and a budget; every response records tokens and cost. **Do not build a second provider adapter speculatively** — build the seam, not the implementations.

## Cost model (HYPOTHESIS H8 — must be measured)

Estimate for a 10–15 page generation at Opus 5 rates:

| Component | Estimate |
|---|---|
| Output (~10k tokens/page × 15) | ~$3.75 |
| Input, mostly cached (~2M @ cache-read) | ~$1.00 |
| Uncached input, retries, gate repairs | ~$1–4 |
| **Total per generated deck** | **~$5–15** |

**Edit jobs should be materially cheaper** — no narrative phase, and only affected slices are authored. Plausibly **$0.50–3 per edit**, which is the strongest unit-economics argument for leading with editing.

Both figures are unmeasured. Instrument every job from the first run.

## Cost controls

1. **Hard per-job token budget.** Exceed it and the job stops and reports — never silently continues.
2. **Bounded repair loop.** Max N gate passes, then escalate to a human. An unbounded loop is an unbounded bill.
3. **Cache the contract.** Non-negotiable given its size.
4. **Right-size the model per stage.** Extraction on Sonnet, authoring on Opus.
5. **Batch what is not interactive.** −50%.
6. **Do deterministic work deterministically.** Every geometric check moved out of the model is permanently free.

## What the model is never allowed to do

| Never | Because |
|---|---|
| Touch OOXML bytes directly | Fidelity is arithmetic |
| Decide whether output passes the gate | Self-grading flatters itself |
| Assert a number without a citation | Fabricated figures are the category's worst failure |
| Modify an object marked `frozen` | User guarantees must be enforced, not requested |
| Run unbounded | Cost and latency |

The self-grading prohibition is the one most often violated in this category, and it is why the gate is deterministic by design.
