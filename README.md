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

A board pack, a pitchbook, an IC memo, a QBR — these are inherited, not authored. Last quarter's deck is the starting point for this quarter's, and each round of revisions costs hours of expensive people's evenings.

How *many* rounds, and how expensive, we do not yet know. That number decides whether this is a business, and no practitioner has been asked. It is the open question, not a settled premise.

And every AI tool treats an existing deck as an *import problem* — parse it, flatten it into the tool's own representation, re-emit it. That is lossy by construction. The result is a specific absurdity: **the decks that matter most are the ones AI can help with least**, because the one thing their owners cannot tolerate is a tool that touches something they did not ask it to touch.

Slide-Wright starts from the opposite premise. **Your file is evidence, not input.**

## What works today

```bash
slide-wright inspect  deck.pptx                     # structural summary
slide-wright audit    deck.pptx                     # what is wrong with this deck
slide-wright brand    house.potx deck.pptx          # where it departs from the template
slide-wright brand    house.potx deck.pptx --fix    # correct it, changing no content
slide-wright align    deck.pptx --fix              # snap near-miss edges, bounded
slide-wright tidy     deck.pptx -o out.pptx        # both of the above, one receipt
slide-wright refresh  deck.pptx --source comps.csv  # update figures, with citations
slide-wright verify   before.pptx after.pptx        # part-level fidelity report
slide-wright diff     before.pptx after.pptx        # what a reader would notice
slide-wright profile  deck.pptx                     # how adversarial is this deck?
slide-wright edit     deck.pptx \
    --set "3:5/r1/c1:9.4x=11.8x" \
    --lock numbers \
    -o out.pptx
```

### The reviewable path

`edit` approves what it proposes, which is what you want for a change you typed
yourself. For a change a model suggested, the decision belongs to a person —
so proposing, reviewing and applying are separate commands, and nothing reaches
a deck until someone approves it by id.

```bash
slide-wright propose deck.pptx --instruct "..." -o changes.json   # writes nothing else
slide-wright review  changes.json --approve c1 --reject c2        # a human decides
slide-wright apply   deck.pptx changes.json -o out.pptx           # only the approved
slide-wright history deck.pptx                                    # every version
slide-wright revert  deck.pptx --to 1                             # go back to one
```

`revert` undoes nothing. Every version is kept, so going back is choosing an
earlier one; the discarded versions stay in the workspace.

### Two questions, two commands

`verify` asks whether the package is intact — which parts differ, byte for
byte, and whether any native object was lost. It is the guarantee, and it is
the one that cannot be argued with.

`diff` asks what a reader would notice — which shape's text changed and to
what, what moved and by how far, what was resized. When a deck is blocked
because something changed that nobody asked for, the report now names the
figure that moved rather than the file that contains it:

```
  Changes nobody asked for
    · slide 3 — Table 2 (id=3) text …Alpha Corp[9.4 -> 11.8]x22.1%…
```

### Conform a deck to its template, without touching a word

Slides pasted in from other decks arrive carrying their old typefaces. `--fix`
corrects them and nothing else — the guarantee is inverted but the same in kind:
*change every typeface that does not conform, change not one word or number, and
prove it.*

On a real 350-part US government deck: 45 runs corrected, all 29 charts and 29
embedded workbooks byte-identical, and **zero content changes** in the structural
diff. The check is enforced, not asserted — if a formatting pass alters content,
the deck is refused rather than delivered.

A run whose typeface is `+mn-lt` is left alone. That is not a font, it is a
reference to the theme's own font, so the run already follows the template in the
only way that survives the template changing. Rewriting it to a literal name
would quietly break that link.

### Tidy an inherited deck

The ordinary case: a deck assembled from other decks, carrying their typefaces
and their almost-but-not-quite alignment. One command, one verification, one
point to revert to.

```
SLIDES WORTH A LOOK

  · slides 12, 14, 15, 17 — 4 of 25 slides hardcode a typeface (Arial)
    where the rest inherit from the theme
  · slides 9, 13, 15 — 3 slide(s) use a layout no other slide uses

TIDY — deck.pptx

  45 typeface(s) off the deck theme
  2 shape(s) nearly, but not quite, aligned

  345 of 350 package parts are byte-for-byte identical (98.57%)

  45 typeface(s) conformed · 2 shape(s) nudged · 0 words or numbers changed, verified
  revert with: slide-wright revert deck.pptx --to 0
```

Suspect slides are **named, never acted on** — calling a slide foreign is a
judgement, and the corrections stand without it. With no template given, the
deck's own theme is the authority: a deck assembled from several sources has a
visual system of its own, and the pasted-in slides are the ones departing from it.

