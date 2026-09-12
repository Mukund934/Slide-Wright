# ADR-0011 — The bind address is a deployment mode, not a constant

Status:  accepted
Date:    2026-09-12
Amends:  ADR-0010 (the product surface is a local application) — makes its
         loopback rule a property of a named mode rather than a hardcoded value
Follows: ADR-0008 (the document does not leave the machine)
Reads:   docs/architecture/08-deployment-models.md

## Context

ADR-0010 pinned the API to `127.0.0.1` and said, correctly, that a product whose
governing constraint is that the file does not leave the machine must not ship a
flag that lets it. That was implemented as a module constant plus three CI greps:
`HOST = "127.0.0.1"` must still be present, `--host` must still be absent, and
nothing may bind `0.0.0.0`.

Those are real safeguards. They have one specific weakness, and it is structural
rather than a matter of degree:

> They protect the promise by making a legitimate deployment **impossible to
> express**. So the first time a customer needs the product on their own
> infrastructure, the only available move is to delete the safeguard.

A constant cannot distinguish *"this must never be public"* from *"this is the
local mode, and the local mode must never be public"*. Only the second is
actually true — `08-deployment-models.md` §4B establishes that a server the
customer runs inside their own network does not violate ADR-0008 at all, because
the trust boundary does not move. The document still does not leave *their*
machines, and their infrastructure is theirs.

There is also a second, quieter problem. The guards were greps. A grep is
satisfied by a comment, matches text rather than behaviour, and says nothing
about what the program does when an operator sets an environment variable it has
never heard of.

## Decision

**The deployment is resolved once at startup from the environment, into one of
two named modes, and the local mode is sealed.**

1. **`local` is the default and is what happens when nothing is configured.**
   Loopback bind, the loopback Host-header allowlist, no authentication, no
   accounts. Byte-for-byte the behaviour ADR-0008 and ADR-0010 describe.

2. **`local` refuses configuration rather than ignoring it.** Setting
   `SLIDE_WRIGHT_BIND`, `SLIDE_WRIGHT_HOSTNAME` or `SLIDE_WRIGHT_TOKEN` while in
   local mode stops the process with an error naming the mode to change instead.
   A setting that is silently discarded teaches an operator that the control is
   not there; a refusal teaches them where it is.

3. **`self-hosted` fails closed, three ways.** It will not start without a token,
   without a token of at least 32 characters, or without being told the hostnames
   it answers to. There is no warn-and-continue path: a misconfiguration of this
   product exposes confidential documents, and a warning in a log is not a
   control.

4. **The Host-header check is unchanged in mechanism** and now reads its
   acceptable names from the deployment. In local mode that set is the loopback
   names and nothing can add to it. The reason is the one ADR-0010 gives: a DNS
   rebinding attack cannot forge the Host header, because the browser still sends
   the name the page was loaded from.

5. **Authentication is a shared bearer token, compared with `compare_digest`.**
   One token, therefore one principal, therefore no tenancy, no accounts and no
   database — which is the point. This is a team running the product on their own
   infrastructure, not a vendor-hosted service. Per-user identity is a later
   decision and is not needed to make this one honest.

6. **`/api/ping` is unauthenticated and says only whether a token is needed.**
   A client that must decide whether to ask for credentials cannot be required to
   present them first. `/api/health` reports the engine version and how many
   documents are open — a description of somebody's activity — so it stays behind
   the token.

7. **The built client is served unauthenticated.** It is a script, a stylesheet
   and a page with an empty div; it contains no deck, no deck name and no
   workspace path, and every route returning any of those is behind the check.
   Serving it is what gives the operator's users somewhere to enter the token.

8. **The guard becomes a program, not a grep.**
   `scripts/assert_local_mode_is_sealed.py` runs the resolver and asserts the
   properties: nothing configured is local on loopback with no token, local
   refuses to be moved, self-hosted will not start unauthenticated, and loopback
   is never silently added to a hosted deployment. `tests/product/test_deployment.py`
   asserts the same properties in the suite, and additionally drives a hosted app
   through a client to check what it actually refuses. Two layers on purpose: the
   script is runnable in CI without the test dependencies, and the tests fail on
   a developer's machine before anything is pushed.

## Consequences

- **The local guarantee is stronger than it was.** It went from *no code in this
  repository may bind a public interface* — which survives only while the feature
  does not exist — to *the local mode cannot be configured on to one, at all*,
  which survives the feature existing.
- **`self-hosted` is specified and reachable, and remains unbuilt as a product.**
  There is no container image, no upload path and no per-user identity in this
  ADR. It makes the architecture capable of a deployment; it does not claim one
  has happened.
- **No module-level `app`.** There was one, and it resolved the deployment at
  *import* time, so a misconfiguration raised a traceback out of an import before
  the entry point could print the sentence that fixes it. `--reload` uses
  uvicorn's factory instead.
- **CORS now depends on the mode.** The dev server's origin is permitted in local
  mode and nowhere else: on a deployment other people can reach, allowing
  `http://localhost:5173` means a page served from any developer's machine can
  drive somebody's editor.
- **This ADR does not decide which model Slide-Wright ships.** That decision is
  recorded as open in `08-deployment-models.md` §7 and needs practitioner
  evidence, not more architecture.

## Alternatives

| Option | Rejected because |
|---|---|
| Keep the constant and the greps | Protects the promise by making a legitimate deployment inexpressible. The guard is deleted exactly when it is first tested |
| A `--host` flag | Invites `--host 0.0.0.0` typed into a terminal to see what happens. An environment that must also name its hosts and carry a token does not |
| Default to `self-hosted` when a token is present | Infers intent from a side effect. The mode must be stated, because getting it wrong exposes documents |
| Warn and fall back to loopback on a bad hosted config | A warning is not a control, and the operator believes they have a working deployment |
| Build per-user accounts now | Nobody has asked, no customer exists, and one shared token is the honest minimum for a team's own server. Phase 2b's gate exists for this |
