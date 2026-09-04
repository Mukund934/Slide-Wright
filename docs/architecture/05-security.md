# Security and Privacy

**Status:** Proposed · **Updated:** 2026-09-04

## The threat that matters

Customers upload **live M&A pitchbooks, board packs and IC memos**. A single cross-tenant leak is not a bug — it is the end of the company, and plausibly a client's regulatory problem.

Confidentiality is therefore a **day-one architectural constraint**, not a hardening pass. Retrofitting isolation into a system that assumed trust does not work.

## Isolation

**Row Level Security is the primary mechanism.** Every tenant-scoped table carries `org_id`, and RLS policies enforce access **in the database**. An application bug cannot leak another customer's deck, because the application is not the thing enforcing it.

That is the main reason for choosing Postgres/Supabase over a document store (`04-infrastructure-and-data.md`).

| Layer | Control |
|---|---|
| Database | RLS on every tenant table; policies deny by default |
| Storage | Private buckets; **no public objects, ever**; short-lived signed URLs |
| Worker | One job processes one tenant's deck; scratch space wiped on completion |
| API | Org membership checked per request, independently of RLS |
| Logs | Never log deck content, slide text, or extracted values |

## Data lifecycle

| Stage | Control |
|---|---|
| Upload | Signed, size-capped, MIME and OOXML-structure validated before processing |
| At rest | Encrypted; original immutable and hashed |
| In transit | TLS everywhere |
| Processing | In-memory and ephemeral scratch; wiped after the job |
| Retention | **Short by default**, configurable per org; deletion actually deletes |
| Deletion | Hard delete of blobs; audit record of the deletion retained |

**Default retention should be short and stated plainly.** *"We delete your deck after N days and never train on it"* is both correct practice and a sales asset in this market. The value of N is a business decision (see private `operations/human-actions.md`, DQ/H-15).

## Model providers — the non-obvious exposure

Deck content is sent to a third-party model API. That is the largest privacy surface and it is easy to overlook.

**Requirements:**
1. **Zero-retention endpoints** where available. Verify in writing; do not infer from a marketing page.
2. **No training on customer data.** Contractual, not assumed.
3. **The provider abstraction is a security control**, not only an engineering one — it is what makes moving providers possible if terms change (R8).
4. **Minimise what is sent.** The model needs the IR of the slices it is working on, not the whole deck, and never the raw file.

Point 4 is worth stating clearly: the model does not need `ppt/media/` images, the embedded workbook, or slides outside the change scope. Sending less is both cheaper and safer.

## File-format threats

`.pptx` is a ZIP archive, which brings a specific threat class that generic upload validation misses:

| Threat | Control |
|---|---|
| Zip bomb | Uncompressed-size and entry-count caps before extraction |
| Path traversal in entry names | Reject absolute paths and `..` segments |
| XXE / external entities in OOXML | XML parsing with external entities disabled |
| Malicious embedded objects, macros (`.pptm`) | Reject macro-enabled formats; do not execute embedded content |
| Excessive part counts | Cap and reject |

The ingest path is the only component that touches untrusted bytes, and it should be treated as the hostile boundary.

## Access control

Roles: `owner`, `admin`, `member`. Deliberately minimal — invented granularity is complexity without a customer asking for it. Enterprise SSO and finer roles arrive when an enterprise asks, and the model is designed to extend.

## Audit

`audit_events` is **append-only**. Every deck upload, job run, export, permission change and deletion is recorded with actor, action, target and timestamp.

Enterprises will ask for this. It is also how we investigate our own incidents.

## Deliberately not yet

| Item | When |
|---|---|
| SOC 2 | When an enterprise deal requires it. Architecture supports the evidence; the certification is expensive and premature. |
| On-prem / VPC deployment | If AI-usage policies forbid cloud processing (R9, Q3). **The engine already runs locally, so this is a genuine answer** rather than a hypothetical. |
| Customer-managed keys | Enterprise ask |
| Pen test | Before general availability |

## The Phase 0 position

During validation, decks are processed **locally on Mukund's machine**. Nothing is uploaded anywhere. That is simultaneously the simplest implementation and the strongest possible privacy posture — and it is worth saying to early testers explicitly, because *"it never leaves my laptop"* removes the largest objection to handing over a live deal document.