### Snap the boxes that are almost lined up

Three headers at 1.00in, 1.01in and 1.00in, and somebody nudging them with
arrow keys at midnight. Automating that is easy; automating it *safely* is the
whole problem, because the hard part is deciding which shapes were meant to line
up at all.

Three rules, and two of them exist because measurement caught the code getting
it wrong:

1. **Nothing moves further than the tolerance** (0.02in by default). A shape two
   inches out of line is a decision; a shape a hundredth out is a slip.
2. **An alignment that is already exact is never broken** to fix a near one.
3. **A stray only snaps onto a line at least two shapes already share.** Without
   this the pass never terminated — two boxes near each other on different edges
   chased one another down the slide, one pass after another.

With all three, corrections only ever add exact alignments. The planning is then
iterated to a fixpoint internally, because snapping a shape onto a line makes
that line one member wider and can pull in a shape that was previously a lone
stray — on a 41-slide deck, 66 corrections were followed by 11 more. One run now
does all of it and leaves nothing behind.

A shape still moves at most once: the moment it is flush with another it is
anchored and held. Measured across the whole cascade on that deck, the largest
total displacement was 0.0191in against a 0.020in bound. Content is untouched
and that is checked, not claimed.

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
| Round-trip fidelity on 3 corpus decks | **100.00%** parts byte-identical, every deck |
| A one-object edit on the 12 editable third-party decks | **exactly 1 part changed** — 12 of 12 — at 96.67–99.71% identical |
| A table-cell edit, measured inside the edited slide | **2 character substitutions**; 99.95% of the slide preserved |
| Native tables, charts, embedded workbooks, media | preserved and asserted on every edit |
| The whole loop on a 400-slide deck | open, read, audit, plan, **apply 2,400 corrections** and diff, in **2.3 s total** |
| The heaviest real deck — 52 slides, 55 MB | opens in 0.19 s, reads in 0.21 s, applies and verifies in 3.0 s |

The hardest corpus deck carries native charts with embedded Excel workbooks, grouped shapes, native tables, custom geometry, a picture, hyperlinks, speaker notes, and text whose runs do not divide on word boundaries. The largest real deck tested is 340 parts across 52 slides and 55 MB.

Timings are `scripts/perf_report.py --large`. The largest real deck available is
52 slides, so the 100-, 200- and 400-slide rows are synthesised — text only, so
they measure how the engine scales with shape count while the real decks measure
what media and charts cost. Applying is linear in the number of changes at about
**0.4 ms each**, which is the property worth having; the absolute number will
differ on your machine.

The lower end of that fidelity range is arithmetic rather than a worse edit. A one-object change touches exactly one part in every case; on a 45-part deck that is 97.8% and on a 350-part deck it is 99.7%. The count is the claim — the percentage is the count divided by how much else the deck happened to contain.

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
python -m pytest tests -q          # 970 tests
```

Python 3.11+. **No API key is required.** With none set the planner falls back to an offline stub, and every deterministic layer — ingest, gate, apply, verify, audit, refresh, brand, SmartArt — runs unchanged.

For plain-language instructions, copy `.env.example` to `.env` and set `GEMINI_API_KEY` (the free tier is sufficient), then:

```bash
slide-wright edit deck.pptx --instruct "change the Alpha Corp multiple to 11.8x"
```

Development runs at zero cost by design. Every model call is recorded — provider, model, tokens, latency, and what it *would* cost at published paid rates — so the free-tier constraint stays measurable rather than assumed.

## The workspace

The same loop, with the deck on screen. It runs entirely on your machine
(ADR-0008, ADR-0010): loopback only, no accounts, no storage, no telemetry, and
no flag that would let it listen anywhere else.

```bash
pip install -e "src/product/api"
npm --prefix src/product/web install && npm --prefix src/product/web run build
python -m slide_wright_api
```

That opens `http://127.0.0.1:8787`. Point it at a `.pptx` **by path** — the deck
is opened where it sits and is never uploaded.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Pitchbook_v9.pptx                    .slidewright/Pitchbook_v9   engine │
├────────────┬─────────────────────────────────────────────┬───────────────┤
│  FILMSTRIP │              SLIDE CANVAS                   │   CHANGE SET  │
│   1  ○     │      ┌─────────────────────────────┐        │  1 applied    │
│  12  ●←    │      │  slide 12, changed region   │        │  1 blocked    │
│  34  ●     │      │  outlined, nothing else     │        │               │
│  60  ○     │      └─────────────────────────────┘        │  RESULT  ✓    │
│  58 of 60 untouched                                      │  125/126 parts│
├────────────┴─────────────────────────────────────────────┴───────────────┤
│  Scope: slide 12   Protect: numbers wording layout   ▸ What should change?│
└──────────────────────────────────────────────────────────────────────────┘
```

It opens on the **audit** — what is wrong with this deck — because that is what
someone does first with a deck they inherited. Findings are split before
anything else:

```
SLIDE-WRIGHT CAN CORRECT THESE          Deterministic; content is locked
  consistency · deck    266 of 266 runs name a typeface directly
  layout · slide 8,9,13  6 shapes sit within 0.02in of an edge others share
  → 266 runs re-linked · 6 shapes snapped, largest movement 0.017in of 0.02in
    [ Propose 272 corrections ]   proposes only; you review each one

