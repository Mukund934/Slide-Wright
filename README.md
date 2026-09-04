# Slide-Wright

**Change what I asked. Preserve everything else. Prove it.**

An AI-native presentation intelligence and editing system that understands, modifies and improves PowerPoint presentations — and will eventually create them — while preserving the user's existing work.

```
104 of 105 package parts are byte-for-byte identical to the file you supplied (99.05%)
18 slide(s) untouched

  Requested changes
    · slide 5 · title — retitle to "Literature Review 2026"

  Integrity                source -> output
    native tables              6 -> 6   ok
    chart parts                0 -> 0   ok
    embedded workbooks         0 -> 0   ok
    editable text runs       303 -> 303

  VERIFIED — every change is accounted for.
```

---

## The problem

Every AI presentation tool is built to *create* decks. Almost every valuable deck already exists.

A board pack, a pitchbook, an IC memo, a QBR — these are inherited, not authored. Last quarter's deck is the starting point for this quarter's. A live pitchbook goes through 5–15 revision rounds, each costing hours of expensive people's evenings.

And every AI tool treats an existing deck as an *import problem* — parse it, flatten it into the tool's own representation, re-emit it. That is lossy by construction. The result is a specific absurdity: **the decks that matter most are the ones AI can help with least**, because the one thing their owners cannot tolerate is a tool that touches something they did not ask it to touch.

Slide-Wright starts from the opposite premise. **Your file is evidence, not input.**

## What works today

```bash
slide-wright inspect  deck.pptx                     # structural summary
slide-wright audit    deck.pptx                     # what is wrong with this deck
slide-wright brand    house.potx deck.pptx          # where it departs from the template
slide-wright refresh  deck.pptx --source comps.csv  # update figures, with citations
slide-wright verify   before.pptx after.pptx        # part-level fidelity report
slide-wright profile  deck.pptx                     # how adversarial is this deck?
slide-wright edit     deck.pptx \
    --set "3:5/r1/c1:9.4x=11.8x" \
    --lock numbers \
    -o out.pptx
```

### Refresh a recurring deck from a workbook

Last quarter's deck plus this quarter's numbers. **No model is involved** —
values are matched by row and column *label*, never by position or resemblance,
and a figure that cannot be justified with a coordinate is not changed.

```
REFRESH PLAN

  2 figure(s) to update · 7 already correct · 0 not found in the source

  Updates
    · slide 3 r1c1: '9.4x' -> '11.8x'
        from comps.csv!B2
    · slide 3 r2c2: '19.8%' -> '21.4%'
        from comps.csv!C3
```

Knowing seven figures are *still correct* is as useful as knowing two moved.

### Audit a deck you have just inherited

```
DECK AUDIT — pitchbook.pptx
  19 slides · 1449 words · 76 words per slide

  NARRATIVE
    · 5 slides (3–15): 5 titles name a topic rather than state a finding
        replace "Results" with the result — a reader should get the argument
        from the titles alone
  EVIDENCE
    · slide 5, 12, 13: 3 slide(s) present a table or chart with no source line
        add a source note; an unattributed figure cannot be checked later
```

### Check it against the house template

```
BRAND CONFORMANCE — deck.pptx
  · [font]   'Aptos' is not a template typeface        slide 1, 2, 3, 5, 6
  · [colour] #FFC000 is not in the template palette    slide 5, 6

  conformance 73.5% of 102 text run(s)
```

The template's theme is the authority. A deck that uniformly ignores it is still
non-conforming — the commonest font in a deck is never mistaken for the brand.

**Measured, not asserted** (`scripts/phase1_exit_check.py`, `scripts/run_benchmark.py`):

| Result | Evidence |
|---|---|
| Round-trip fidelity on 6 corpus decks | **100.00%** parts byte-identical, every deck |
| A one-object edit on 11 real third-party decks | **exactly 1 part changed**, 98.5–99.6% identical |
| A table-cell edit, measured inside the edited slide | **2 character substitutions**; 99.95% of the slide preserved |
| Native tables, charts, embedded workbooks, media | preserved and asserted on every edit |

The hardest corpus deck carries native charts with embedded Excel workbooks, grouped shapes, native tables, custom geometry, hyperlinks and speaker notes. The largest real deck tested is 289 parts across 64 slides with multiple slide masters.

## How the guarantee is enforced

Five mechanisms, all deterministic:

