# SlideWrightCLEAN

**Change what I asked. Preserve everything else. Prove it.**

An AI presentation intelligence system for PowerPoint files that already exist and already matter.

---

## The problem

Every AI presentation tool is built to *create* decks. Almost every valuable deck already exists.

A board pack, a pitchbook, an IC memo, a QBR — these are inherited, not authored. Last quarter's deck is the starting point for this quarter's. A bank's pitchbook template encodes a decade of decisions about how that bank presents itself. A live pitchbook goes through **5–15 revision rounds**, each costing hours of expensive people's evenings.

And every AI tool treats an existing deck as an *import problem* — parse it, flatten it into the tool's own representation, re-emit it. That is lossy by construction. It is why market-leading exports damage a third of slides, and why enterprises rate the category poorly on template compliance.

The result is a specific absurdity: **the decks that matter most are the ones AI can help with least**, because the one thing their owners cannot tolerate is a tool that touches something they did not ask it to touch.

## What this is

SlideWrightCLEAN reads a real `.pptx`, understands its structure and meaning, changes exactly what was asked, and returns a genuinely editable PowerPoint — with a verifiable account of what changed and what did not.

Generation is one capability inside that system. It is not the point of it.

## Status

**Phase 0 — architecture and validation.** Not implemented. No product code yet, by design.

The engine capability underneath this has been empirically verified (see `docs/foundation/`), but **customer demand has not been tested at all**. The next step is a validation sprint with real professionals and real decks, not a build.

## Repository layout

```
docs/          Public engineering documentation — architecture, decisions, guides
src/           Implementation (empty until Phase 1; see docs/architecture/)
tests/         Test suites and the fidelity benchmark corpus
scripts/       Development and operational tooling
private/       Project intelligence — gitignored, never committed
```

`private/` is the project's persistent brain: roadmap, research, strategy, costs, customer findings, decision log. It is excluded from Git deliberately and permanently.

## Principles

1. **The user's file is not a disposable input.** It is their work. Preserve it.
2. **Verify deterministically.** Anything that can be checked with arithmetic must not be judged by a model.
3. **Fail closed.** Refuse to produce a deck rather than produce a quietly wrong one.
4. **Premium does not mean more.** The visible product should be simple; the machinery underneath can be deep.

## Licence

Not yet chosen — see `docs/decisions/`. Source-available and proprietary options are both open pending the commercial model.
