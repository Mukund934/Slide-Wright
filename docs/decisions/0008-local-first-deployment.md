# ADR-0008 — The document does not leave the machine

Status:  accepted
Date:    2026-09-06
Amends:  ADR-0004 (Supabase backend) — narrows when it applies, does not reverse it

## Context

ADR-0004 chose Supabase and, implicitly, a hosted web application as the first
delivery surface. That decision was made on engineering grounds — relational
data, row-level tenant isolation, bundled storage and auth — and those grounds
still hold for a hosted product.

What it did not weigh is whether the files this system exists to edit are allowed
to reach a hosted product at all.

The material this engine is built for is confidential by default: deal documents,
board packs, investment memos. Organisations that handle such material commonly
run data-loss-prevention controls that inspect and block uploads of documents to
unapproved external services, independently of whether the vendor is trustworthy.
Under those controls the failure is not "the customer declines to buy" — it is
"the upload is blocked and the user is reported", which no amount of product
quality repairs.

The countervailing fact matters just as much: these same organisations *do* buy
third-party PowerPoint software from small vendors. Established add-ins are
licensed per user, installed into Office, and run on the analyst's machine. So
the constraint is not a prohibition on outside software. It is narrower and more
actionable:

> **The document must not leave the machine.**

That distinction is the whole decision. It rules out one architecture and
explicitly permits another that is already proven in the same environments.

## Decision

**Local execution is the primary delivery model. A hosted surface is a later
option, not the first one.**

1. **Every deterministic capability must run entirely locally**, with no network
   access required: ingest, gate, apply, verify, audit, refresh, brand, diff,
   session history and revert. This is already true and is now a constraint
   rather than an accident. A test asserts it.

2. **Model calls are the only network boundary**, they are optional, and the
   system is fully functional without them. With no key configured the planner
   falls back to an offline stub and every guarantee above is unaffected.

3. **A deployment that cannot make model calls must remain useful.** If an
   environment forbids all outbound traffic, the deterministic engine is the
   product. That is a supported configuration, not a degraded one.

4. **Nothing is uploaded implicitly.** No telemetry, no crash reporting, no
   sample collection, no "help us improve" pathway. A tool trusted with a live
   deal document earns that trust by having no code that could send it anywhere.

5. **ADR-0004 stands for the hosted surface**, if and when one is built. Supabase
   remains the right choice for that shape. It was already gated on a paying
   customer and remains so.

## Consequences

- The command line stops being scaffolding for a web application and becomes a
  legitimate delivery surface in its own right. Work on it is not throwaway.
- Session workspaces, version history and revert already live on local disk,
  which is now correct by design rather than by convenience.
- Any future feature must answer "does this work with the network unplugged?"
  before it is designed. Where the answer is no, that must be a deliberate,
  recorded exception.
- The eventual desktop surface will need code signing and an installer, which is
  real work that a web application would not have needed. Accepted.
- We give up the operational conveniences of a server — central logging,
  instant updates, server-side measurement of usage. Cost accepted; those
  conveniences are precisely what the constraint forbids.

## Alternatives

| Option | Rejected because |
|---|---|
| Hosted multi-tenant SaaS first | The upload is the failure point. No isolation guarantee helps when a control blocks the file before it reaches us |
| Private cloud / VPC per customer first | Plausible for large organisations later, but it is a per-customer engagement, not a product. Wrong shape to start with |
| Browser-only, processing entirely client-side | Attractive, and worth revisiting. But the engine is Python and the guarantee is byte-level; reimplementing it in WebAssembly to preserve that property is a large project justified by no current evidence |
| Ignore the constraint and sell to whoever has no such controls | Possible, but it selects for customers whose documents matter least — the opposite of the material this engine was built to protect |

## What would reverse this

Direct evidence from practitioners that a hosted service is genuinely usable on
real documents in their environment — not that they would like it to be. Until
someone says "yes, we upload files like this to outside services today", local
execution is the honest default.

The supporting evidence for the constraint is currently second-hand and
uncited; the *permissive* half (that installed third-party add-ins are routinely
licensed in these environments) is verifiable from public vendor pages, and is
the stronger half. This ADR rests mainly on that.
