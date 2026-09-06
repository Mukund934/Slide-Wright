# ADR-0007 — SmartArt is read-only, and the round-trip engine cannot ingest it

Status:  accepted
Date:    2026-09-06

## Context

SmartArt was the last untested construct in the fidelity corpus. Until 6 Sep 2026 every fidelity result — 100% round-trips, byte-identical edits — was measured on decks containing **zero** SmartArt, so nothing was known about the construct most likely to be silently destroyed.

A SmartArt graphic is not one object. It is four correlated parts plus a cached rendering:

```
ppt/diagrams/data1.xml        the authored model — nodes, text, hierarchy
ppt/diagrams/layout1.xml      the layout algorithm
ppt/diagrams/quickStyle1.xml
ppt/diagrams/colors1.xml
ppt/diagrams/drawing1.xml     a cached rendering
```

The slide binds them through a single `<dgm:relIds>` element carrying four relationship ids. Edit one part out of step with the others and PowerPoint either repairs the file, re-renders from the stale cache, or flattens the diagram to a picture — with no error anywhere.

Ten third-party fixtures were obtained (Apache POI, Apache-2.0; LibreOffice, MPL-2.0) and the validation matrix run against them.

## Measurements

| Case | Result |
|---|---|
| **A · read** | **10/10** diagrams read completely — parts, authored text, structural points |
| **B · preserve** | **2/2 testable** — editing other content left the diagram byte-identical, 97.8–97.9% package fidelity (8 fixtures are diagram-only, with nothing else to edit) |
| **C · refuse** | **10/10** attempted diagram edits refused |
| **D · round-trip** | **0/10** survived the heavy engine |
| **Damage** | **0** diagrams damaged anywhere |

### The round-trip finding, isolated causally

The engine refuses every SmartArt fixture at ingest:

```
Canonical authoring projection failed: Visible text requires one direct root
font-family default; semantic groups and text elements keep only real overrides
```

That message does not name SmartArt, so the cause was isolated rather than assumed. Taking one fixture and removing **only** the diagram — same deck, same fonts, same everything else:

```
WITH smartart      -> engine refused
WITHOUT smartart   -> engine ACCEPTED
```

The diagram is the cause.

## Decision

1. **SmartArt is read-only.** `guard_edit` refuses any change targeting a diagram, before anything is written, with a message that names the diagram, its node count and why.
2. **Every edit asserts diagrams survived.** `assert_preserved` compares diagram count, part count, text nodes **and structural points** between source and output. A shortfall fails the job.
3. **Structure is counted separately from text.** `poi-smartart.pptx` has 26 structural points and no text at all — a diagram of empty boxes. Verifying text alone would let that entire diagram be destroyed without an assertion firing.
4. **The in-place applier is the only supported path for SmartArt decks.** It handles them; the round-trip engine cannot ingest them.

## Alternatives

| Option | Rejected because |
|---|---|
| Edit `data1.xml` and let PowerPoint re-render | The cached `drawing1.xml` may render instead, showing the *old* diagram with no error. Exactly the silent corruption this product exists to prevent. |
| Edit both data and drawing cache | Requires reimplementing PowerPoint's layout engine. Any divergence is invisible until a customer opens the file. |
| Delete the cache to force a re-render | Changes rendering on machines we do not control, and some fixtures ship no cache at all. |
| Flatten SmartArt to shapes and edit those | Destroys the diagram. Precisely the failure mode we refuse. |
| Say nothing and let edits through | Would produce plausible-looking corruption. Worse than refusing. |

## Consequences

- A user cannot ask us to change text inside an org chart. They are told why, and told to edit it in PowerPoint.
- Decks *containing* SmartArt are fully supported for everything else — verified byte-identical on the diagram parts.
- The round-trip engine is unavailable for SmartArt decks. Since the in-place path is both narrower and more capable here, this is a smaller loss than it appears — and it is further evidence for leading with in-place editing (ADR-0002).
- A corrupted diagram part is now reported as damage rather than raised as a parser error.

## Reversal

Revisit if PowerPoint's diagram cache semantics can be satisfied deterministically — specifically, if editing `data1.xml` while removing or correctly regenerating `drawing1.xml` proves reliable across PowerPoint, LibreOffice and Google Slides. That needs rendering evidence on all three, not a code change.

`tests/engine/test_smartart.py::TestRoundTripEngineLimitation` asserts the engine still refuses. If that test starts failing, the limitation has been fixed upstream and this ADR needs revisiting.
