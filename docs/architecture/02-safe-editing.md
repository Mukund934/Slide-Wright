# Safe Editing Architecture

**Status:** Proposed · **Updated:** 2026-09-04

This is the defining capability. Everything else in the system exists to make this work.

> **The promise:** change what was asked, preserve everything else.
> **The proof:** a verifiable account of what changed and what did not.

The promise is the product. The proof is why a professional will risk a real deck on it.

---

## What is already verified

Measured on this machine, 4 Sep 2026, on real third-party decks:

| Result | Measurement |
|---|---|
| Untouched round-trip, real 19-slide deck (105 parts, 6 native tables) | **105/105 parts byte-identical** |
| Untouched round-trip, real 17-slide deck (407 shapes) | **76/76 byte-identical** |
| **One slide title edited** | **exactly 1 of 105 parts changed**; native table preserved; 33/33 text runs intact |

The mechanism is a **per-subtree content hash** recorded at ingest (`initial_authoring_subtree_sha256`). At render, any object whose hash is unchanged is emitted from its **original XML** rather than re-serialised. Untouched means untouched at the byte level, not "re-generated to look the same."

**Unverified:** whether this holds on decks containing SmartArt, embedded Excel charts, grouped shapes or custom XML parts. Neither tested deck contained any. This is risk R2 and Phase-0 experiment 1.

---

## The five guarantees

### G1 — Immutable source

The uploaded file is snapshotted, hashed, and never mutated. Every output is a new artifact diffed against it. Rollback is trivial because the original always exists.

### G2 — Object identity and provenance

Every object carries a stable reference to its origin (`source_ref`) and a content hash. This is what makes "did this change?" answerable by arithmetic rather than inspection.

### G3 — The change set is a contract

The planner produces an explicit list of intended mutations **before anything is written**. It is inspectable, and it is the reference the verifier checks against. Anything not in the change set that changed is a **defect**, not a surprise.

### G4 — Passthrough by default

Unchanged objects are emitted from original XML. The default is *do nothing*. Mutation requires an entry in the change set.

### G5 — Verify before deliver

Post-render, diff every part against the immutable source:
- every changed part must map to a change-set entry
- **any unattributed change fails the job** — the deck is not delivered
- native-object counts (`<a:tbl>`, chart parts, pictures) must be ≥ source

Failing closed is correct here. Delivering a quietly corrupted board deck is far worse than delivering nothing.

---

## Granularity: what we actually promise

There are two paths, and they promise different things. Both are measured.

### In-place path — text, table cells, geometry, font size

`apply.py` patches the target run directly in the slide XML and copies every
other part byte-for-byte. Measured on the corpus deck, changing a table cell
from `9.4x` to `11.8x`:

```
EDITED SLIDE, character-level
  source chars     3706
  identical chars  3704   (99.95% of the slide preserved)
  distinct edits   2      '9' -> '11'   and   '4' -> '8'
  text runs        17 -> 17
```

**Two character substitutions.** Not a rebuilt slide that happens to look the
same — the same bytes, with two numbers different.

So on this path the promise is the strong one:

> **"Every byte of your file is identical except the characters you asked to change."**

This is enforced in tests (`test_apply.py::TestNarrowness`), which fail if an
edit produces more than three diff hunks or preserves less than 99% of the
edited slide.

### Round-trip path — structural and visual change

Anything the in-place applier cannot do precisely — restructuring, redesign,
regenerated layouts — routes through the engine, which **rebuilds** each slide
it touches from the intermediate representation:

```
Round-trip export summary: passthrough=18  rebuilt=1
```

Untouched slides remain byte-identical; the edited slide is re-serialised. The
promise there is the weaker, still-useful one:

> **"Slides you did not target are byte-for-byte identical. On a slide we did
> change, every object you did not target is preserved and verified."**

### The rule

**Prefer the in-place path, and refuse rather than widen.** `apply.py` raises
`ApplyError` on any operation it cannot perform surgically instead of silently
falling back to a rebuild. Widening the blast radius without telling the user is
exactly the failure this product exists to prevent.

---

## The mandatory safety rail

A verified defect in the underlying engine (F11), which any product on it **must** neutralise:

> Exporting **without** `--native-charts-and-tables` silently converts native tables into pictures **and discards the edits**, while reporting `status=passed-with-warnings`.

Measured:
```
native <a:tbl>   before=1  after=0     ← table destroyed
pictures         before=0  after=1     ← replaced by an image
edits present:   False                 ← silently lost
```

**Required, non-negotiable:**
1. Force `--native-charts-and-tables` on every export path. No configuration option to disable it.
2. Assert post-export that native table and chart-part counts are ≥ source.
3. Fail the job loudly on any shortfall.

Silent numeric data loss in a board deck is the worst failure this product could have. Cost of prevention: one flag and three assertions.

---

## User-facing controls

| Control | Behaviour |
|---|---|
| **Lock slide / object** | Marked `frozen`; the planner may not target it; verifier treats any change as a hard failure |
| **Preserve exact numbers** | Numeric runs immutable — reformat freely, never re-render a value |
| **Scope** | Restrict a change to a slide range or section |
| **Preview** | Change set shown before application, not after |
| **Revert** | Per-change, not just whole-document — every change is individually attributed |

"Preserve exact numbers" deserves emphasis: *"make this deck much more polished but do not touch a single figure"* is a genuinely common professional request that no current tool can honour with confidence.

---

## The change report

Deterministic, generated from hashes, not from the model's account of itself:

```
CHANGE REPORT — Pitchbook_v9.pptx

Requested   3 changes
Applied     3 changes
Unrequested 0                                  ← must be zero or the job fails

Slides      58 of 60 byte-for-byte identical
            2 slides modified

  Slide 12  · table "FY26 Summary" — 4 cells updated
            · 31 other objects on this slide preserved and verified
  Slide 34  · title text updated
            · 11 other objects preserved and verified

Integrity   native tables    6 → 6   ✓
            chart parts      4 → 4   ✓
            embedded images 18 → 18  ✓
            editable runs  842 → 842 ✓
```

Every line is computed. None of it is the model's opinion of its own work.