FOR YOU TO DECIDE                        Slide-Wright will not touch these
  narrative · 26 slides   26 slide(s) have no title
```

There is **no score**. One number would compress "no slide title makes a claim"
and "266 runs hardcode a typeface" into a figure that means neither. And the
split is the engine's answer, not the interface's: a rule carries a remedy only
when a deterministic pass can actually correct it, so the UI cannot offer a fix
the engine will not perform.

Measured on a real 26-slide NASA deck: **272 corrections applied, 0 unexpected
changes, and 0 of them changed what the deck says** — 272 of 272 deltas are
formatting, every native table, chart, workbook and text run intact.

The workspace covers every capability the engine has:

| | |
|---|---|
| **Audit** | What is wrong with this deck, split into what Slide-Wright can correct and what only you can decide |
| **Tidy** | Conform typefaces and snap near-miss edges — to the deck's own theme, or to a template you supply |
| **Sources** | Refresh figures from a workbook, each carrying the cell it came from |
| **Changes** | Review every proposal with its provenance, approve or reject one at a time |
| **Diff** | Compare any two versions — leading with the sharpest claim: *no figure changed* |
| **Protect** | Lock a slide or a single object; the engine refuses a change that collides with it |
| **History** | Every version, and going back to one |
| **Export** | Refused outright if verification failed, and never over your original |

Any two versions can be compared. That answers a different question from
verification, and keeping them apart is most of the value:

```
v000 → v001        0 changes what it says · 272 change how it looks
                                                        [ v000 | v001 ]
CHANGES HOW IT LOOKS       Typeface, colour, position, size.
  1  Title 1 (id=3) run 1 font 'Century Gothic' -> '+mn-lt'   formatting
```

`verify` says the package is intact — which parts differ byte for byte, whether
any native object was lost. It cannot tell you whether a *figure* moved.
`diff` can. Flipping between the two versions on the canvas is a **hard cut with
no transition**, because a crossfade between two near-identical slides is exactly
what hides the difference between them.

Three properties it is built to hold, each asserted by a test:

- **Nothing is written before you approve it.** Proposing leaves the file
  byte-identical; the change set is the contract you read first.
- **There is no green state unless the engine said `deliverable`.** No "verified
  with warnings" invented in the interface.
- **A tidy proposes; it never applies.** The CLI approves its own change set,
  which is right when you typed the command. A button that did both would be the
  one place mutation happens without a person saying yes.
- **No progress bar.** The engine does not know how long a stage takes, so the
  apply narrates the stages that actually happen rather than interpolating a
  number.

One thing that leaves the machine, stated plainly: if you configure a model key
and *describe* a change in prose, a structural summary of the whole deck — every
slide title, and the first 70 characters of every text object — goes to that
provider. Editing objects directly, auditing, verifying and reverting send
nothing, and with no key configured nothing is sent at all.

Develop against it with `npm --prefix src/product/web run dev` (port 5173,
proxying `/api` to the engine).

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
  smartart.py               diagram detection; read-only by design
  planner.py                instruction -> validated change set
  llm/                      provider abstraction, Gemini, budgets, usage ledger
  corpus/                   deck profiler and adversarial generator
src/product/api/            the local HTTP surface (ADR-0010)
  contracts.py              wire types; the client never touches engine classes
  workspace.py              open documents; derives `before` from the deck
  app.py                    the loop as routes, plus SSE progress
src/product/web/            the workspace client — React, TypeScript, Motion
  api/                      typed service layer, one origin, no second base URL
  design/                   primitives; none may wear the attention colour
  motion/                   three durations, two curves, one exception
  workspace/                filmstrip · canvas · audit · sources · changes
                            diff · result · history · export
docs/                       architecture, 10 ADRs, guides
tests/                      970 engine and API tests, plus 204 in the client
scripts/                    benchmark, exit check, engine vendoring
private/                    project intelligence — gitignored, never committed
```

## Status

**Phase 1 complete. The local product surface is built. The hosted one is still gated.**

