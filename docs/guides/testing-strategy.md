# Testing Strategy

**Status:** Proposed · **Updated:** 2026-09-04

## Why this exists before any code

The engine we build on has **no CI** for 174,647 lines of Python producing binary output. Every regression there is currently found by a user.

Our product promise is *"we changed only what you asked."* That promise is testable by arithmetic — which means it is also **continuously verifiable**, and a promise you can verify but don't is negligence.

So the benchmark corpus is not a QA artifact. **It is the product's evidence base**, and it is the thing that would let us maintain the engine ourselves if upstream stops (risk R4).

## The fidelity benchmark — the one that matters

> **Fidelity Score = % of source file parts byte-identical after a targeted edit.**

Current measurement, on a real 19-slide deck: **104/105 = 99.05%**.

A tool that regenerates a deck rather than editing it scores near zero here by construction: it cannot preserve bytes it never read. That is what makes the number worth measuring — it is a property of the approach, not of the effort.

### Corpus requirements

The corpus must contain what real decks contain. Both decks tested so far were student/technical projects with **zero** SmartArt, embedded charts or grouped shapes — so current evidence does not cover the hard cases.

| Class | Must include |
|---|---|
| Simple | Text and shapes only |
| Tables | Native `<a:tbl>`, merged cells, banded styles |
| **Charts** | **Embedded Excel chart parts** (`ppt/charts/`, `ppt/embeddings/`) |
| **SmartArt** | **`ppt/diagrams/` parts** |
| Grouped | Nested `<p:grpSp>` |
| Media | Images, cropped images, video references |
| Templates | Corporate master/layout inheritance, `.potx` |
| Foreign-authored | Google Slides, Keynote, WPS, LibreOffice exports |
| Fonts | Licensed fonts not installed locally |
| Scale | 60+ slides |

Corpus lives in `tests/corpus/`. **Only decks explicitly cleared for sharing are tracked**; customer material never enters Git (see `.gitignore`).

## Test layers

| Layer | What it proves |
|---|---|
| **OOXML integrity** | Output opens; parts and relationships valid; passes OPC validation |
| **Fidelity** | Untouched round-trip is byte-identical, per part |
| **Selective edit** | A targeted change alters only the intended parts |
| **Native preservation** | Table, chart and image counts ≥ source after any edit (ADR-0006) |
| **Editability** | Text is live `<a:t>` runs, not pictures; tables are `<a:tbl>` |
| **Geometric** | No overflow, collision or off-canvas content |
| **Change attribution** | Every changed part maps to a change-set entry; unattributed ⇒ fail |
| **Adversarial** | Malformed, zip-bomb, XXE, path-traversal and macro-enabled inputs are rejected safely |
| **AI evaluation** | Instruction → correct change set; measured against a labelled set |
| **Cost/performance** | Tokens and wall-clock per job, tracked over time |

Note that most of these are **assertions on bytes**, not screenshots. Visual regression testing is deferred — it is expensive, flaky, and would duplicate what geometric checks already prove more cheaply.

## The regression rule

**Every real deck that breaks becomes a permanent corpus entry, the same day.**

This is what keeps the corpus honest. A deck that broke once is the only reliable evidence that it stays fixed.

## Competitive benchmarking

To compare against Gamma, Canva, Copilot and Beautiful.ai honestly:

1. Same source material, same instruction, to every tool.
2. Export each to `.pptx`.
3. **Score by parsing OOXML, never by looking at it.**
4. Publish the harness so results are reproducible.
5. **Report our own failures.** A benchmark you always win is marketing, and readers can tell.

## What we do not test yet

Load, multi-region, browser matrix, mobile. All premature before a paying customer exists.
