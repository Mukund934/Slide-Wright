# ADR-0009 — Chart data is read-only; charts are preserved

Status:  accepted
Date:    2026-09-06
Follows: ADR-0007 (SmartArt is read-only) — same hazard, different construct

## Context

A native chart stores its numbers twice:

```
ppt/charts/chart1.xml                  the cached series — what is drawn
ppt/embeddings/Microsoft_Excel_*.xlsx  the authored workbook — what "Edit Data" opens
ppt/charts/_rels/chart1.xml.rels       the relationship binding them
```

Edit the cached series alone and the picture updates while the workbook keeps
the old numbers. Edit the workbook alone and nothing visible changes until
something triggers a refresh. Either way the file disagrees with itself, and the
disagreement surfaces when a reviewer opens the data — in front of the audience
the deck was made for.

This is ADR-0007's hazard wearing different clothes. A diagram has an authored
model and a drawing cache; a chart has an authored workbook and a cached series.
Both fail silently and both fail late.

The question was raised sharply from outside: could a chart's numbers be changed
programmatically *without* breaking the data link, and if not, was that fatal?
It splits in two, and the halves have different answers.

## Measurements

**Preservation — answered, and the answer is good.**

Measured on `eia-aeo2023-release.pptx`, a real 350-part US EIA release deck
carrying **29 charts, 29 linked workbooks and 29,922 cached values**. One text
edit elsewhere in the deck:

| | Result |
|---|---|
| Package fidelity | **99.71%** — one part changed, `ppt/slides/slide1.xml` |
| Chart XML | **all 29 byte-identical** |
| Embedded workbooks | **all 29 byte-identical** |
| Chart → workbook links | **all 29 intact** |

Reproduced on `pypptx-chart-types.pptx` (31 charts) and the synthetic
adversarial deck. Editing a deck does not disturb its charts.

**Modification — not attempted, and deliberately refused.**

Nothing in the system can change a chart's numbers. Before this ADR that was
true by accident: the applier looked for the value on the *slide*, failed to
find it, and reported *"shape 3 does not contain the text 'Q4'"*. The value is
in the deck — it is in the chart part — so the message sent a reader looking for
something demonstrably present.

## Decision

1. **Chart data is read-only.** `guard_edit` refuses any change targeting a
   chart before anything is written, naming the series count, the value count
   and the linked workbook, and saying why.
2. **Charts are read.** `values()` returns the cached series. An audit can ask
   whether a slide's prose agrees with the chart beside it, which is useful and
   cannot corrupt anything.
3. **Every edit asserts charts survived.** Chart count, chart parts, workbooks,
   series and values are compared before and after. A shortfall fails the job.
4. **A data link counts only if the workbook is present.** A relationship
   pointing at a missing part is tracked separately as a dangling link, and
   gaining one fails the job. This case found a real bug during implementation:
   the first version read the relationship without checking its target existed,
   so a chart whose workbook had been stripped still reported a healthy link —
   the picture intact, the data unreachable, and nothing complaining.

## Alternatives

| Option | Rejected because |
|---|---|
| Edit the cached series only | The workbook keeps the old numbers. "Edit Data" then contradicts the slide |
| Edit the workbook only | Nothing renders differently until a refresh that may never happen |
| Edit both, keeping them in step | The honest long-term answer, and a real project: rewriting an `.xlsx` inside a `.pptx` while preserving formulas, formatting and the chart's own caching. Not something to do casually on a construct where being wrong is this expensive |
| Delete the workbook to force a re-read | Destroys the data link — the exact failure mode above |
| Let edits through and hope | Would produce plausible-looking wrong numbers in financial documents. The worst outcome this system can have |

## Consequences

- A user cannot ask us to change a figure inside a chart. They are told why, and
  told where they can change it.
- Decks *containing* charts are fully supported for everything else, verified
  byte-identical on 29 charts of a real government deck.
- `refresh.py` updates figures in tables, not charts. That asymmetry is now
  explicit rather than implied.
- The path to supporting chart edits is clear and narrow: write both
  representations atomically, and prove it across the corpus before claiming it.

## Reversal

Revisit when the embedded workbook can be rewritten and the cached series
regenerated in step, demonstrated across the chart corpus and confirmed by
opening "Edit Data" in PowerPoint — not by a passing test alone. Until then the
refusal is the honest answer.
