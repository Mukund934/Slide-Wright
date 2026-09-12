# 08 — Deployment models

```
Status:  analysis, not a decision
Date:    2026-09-12
Reads:   ADR-0004 (Supabase), ADR-0008 (the document does not leave the machine),
         ADR-0010 (the product surface is a local application)
```

This document exists because a reasonable objective — *ship a live product with a
deployed frontend and backend* — collides with two accepted decisions that
forbid exactly that, and the collision deserves to be resolved on evidence
rather than by whichever document was read last.

It is **not** a decision. It sets out what the current architecture actually
protects, what it would cost to host, which parts are reusable, and what the
smallest honest change would be. The decision itself is recorded as open.

---

## 1. What ADR-0008 and ADR-0010 protect

The promise is one sentence: **the document does not leave the machine.**

That is not a preference about hosting. It is a claim about a specific failure
mode. The material this engine edits — deal documents, board packs, IC memos —
is confidential by default, and organisations that handle it commonly run
data-loss-prevention controls that inspect and block uploads to unapproved
external services. Under those controls the failure is not *"the customer
declines to buy"*. It is *"the upload is blocked and the user is reported"*,
which no amount of product quality repairs.

### What enforces it in code, exhaustively

Eight things. Anyone proposing a hosted surface is proposing to change some of
them, and it is worth being precise about which.

| # | Mechanism | Where |
|---|---|---|
| 1 | Bound to `127.0.0.1`, with no flag to change it | `__main__.py` — `HOST` is a module constant and `--host` is deliberately absent |
| 2 | Answers only to `localhost`/`127.0.0.1`/`::1` by Host header, else `421` | `app.py` — `only_answer_to_loopback`, before every route |
| 3 | Decks are opened **by path**, never uploaded | `POST /api/documents` takes `{path}` |
| 4 | No authentication, because there is one trusted local user | `app.py` module docstring states this as a decision |
| 5 | No outbound call except the optional model provider | asserted by test |
| 6 | No telemetry, crash reporting or sample collection | ADR-0008 §4 — "no code that could send it anywhere" |
| 7 | The built client loads nothing from an external origin | CI step, `grep` over `dist/` |
| 8 | Durable state is version files on local disk beside the deck | `session.py`, `workspace.py` |

Three of these are also CI gates that fail the build: the loopback pin, the
absence of `--host`, and the absence of any `0.0.0.0` bind.

### What the evidence for the constraint actually is

ADR-0008 is honest about this and it should stay honest here. The argument has
two halves and they are not equally supported:

- **The prohibitive half** — *DLP controls block uploads of documents like these*
  — is second-hand and uncited. No practitioner has confirmed it. It is
  plausible and it is unproven.
- **The permissive half** — *these same organisations do license third-party
  PowerPoint add-ins that run on the analyst's machine* — is verifiable from
  public vendor pages, and is the stronger half.

**Zero practitioner conversations have happened.** The single question that
would settle this is already written down in `practitioner-outreach.md` §H, and
it is asked as a pair: could you use this if the deck had to be uploaded — and
would it change your answer if it ran entirely on your machine? The gap between
those two answers is the whole evidence base for ADR-0008.

Until someone answers it, local-first is the *defensible default*, not a proven
requirement. That distinction matters: a default can be revisited cheaply, and a
proven requirement cannot.

---

## 2. What is reusable, and what is not

This is the load-bearing finding of the analysis.

**The engine is deployment-agnostic and would need no change.** It is pure
Python over the filesystem: `inspect`, `gate`, `apply`, `verify`, `audit`,
`refresh`, `brand`, `diff`, `session`. It has no notion of a user, a request or
a tenant. CI already proves it stands alone by installing it with nothing else
and running its 880 tests. Whatever surface is built, this is the part that
does the work, and it is untouched by the question.

**The API is not reusable as-is**, and ADR-0010 says so in its own consequences
section. Three specific reasons, verified in the code:

- `Workspace.sessions` is a plain `dict` with **no locking** and no eviction.
  One process, one user, a handful of decks.
- The document id is a **SHA-256 of the resolved path**, so two users opening
  the same path would collide onto one session. That is a feature locally — a
  reload reconnects to its own document — and a tenancy bug hosted.
- There is **no authentication anywhere**, by design, and every route assumes
  the caller is entitled to every document.

**The client is reusable.** It talks to one origin over a typed service layer
and holds no local-only assumption beyond the path-shaped open field.

---

## 3. The job queue is not needed, and the reason it was is stale

ADR-0010 declines a job queue for the local surface and justifies SSE progress
with: *"Ingest and apply take minutes on a real deck."*

**That is no longer true, and the correction changes the hosted analysis.**
Measured today on this machine, over the third-party corpus, end to end —
`inspect` + `Session.open` + `apply` + verification:

| Deck | Slides | Size | inspect | open | apply + verify | **total** |
|---|---|---|---|---|---|---|
| `nasa-es6-exit` | 52 | 57.5 MB | 398 ms | 240 ms | 4,692 ms | **5.33 s** |
| `nasa-esm-annual-review` | 41 | 19.1 MB | 490 ms | 100 ms | 3,302 ms | **3.89 s** |
| `eia-aeo2023-release` | 25 | 3.7 MB | 337 ms | 90 ms | 1,654 ms | **2.08 s** |

The worst real deck available is **5.3 seconds**, not minutes. The roadmap's
separate end-to-end figure agrees: 400 slides and 2,400 corrections in 2.5 s.

Two consequences:

1. The SSE progress stream is now a **UX choice**, not a technical necessity.
   It stays — five seconds still deserves honest narration — but it is no
   longer load-bearing.
2. **A hosted deployment would not need a job queue** for this workload. Every
   figure above fits inside an ordinary HTTP request on every platform worth
   considering. ADR-0004's worker-and-queue shape was designed for a cost that
   has not been measured to exist.

That removes the single largest piece of infrastructure a hosted surface was
assumed to require.

---

## 4. Four deployment models

### A. Local application — *what exists*

The user installs two wheels and runs `slide-wright-app`. Nothing leaves the
machine.

**Preserves the promise:** completely.
**Missing for production:** nothing architectural. A licence and a release.
**Deployable today:** yes.

### B. Customer-hosted (their own infrastructure)

A container the customer runs inside their own network — a VPC, an internal
host, a workstation on their domain. "Hosted" in the sense of a server; **not**
hosted in the sense that matters to ADR-0008, because the document never
crosses the customer's trust boundary.

**Preserves the promise:** yes — and this is the important point. ADR-0008's
constraint is about *whose* machine, not about *whether there is a server*.
**Missing:** authentication, per-user session isolation, a configurable bind
address, a container image, and a way to turn the loopback pin off **only** in
this mode.
**Effort:** the smallest of the three non-local options.

### C. Vendor-hosted multi-tenant SaaS

The shape ADR-0004 originally assumed. Upload a deck, process it on our
servers, download the result.

**Preserves the promise:** no. It is the precise thing ADR-0008 rejects.
**Missing:** accounts, a database, tenant isolation with row-level policies,
object storage with signed URLs, an upload path (the API takes a path, not a
file), quotas and rate limits, a retention policy, a DPA, and a security
questionnaire answer. None of it exists, deliberately.
**Blocked by:** Phase 2b's gate — at least one paying customer — which is not
met, and by the absence of any practitioner saying they could use it.

### D. Browser-only (WebAssembly)

Already rejected in ADR-0008 and the rejection still holds: the guarantee is
byte-level and the engine is 174k lines of Python. Reimplementing it in WASM to
preserve that property is a large project justified by no current evidence.

### The hybrid that is usually proposed, and why it is worse

*"Keep sensitive decks local and send the rest to a hosted service."*