1. **Immutable source.** The uploaded file is snapshotted and hashed. Every output is a new artifact diffed against it, so rollback is choosing an earlier version rather than undoing an edit.
2. **The change set is a contract.** Nothing may modify a deck that is not written there first, reviewed, and approved.
3. **Passthrough by default.** Unchanged parts are copied byte-for-byte. Mutation requires an entry in the change set.
4. **Verify before deliver.** Any unattributed change, removed part, native-object loss or suspected rasterisation **blocks delivery**. The deck is not handed over.
5. **Locks are enforced at the verifier**, not just at the planner — including `--lock numbers`, which makes *"polish this deck but do not touch a single figure"* a checkable instruction.

**The model never writes OOXML.** It receives a structural summary and returns JSON; every proposed change is validated against the real deck before it enters the change set. A change naming a shape that does not exist is dropped at planning time, not discovered at apply time.

**The quality gate is never a model** ([ADR-0003](docs/decisions/0003-deterministic-quality-gate.md)). A model asked to grade its own slide reports success in the direction that makes it look useful. Findings are computed, and they carry repair instructions an authoring model can act on:

```
[ERROR] slide 15 · TextBox 4: extends 3.33in past the right edge (25.0% overflow)
        fix: reduce width to at most 3.33in, or move left to x <= 6.66in
```

## Install

```bash
pip install -e "src/engine[dev]"
python -m pytest tests -q          # 291 tests
```

Python 3.11+. No API key is required: the planner falls back to an offline stub, and every deterministic layer runs without credentials or network access.

To enable plain-language instructions, set `ANTHROPIC_API_KEY` and use `slide-wright edit --instruct "..."`.

## Repository layout

```
src/engine/slide_wright/    the engine — deterministic, no model calls
  package.py                OPC reader, hostile-archive guards
  inspect.py                structural model of decks, slides, shapes
  fidelity.py               part-level comparison, native-object census
  gate.py                   deterministic quality checks with repairs
  changeset.py              the contract: changes, locks, review lifecycle
  apply.py                  in-place edits, character-level
  report.py                 the change report and delivery gate
  session.py                transactional session, versions, rollback
  audit.py                  what is wrong with this deck
  brand.py                  template conformance
  sources.py                spreadsheets, with cell-level citations
  refresh.py                update figures from a source, deterministically
  planner.py                instruction -> validated change set
  llm/                      provider abstraction, budgets, offline stub
  corpus/                   deck profiler and adversarial generator
docs/                       architecture, 6 ADRs, guides
tests/                      291 tests, including regressions from real decks
scripts/                    benchmark, exit check, engine vendoring
private/                    project intelligence — gitignored, never committed
```

## Status

**Phase 1 complete. Phase 2 deliberately not started.**

| Phase | State |
|---|---|
| 0 — Validation | Technical gates **passed**. Demand gate **not run** — needs practitioner conversations. |
| 1 — Core engine | **Complete.** Exit condition met: 11/11 eligible real decks edited and verified. |
| 2 — MVP (backend, web) | **Gated on a paying customer.** Not started, on purpose. |
| 3 — Engine capabilities | Audit, citations, refresh and brand conformance **built**; the customer-facing half stays gated. |

There is no web application, no database and no account system, because no customer has yet said they want one. The roadmap gates that work on evidence rather than on readiness, and the evidence does not exist yet.

Phase 3's *engine* capabilities were built ahead of that gate because each is deterministic, testable offline, and useful whatever the eventual delivery surface turns out to be. Brand conformance currently **reports** drift; correcting it automatically is the next increment, and is worth building only once someone asks for it.

**Known gaps, stated plainly:**

- **SmartArt is untested.** No deck available for testing contains a `ppt/diagrams/` part, and it cannot be generated faithfully. Fidelity on SmartArt is unproven — not claimed.
- **Per-deck model cost is unmeasured.** Budgets and ceilings are implemented; the real figure needs an API key.
- Also unproven: OLE embedded objects, licensed fonts not installed locally, packages above ~300 parts.

## What this project has learned the hard way

The Phase 1 exit check ran 12 real decks through the full loop. **Three passed.** All 183 tests were green at the time.

Two bugs were hiding behind them: text edits silently matched nothing whenever the target spanned paragraphs, and — far worse — `apply()` reported **VERIFIED** while writing nothing at all. A failure that looks like a pass is the worst outcome this product can have, because nobody investigates a pass.

Both are fixed and are now permanent regression tests. The lesson is recorded in the project's foundation as a fact rather than a footnote: **a synthetic corpus validates the code against itself.** Green tests on decks you generated prove very little about decks someone else wrote.

## Licence

Not yet chosen — see [ADR index](docs/decisions/README.md). The engine it builds on is MIT ([ppt-master](https://github.com/hugohe3/ppt-master)), vendored at a pinned version with licence hygiene applied and recorded in `vendor/pin.json`.
