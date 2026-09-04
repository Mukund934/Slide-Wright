# System Architecture

**Status:** Proposed · **Updated:** 2026-09-04 · **Phase:** 0 (nothing implemented)

---

## The governing principle

> **Generate narrowly. Verify deterministically. Let the verifier teach the generator.**

Generative steps are cheap, fast and unreliable. Deterministic steps are what make output trustworthy. A long generative chain compounds error at every link; a short generative step wrapped in a hard deterministic gate does not.

This is not theory. The engine we build on already emits repair instructions addressed to a machine:

```
overflow horizontal 14.8%; ≈11.8 px per Latin char at 20px; ≈108 chars fit in 1280 px
```

That is a **computed** correction signal, not a generated one. The whole architecture is organised around producing more signals of that kind.

## The second principle

> **The user's file is evidence, not input.**

Most systems treat an uploaded document as raw material to be consumed. Here the source file is retained immutably and is the reference against which every output is verified. We never lose the ability to answer "what exactly did you change?"

---

## System diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              INTAKE                                          │
│                                                                              │
│   existing .pptx ─────┐                                                      │
│   template .potx ─────┼──▶  immutable source snapshot  (hash, never mutated) │
│   xlsx / docx / pdf ──┤                                                       │
│   plain instruction ──┘                                                       │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  UNDERSTAND                                        [deterministic]           │
│    OOXML → Presentation IR                                                   │
│    · per-object identity + source refs   · per-subtree content hashes        │
│    · master/layout/theme inheritance     · fidelity diagnostics recorded     │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  FACT LAYER                                        [model + strict schema]   │
│    source docs → typed facts, each carrying a citation to its origin         │
│    no value enters a slide without provenance                                │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  PLAN — the Change Set                             [model, human-reviewable] │
│    "what must change, and nothing else"                                      │
│    an explicit, inspectable list of intended mutations                       │
│    ── THIS IS THE PRODUCT'S CONTRACT. Everything downstream honours it. ──   │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  THE LOOP                                          [bounded: max N passes]   │
│                                                                              │
│      author affected objects ──────▶ deterministic gate                      │
│               ▲                            │                                 │
│               └────── repair spec ◀────────┘  (computed, not generated)      │
│                                                                              │
│    gate checks: geometry · overflow · collision · contrast · brand tokens ·  │
│                 native-object preservation · change-set conformance          │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  RENDER                                            [deterministic]           │
│    IR → native DrawingML .pptx                                               │
│    untouched objects pass through with original XML, byte-for-byte           │
│    ⚠ native charts/tables flag FORCED — see 02-safe-editing.md               │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  VERIFY                                            [deterministic]           │
│    part-level hash diff  output ⟷ immutable source                           │
│    · every change traced to a change-set entry                               │
│    · any unrequested change ⇒ FAIL THE JOB, do not deliver                   │
│    · native-object counts asserted ≥ source                                  │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 ▼
                      deck  +  change report
```

## Why not the long linear pipeline

A pipeline of `intent → research → narrative → layout → chart choice → asset gen → render → QA → critique → regenerate` is the intuitive design and it is wrong as a default, for two reasons found empirically:

1. **Most jobs are edits, not creations.** When the user says "update the Q2 figures," the narrative already exists — it is in the file. Running narrative planning on an edit job is expensive, slow, and actively dangerous: it invites the system to restructure a deck nobody asked it to restructure.

2. **Error compounds across generative stages.** Each hop is an opportunity to drift. The gate catches geometry, not intent drift.

So the pipeline is **conditional on job class**:

| Job class | Path |
|---|---|
| **Edit** (the common case) | Understand → Plan → Loop → Render → Verify. No narrative phase. |
| **Transform** (restructure, condense, re-audience) | Adds narrative re-planning, scoped to affected sections |
| **Create** (from documents/data) | The full pipeline, narrative first |

**Build the Edit path first.** It is the wedge, it is cheapest, it is most reliable, and it is where nobody else can follow.

## Whole-deck vs slide-by-slide reasoning

Evidence: the upstream engine generates **intentionally serially** because *"parallel generation was tested and produced inconsistent styles."* Style and argument are global properties; naive parallel local generation destroys both.

Resolution — three phases rather than one choice:

1. **Global, cheap, once.** Narrative arc, slide roster, each slide's message and role, the visual system. Small output, whole-deck scope, high model effort.
2. **Local, parallel-safe.** Once the visual system is locked, the constraint that broke parallelism is gone. Slides fully determined by phase 1 can be authored concurrently.
3. **Serial only where coherence is genuinely pairwise** — build sequences, progressive reveals, before/after pairs.

**On the Edit path, phase 1 is free** — the global structure is read from the file rather than invented. Another reason editing is the easier and better first product.

## Where agents are dangerous

| Stage | Mechanism | Why |
|---|---|---|
| Ingest / render / verify | **Deterministic. Never a model.** | Fidelity is arithmetic. A model near byte-level operations is a corruption risk with no upside. |
| Fact extraction | Model + strict schema | Language understanding needed; output must be typed and cited. |
| Change-set planning | Model, output reviewable | Where judgement lives, and where a human should be able to intervene cheaply. |
| Slide authoring | Model, tightly constrained | Bounded by contract and gate. |
| **Quality gate** | **Deterministic. Never a model.** | The correction signal must be trustworthy. A model grading its own output will flatter it. |

The last row is the one most often got wrong in this category. A model asked to score its own slide will report success in the direction that makes it look useful.

## Component map

| Component | Responsibility | Determinism |
|---|---|---|
| `ingest` | OOXML → IR, snapshot, hashing | Deterministic |
| `ir` | Presentation representation + schema | Deterministic |
| `facts` | Source docs → typed cited facts | Model |
| `planner` | Instruction → change set | Model |
| `author` | Change set → object edits | Model |
| `gate` | Geometric, brand, conformance checks | **Deterministic** |
| `render` | IR → .pptx | Deterministic |
| `verify` | Hash diff, change attribution | **Deterministic** |
| `report` | Human-readable change account | Deterministic |

## Related

- `01-presentation-ir.md` — the internal representation
- `02-safe-editing.md` — how "preserve everything else" is enforced
- `03-ai-architecture.md` — models and provider abstraction
- `04-infrastructure-and-data.md` — backend, storage, data model
- `05-security.md` — confidentiality architecture
- `06-ux-architecture.md` — screens and interaction model