It fails on a practical point rather than a principled one: **the user has to
classify the deck, and they will get it wrong.** A promise that holds only when
someone correctly labelled a file in advance is not a promise a compliance
function can accept, and one mislabelled deck destroys the trust the whole
product is sold on. A guarantee with a user-operated exception is a guarantee
with a defect rate.

There is one hybrid that does work, because it splits on **data** rather than
on judgement: local processing plus a hosted surface that never receives a
document — licensing, updates, documentation, a public compatibility matrix.
That is model A plus a website, and it is covered below.

---

## 5. The minimum change for a real hosted deployment

Model **B** is the smallest architectural change that produces a genuinely
deployed frontend and backend without weakening the promise. Concretely:

1. **Make the bind address a deployment mode, not a constant.** One setting with
   two values. `local` — the default, and what happens when nothing is
   configured — keeps the loopback pin, the Host allowlist and no
   authentication. `self-hosted` binds a configured interface and **refuses to
   start** without authentication configured. The safety property becomes *the
   local mode cannot be configured away*, which is stronger than today's *no
   code may bind publicly*, because it survives the feature existing.
2. **Authentication, required in `self-hosted` and forbidden in `local`.** Not
   optional, not defaulted-on: absent means the process does not start.
3. **Per-principal session isolation.** `Workspace` keys documents by a hash of
   the path; it would key by `(principal, path)` and acquire a per-document lock
   around mutation. Small, local change.
4. **An upload path, additive.** `POST /api/documents` keeps taking a path for
   local use and gains a multipart form for hosted use. The engine is unchanged:
   it wants a file on a filesystem either way.
5. **No queue.** §3 measured why.
6. **A container image**, which is the actual deliverable of a hosted mode.

What this explicitly does **not** do: add a database, add object storage, add
accounts we manage, add billing, or add telemetry. In model B the customer's own
infrastructure supplies identity and storage, and the trust boundary never
moves.

**Cost of doing this now, honestly:** it is real work — probably a week — and it
is work no one has asked for. Phase 2b's gate exists for exactly this reason,
and the 33 prior studio campaigns died of building the next thing because it was
buildable.

---

## 6. What can be deployed publicly today without touching any of this

A **static site**: what the product does, the generated compatibility matrix,
the install instructions, the fidelity evidence. It receives no document, so
ADR-0008 does not apply to it. It is a genuine public deployment, it is useful
before the practitioner conversations rather than after, and it costs nothing.

It is also the honest answer to *"there should be something live"*: the thing
that can be live is the thing that does not handle customer files.

---

## 7. The decision, stated as open

**Unresolved:** whether Slide-Wright ships as a local application (A), gains a
customer-hosted mode (B), or reverses ADR-0008 for a vendor-hosted product (C).

**This is not an engineering decision.** It is a decision about who the customer
is and what they are permitted to do, and the evidence that would settle it is a
conversation that has not happened.

**What would choose each:**

| If a practitioner says | Then |
|---|---|
| "We could never upload a deal deck to an outside service" | **A**, and ADR-0008 is confirmed rather than assumed |
| "We'd need it on our own infrastructure, with SSO" | **B** — build §5 |
| "We upload files like this to outside services today" | **C** is open, and ADR-0008 should be revised with the citation |
| Nothing, because nobody was asked | **A**, because it is the only one that is true today |

Until then the status is: **A is built and shippable; B is specified and not
built; C is gated and correctly untouched.**

---

## 8. Corrections this analysis makes to existing documents

Recorded here rather than silently edited, because both are load-bearing claims
that other documents cite.

- **ADR-0010's "ingest and apply take minutes on a real deck" is wrong.**
  Measured worst case over the real corpus is 5.3 s. The SSE decision it
  justifies is still right on UX grounds; the queue it declines is now declined
  for a second and better reason.
- **ADR-0004's worker-and-queue shape is not required by the measured
  workload.** It remains the right shape for a hosted product that acquires one,
  which is a different claim from the one it currently makes.
