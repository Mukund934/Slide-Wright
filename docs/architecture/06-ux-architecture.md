# UX Architecture

**Status:** Proposed · **Updated:** 2026-09-04

## The governing constraint

A product whose entire promise is *"I will not touch what you did not ask me to touch"* **cannot ship an interface that redecorates itself with unrequested motion.** The UI must behave the way the engine behaves.

Restraint here is not a style preference. It is the product argument, made visible.

This aligns with the standing design reference, whose own philosophy is *"preserve the existing product… sometimes the best enhancement is to remove something"* and *"do not confuse complexity with quality."*

## The one screen that matters

Not a dashboard. **The deck is the interface.**

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Pitchbook_v9.pptx          [ Original | Proposed | Changes ]     Export │
├────────────┬─────────────────────────────────────────────┬───────────────┤
│            │                                             │               │
│  FILMSTRIP │              SLIDE CANVAS                   │   CHANGE SET  │
│            │                                             │               │
│   1  ○     │      ┌─────────────────────────────┐        │  Requested 3  │
│   2  ○     │      │                             │        │  Applied   3  │
│   …        │      │   slide 12, changed regions │        │  Unasked   0  │
│  12  ●←    │      │   outlined, nothing else    │        │               │
│   …        │      │                             │        │  ▸ S12 table  │
│  34  ●     │      └─────────────────────────────┘        │    4 cells    │
│   …        │                                             │  ▸ S34 title  │
│  60  ○     │                                             │               │
│            │                                             │  58 slides    │
│  ○ untouched                                             │  unchanged ✓  │
│  ● changed │                                             │               │
├────────────┴─────────────────────────────────────────────┴───────────────┤
│  ▸ What should I change?                                                 │
└──────────────────────────────────────────────────────────────────────────┘
```

Three regions, one job each:

- **Filmstrip** — where the change *is not*. The overwhelming majority of dots stay hollow, and that is the reassurance.
- **Canvas** — the slide, with changed regions outlined. Nothing else decorated.
- **Change set** — the contract. Visible *before* application, not only after.

## The interaction that defines the product

**Propose → review → apply → verify → revert.**

Most AI tools have two states: *loading* and *done*. That is the wrong shape when the user is frightened of the output.

| Stage | What the user sees | Why |
|---|---|---|
| **Propose** | The change set, itemised, before anything is written | Consent precedes mutation |
| **Review** | Per-change accept/reject; before/after on the canvas | Partial acceptance is normal, not an edge case |
| **Apply** | Progress, per slide, naming what is happening | 10–20 minutes needs honest narration, not a spinner |
| **Verify** | Integrity results — counts, unattributed changes | The proof, computed |
| **Revert** | Per change, not just whole-document | Every change is individually attributed, so it is individually reversible |

## The primary motion

**One animation deserves real investment: carrying the eye to what changed.** When the report says slide 12 changed, motion connects the claim to the evidence — filmstrip dot, to canvas, to the outlined region.

The design reference lists *"highlighting a changed state"* among the legitimate uses of motion. In this product it is not merely legitimate; it is the point.

Everything else stays at the floor: state transitions, reveal/dismiss, focus feedback. No entrance flourishes, no bounce, no constant movement. `prefers-reduced-motion` respected from the first commit.

## The AI surface

Not a chatbot in a sidebar. Three entry points, each carrying its own context:

| Surface | Context | Example |
|---|---|---|
| **Deck-level command bar** | Whole deck | *"Update all Q2 figures from this spreadsheet"* |
| **Selection action** | Selected slides/objects | *"Make these three board-ready"* |
| **Object inspector** | One object | *"Why isn't this chart working?"* |

Context comes from **selection**, not from the user re-describing what they mean. The distinction between *"make this less crowded"* said with slide 7 selected and said with nothing selected is the whole difference between a usable tool and a chat toy.

## States that are normal operation, not edge cases

In a deck-processing product these are the common path and must be designed first, not last:

| State | Design requirement |
|---|---|
| **Ingesting** (60 slides) | Honest progress, not a spinner |
| **Long job running** | Per-slide narration; leaving the page must be safe |
| **Failed closed** | Calm and specific. The engine refuses rather than corrupting — that is a *feature* and must read as one, never as a crash. |
| **Partially applied** | 2 of 3 changes applied; explain the third clearly |
| **Unattributed change detected** | Job blocked, deck not delivered, reason stated |
| **Empty** | First upload — the only onboarding that matters |

The "failed closed" state deserves particular care. Users trained by other tools read refusal as breakage. Here it is the guarantee working.

## First-run experience

One box: **drop a `.pptx`.**

No signup wall before value, no template gallery, no tour. Upload a deck, ask for one change, see the change report. If that sequence does not produce the reaction, no amount of onboarding will fix it.

## Deferred, deliberately

Collaboration, comments, real-time multiplayer, mobile editing, a template marketplace, an analytics dashboard. Every one is a real feature of a mature product and a distraction from proving the core promise.

## Open design questions

- How to show a *semantic* diff (a rewritten sentence) as clearly as a structural one?
- How should a 60-slide filmstrip behave — the whole value is seeing that most of it is untouched, which argues against pagination?
- Does the change set belong beside the canvas, or as a full-screen review step before apply?