| Phase | State |
|---|---|
| 0 — Validation | Technical gates **passed**. Demand gate **not run** — needs practitioner conversations. |
| 1 — Core engine | **Complete.** Exit condition met: 11/11 eligible real decks edited and verified. |
| 2 — Local workspace | **Built.** API and client over the existing engine, loopback only. |
| 2 — Hosted MVP (accounts, storage, tenancy) | **Gated on a paying customer.** Not started, on purpose. |
| 3 — Engine capabilities | Audit, citations, refresh and brand conformance **built**; the customer-facing half stays gated. |

That split is the point, and it is worth being precise about. ADR-0008 makes local execution the *primary* delivery model, because the documents this engine exists to edit are commonly blocked from upload before a vendor is ever evaluated. Building the local workspace is therefore building the product, not building ahead of the gate.

What remains gated is everything that only a hosted product needs: **no accounts, no database, no cloud storage, no tenancy, no billing, no telemetry.** None of it is written, because no customer has yet said they want it. The roadmap gates that work on evidence rather than on readiness, and the evidence still does not exist.

Phase 3's *engine* capabilities were built ahead of that gate because each is deterministic, testable offline, and useful whatever the eventual delivery surface turns out to be. Brand conformance currently **reports** drift; correcting it automatically is the next increment, and is worth building only once someone asks for it.

**Known gaps, stated plainly:**

Which OOXML constructs this has actually been measured against is in
[docs/compatibility.md](docs/compatibility.md), generated by running the engine
over every deck on the machine. Anything no deck there contains is reported as
**UNKNOWN** rather than assumed — there is no way to write a verdict into that
table by hand.

- **Chart data cannot be edited.** Charts are read, counted and preserved — a real 350-part government deck with 29 charts and 29 linked workbooks came through a text edit with every chart, workbook and data link byte-identical — but changing a figure *inside* a chart is refused. A chart stores its numbers twice, in a cached series and an embedded workbook, and changing one without the other produces a deck whose picture disagrees with its own "Edit Data". See ADR-0009.
- **SmartArt cannot be edited.** It is read, counted and preserved — 16 diagram-bearing fixtures of 26, 0 damaged — but any change *targeting* a diagram is refused before anything is written. A diagram is four correlated parts plus a cached rendering; editing one out of step with the others corrupts the file with no error. Refusing is the honest answer until that can be done deterministically. See ADR-0007.
- **Per-deck model cost is measured on the prompt side only.** `scripts/cost_report.py` computes the exact prompt every deck here produces: the most expensive real deck (41 slides, 18 MB) is **7,204 input tokens, about $0.008 a plan call** at published Gemini Flash rates, and the median across 29 decks is $0.0002. That corrects a "$5-15 per deck" estimate this repo had been carrying unverified, by three orders of magnitude. What it does *not* measure is what a model actually returns — that needs a key and somebody's quota, and the usage ledger records the real counts when a run happens. Token counts are characters over 3.6, which is a stated divisor rather than a measurement; the character counts are exact.
- **Licensed fonts not installed locally are unproven.**
- **The round-trip engine completes a full cycle on 5 of 26 fixtures.** Re-measured 8 Sep 2026, and the limitation is narrower than it sounds: all 26 *ingest*, and the 5 that also export come back **100.00% byte-identical with nothing removed**. The other 21 produce no output file at the export step. It is the secondary path — the in-place applier handles every deck in the corpus, and it is what every guarantee in this README is measured against.
- **The workspace canvas is a structural view, not a render.** Objects are drawn at the exact position and size the file specifies, which is what makes "nothing else moved" checkable. Fills, effects, picture content, text colour and PowerPoint's line breaking are not reproduced, so it cannot tell you whether a slide *looks* good — only where things are.

## What this project has learned the hard way

The Phase 1 exit check ran 12 real decks through the full loop. **Three passed.** All 183 tests were green at the time.

Two bugs were hiding behind them: text edits silently matched nothing whenever the target spanned paragraphs, and — far worse — `apply()` reported **VERIFIED** while writing nothing at all. A failure that looks like a pass is the worst outcome this product can have, because nobody investigates a pass.

Both are fixed and are now permanent regression tests. The lesson is recorded in the project's foundation as a fact rather than a footnote: **a synthetic corpus validates the code against itself.** Green tests on decks you generated prove very little about decks someone else wrote.

## Licence

Not yet chosen — see [ADR index](docs/decisions/README.md). The engine it builds on is MIT ([ppt-master](https://github.com/hugohe3/ppt-master)), vendored at a pinned version with licence hygiene applied and recorded in `vendor/pin.json`.
