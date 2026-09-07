# Design Language

**Status:** Accepted · **Updated:** 2026-09-07

The [UX architecture](06-ux-architecture.md) says what the interface must *do*.
This says what it must *look and behave like*, and why. The tokens live in
`src/product/web/src/styles.css` and `src/product/web/src/motion/tokens.ts`;
this document is the argument behind them, not a second copy of them.

## The governing rule

> **The interface has exactly one attention colour, and it means *this changed*.**

Filmstrip marks, canvas outlines and change-set rows all use it. Nothing else in
the application may. That single restriction does most of the design work:

- "Fifty-eight slides untouched" becomes visible at a glance rather than
  countable. The reassurance the product sells is *the absence of the colour*,
  and absence is only legible against a surface where nothing else competes.
- No button, badge, link, focus ring on a neutral control, or decorative accent
  can borrow it. A primary button is distinguished by **weight**, not hue.

The second restriction follows from the first: **verdict colours are spent only
on what the engine determined.** Green means `deliverable` came back true. Red
means it came back false. Amber means a model proposed something and cited
nothing. The moment a green pill means "nice" somewhere, it stops meaning
"checked" everywhere.

The rule is easier to state than to hold. Three places broke it before anyone
noticed, all of them plausible at the time:

| Broke it | Why it was wrong | Now |
|---|---|---|
| The focus ring | Put the changed colour on every control a keyboard user passes through, which is most of them | Ink — the highest-contrast neutral, unmistakable without spending the reserved signal |
| `::selection` | On the review panel, changed rows already wear that colour; a selection in it is genuinely ambiguous | A neutral ink wash |
| Input focus borders | Used the attention colour to mean "focused" | Neutral; the outline does the work |

One carve-out stands, stated rather than assumed: the drop target on the
first-run screen. That screen has no deck open, so it has no *changed* anything
for the colour to be confused with. Nowhere past it may do the same.

## Surfaces

Five levels, each a real surface rather than a shade to choose from: `ground`
(the room), `panel` (filmstrip, change set), `raised` (inputs, hovered rows),
`line`, `line-strong`.

They are warm graphite rather than black so that **white paper reads as paper**
and not as a light leak. The deck is the subject; the tool is the frame.

## Typography

One system stack, and that is a constraint rather than a preference. ADR-0008
requires the client to load with the network unplugged, which rules out a font
CDN, and bundling a webfont to imitate one the operating system already has is
weight for nothing.

Evidence is set in mono — part names, ids, counts, coordinates, timestamps:
anything a user might compare character by character. Figures are tabular
everywhere, because a count that reflows as it updates reads as an animation.

## The structural view

The canvas draws every object at the exact position and size the OOXML
specifies. It is **not a PowerPoint render**: no theme fill resolution, no
autofit, no line breaking that matches Word's, pictures shown as their frames,
and no text colour.

Two of those are worth defending rather than apologising for.

**Text colour is dropped deliberately.** This view resolves no fills, so a slide
with white text over a dark photograph would render white on white and vanish —
and an object that vanishes reads as *absent*, which is the one impression this
canvas must never give. Colour is real and editable, so it is shown where it can
be shown exactly: in the change row, as before and after.

**A shape with no geometry at all is omitted, not placed at the origin.** A box
in the top-left corner looks like a defect in the user's deck. (Since inherited
placeholder geometry now resolves from the layout and master, this is rare.)

The label in the corner says "structural view" and the tooltip lists what is not
reproduced. For the job this canvas actually does — showing *where* a change
landed and that nothing around it moved — exact boxes are better evidence than
an approximate render. It is bad at judging whether the result looks good, and
the interface never claims it is for that.

## Motion

Three durations — 120ms feedback, 220ms reveal, 420ms deliberate — and two
curves. More than three is not a language, it is a pile. Nothing bounces: a
bounce implies overshoot, and nothing in this product overshoots.

**One animation earns real investment: carrying the eye to what changed.** When
the change set says slide 12, motion connects the claim to the evidence —
filmstrip mark, to canvas, to the outlined region. Everything else stays at the
floor: state feedback, reveal and dismiss, focus.

Panels travel 4px, not 20. A panel that travels far enough to notice is telling
you about itself; one that travels 4px is telling you it arrived.

`prefers-reduced-motion` is honoured from the first commit rather than
retrofitted, and every animation resolves to its correct final state instantly
rather than being skipped.

**Exit animations are not used for popovers, alerts or list rows.** That is a
correctness rule, not a taste one. An `AnimatePresence` exit that failed to
complete left the export panel mounted at `opacity: 0` with its input still in
the tab order — a keyboard user could tab into a dialog that had been closed —
and the same shape left a dismissed error alert in the tree, where zero opacity
hides nothing at all from a screen reader.

The **slide canvas** had the same fault and the worst consequence of it. It used
`AnimatePresence mode="wait"`, which gates the incoming slide on the outgoing one
finishing. The outgoing one never finished, so the canvas stuck: the filmstrip
reached slide 10 while the canvas went on showing slide 1, permanently. On the
surface whose entire job is proving what did and did not change, a reviewer would
have been checking a change against the wrong slide.

Both were invisible to the component tests, which pass in jsdom because jsdom
completes the exit and the browser did not. The fix was to remove the animation
rather than repair it: it was carrying no information, and a popover that simply
goes is what every tool does and what the reader expects. Motion that does no
work and costs correctness is not a trade worth making. Entrances stay, because
they say *this arrived*.

**There is no `AnimatePresence` anywhere in the client**, and that is the rule
rather than the current state. A keyed remount plays an entrance; nothing needs
to wait for anything to leave.

## Progress is stages, never a percentage

The engine reports each transition as it happens and knows nothing about how
long the next one will take. A progress bar would therefore be interpolating,
and **fabricated confidence is precisely what this product exists to replace**.
So the apply narrates: writing the approved changes → changes written →
comparing every part against your original → verified.

## States designed first, not last

Empty, ingesting, applying, failed-closed, partially applied, blocked. In a
deck-processing product these are the common path.

The **refusal** state gets the most care. Users trained by other tools read a
refusal as breakage; here it is the guarantee working. The copy says the
original is untouched, repeats the engine's reason verbatim rather than
paraphrasing it into something friendlier and less true, and offers the
unaccounted-for slides as somewhere to look.

## What this deliberately is not

The standing design reference carries an Awwwards/agency register — curtain
footers, parallax depth, cinematic scroll, ambient gradient drift. Its
*discipline* is used throughout: purposeful motion, hierarchy, interaction
states, accessibility, restraint. Its *effects* are not, and that is not
timidity.

A product whose entire promise is *"I will not touch what you did not ask me to
touch"* cannot ship an interface that redecorates itself. The restraint is the
product argument, made visible. Slide-Wright should look expensive because it is
precisely designed, not because things move.
