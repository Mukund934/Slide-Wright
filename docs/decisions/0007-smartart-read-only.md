# ADR-0007 — SmartArt is read-only, and the in-place applier is its only path

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

Twenty-six third-party fixtures have been obtained (Apache POI, Apache-2.0; LibreOffice, MPL-2.0; python-pptx, ts-pptx, pptx-automizer and dotnet/Open-XML-SDK, MIT; US EIA and NASA, public domain) and the validation matrix run against them. Sixteen contain a diagram; ten deliberately do not, as controls.

## Measurements

| Case | Result |
|---|---|
| **A · read** | **16/16** diagrams read completely — parts, authored text, structural points |
| **B · preserve** | **9 decks** edited with every diagram byte-identical, 96.7–99.7% package fidelity (12 fixtures are diagram-only, with nothing else to edit) |
| **C · refuse** | **16/16** attempted diagram edits refused |
| **D · round-trip** | **2/21** ingested by the heavy engine (measured before the corpus grew to 26) |
| **Damage** | **0** diagrams damaged anywhere |

The hardest case is `nasa-es6-exit.pptx`: a real 52-slide, 340-part NASA presentation carrying 11 diagrams across 55 diagram parts, 323 structural points and 93 media parts. One text edit changed exactly one part — 99.7% fidelity — with all 11 diagrams intact.

`pypptx-ole-object.pptx` closes the other long-standing gap. Moving the OLE frame itself left `ppt/embeddings/Microsoft_Excel_Worksheet.xlsx` byte-identical, with only `slide1.xml` changed.

### What the round-trip engine actually refuses

The heavy engine refuses every diagram-bearing fixture at ingest:

```
Canonical authoring projection failed: Visible text requires one direct root
font-family default; semantic groups and text elements keep only real overrides
```

An earlier version of this ADR concluded from that "the diagram is the cause", on the strength of one experiment: stripping only the diagram from a fixture turned a refusal into an acceptance. That experiment is sound and still reproduces, but the conclusion drawn from it was too strong. The expanded corpus contains five decks with **no diagram at all**, and three of them are refused with a byte-identical message:

| | ingested | refused |
|---|---|---|
| with a diagram | **0** / 16 | 16 |
| without a diagram | 2 / 5 | **3** |

So a diagram is **sufficient** to trigger the refusal, not **necessary**. The real constraint is the engine's font-family projection, which SmartArt reliably violates and which other content violates too. Naming SmartArt as the cause would have sent anyone debugging a refused chart-only deck in the wrong direction.

This does not weaken the decision below. It is *stronger* evidence for it: our own in-place applier handles every fixture in the corpus, including the ones the heavy engine will not ingest.

## Decision

1. **SmartArt is read-only.** `guard_edit` refuses any change targeting a diagram, before anything is written, with a message that names the diagram, its node count and why.
2. **Every edit asserts diagrams survived.** `assert_preserved` compares diagram count, part count, text nodes **and structural points** between source and output. A shortfall fails the job.
3. **Structure is counted separately from text.** `poi-smartart.pptx` has 26 structural points and no text at all — a diagram of empty boxes. Verifying text alone would let that entire diagram be destroyed without an assertion firing.
4. **The in-place applier is the only supported path for SmartArt decks.** It handles all of them. The round-trip engine ingests none of them — and refuses most diagram-free decks too, so this is a limit of that engine rather than a property of SmartArt.

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
- The round-trip engine is unavailable for every SmartArt deck, and for 3 of the 5 diagram-free fixtures as well — 19 of 21 overall. Since the in-place path handles all 21, this is a smaller loss than it appears, and it is the strongest evidence yet for leading with in-place editing (ADR-0002). Do not describe this as a SmartArt restriction; a chart-only deck can be refused for the same reason.
- A corrupted diagram part is now reported as damage rather than raised as a parser error.

## Reversal

Revisit the read-only decision if PowerPoint's diagram cache semantics can be satisfied deterministically — specifically, if editing `data1.xml` while removing or correctly regenerating `drawing1.xml` proves reliable across PowerPoint, LibreOffice and Google Slides. That needs rendering evidence on all three, not a code change.

`tests/engine/test_smartart.py::TestRoundTripEngineLimitation` asserts the engine still refuses. If that test starts failing, the limitation has been fixed upstream and this ADR needs revisiting.
