# Running Slide-Wright on your own server

For a team that wants one shared instance instead of an install per laptop.

**Read this first.** Slide-Wright's promise is that your decks do not leave your
machines. Running it on a server does not change that — the server is *yours* —
but it does mean the decks now sit somewhere several people can reach, and the
controls that keep that narrow are the ones below. None of them is optional and
the process refuses to start without them (ADR-0011).

If you only want it on your own laptop, you do not need any of this. Install the
two wheels and run `slide-wright-app`; it binds loopback, asks for no password,
and cannot be configured to do anything else.

---

## What you need

- Somewhere to run a container, inside your network.
- A hostname your users will reach it by. It does not have to be public — an
  internal DNS name is the normal case.
- A directory of decks the server can read and write.

You do **not** need a database, object storage, an account system, or an
outbound internet connection. There is none of that and none is coming for this
mode.

## Start it

```bash
docker build -t slide-wright .

docker run -d --name slide-wright -p 8787:8787 \
  -e SLIDE_WRIGHT_MODE=self-hosted \
  -e SLIDE_WRIGHT_HOSTNAME=slidewright.internal.example \
  -e SLIDE_WRIGHT_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  -v /srv/decks:/decks \
  slide-wright
```

Then give your users the URL and the token.

## What each setting does, and why it is required

| Setting | Required | What it is for |
|---|---|---|
| `SLIDE_WRIGHT_MODE` | yes | `self-hosted`. Without it you get `local`, which binds loopback and is unreachable from anywhere else |
| `SLIDE_WRIGHT_HOSTNAME` | yes | The name(s) this server answers to, comma separated. A request addressed to any other name is refused with `421` |
| `SLIDE_WRIGHT_TOKEN` | yes | At least 32 characters. Sent by the client as `Authorization: Bearer` |
| `SLIDE_WRIGHT_BIND` | no | Interface to bind. `0.0.0.0` in the image |
| `SLIDE_WRIGHT_PORT` | no | Defaults to 8787 |
| `GEMINI_API_KEY` | no | Only for plain-language instructions. Every deterministic capability works without it |

**Why the hostname is required and cannot be guessed.** Binding to an interface
stops another machine reaching the socket; it does not stop a *web page*, and
CORS does not either. In a DNS rebinding attack a hostile site resolves its own
name to your server's address, and the browser then treats requests to it as
same-origin and never consults CORS. The Host header is what survives that,
because the browser still sends the name the page was loaded from. There is no
safe default for "which names are mine", so the server asks rather than guesses.

**Why the token is required.** A server reachable by anything other than the
machine it runs on is reachable by a scanner. The process will not start
unauthenticated, and there is no flag to make it.

## The volume is how this product works

Decks are opened **by path**, never uploaded. The container needs to see the
filesystem your decks are on, and it must be writable: the engine keeps every
version in a `.slidewright` folder beside each deck, which is what makes
*revert* reach a real earlier copy rather than an undo stack in memory.

A read-only mount will open decks and fail to save.

## What your users see

The first time they open the URL they are asked for the access token. It is kept
for that browser tab only and is forgotten when the tab closes — a shared token
that outlived the session would sit on the disk of every machine that ever
opened the deployment.

After that it is the same product: open a deck by path, audit it, propose
changes, approve them, apply, verify, revert, export.

## What this mode does not give you

Stated plainly, because the gaps are the kind people assume are covered:

- **No per-user identity.** One token, shared. The audit trail records versions,
  not who made them. If you need to know which analyst changed what, this mode
  is not it yet.
- **No access control between decks.** Anyone with the token can open any deck
  the container can read. Scope the mounted volume accordingly — that mount is
  the access control.
- **No concurrency protection.** Two people editing the same deck at the same
  time is not defended against. One person at a time per deck.
- **No rate limiting, quotas, or upload endpoint.** There is nothing to upload
  to; decks come from the volume.

All four are consequences of one shared token being the whole identity model,
which is deliberate. They become worth fixing when somebody asks for per-user
access, and not before.

## Checking it works

```bash
curl -H "Host: slidewright.internal.example" http://your-server:8787/api/ping
# {"ok":true,"auth":"required"}
```

`/api/ping` is the only route that answers without a token, which makes it the
right thing for a load balancer or orchestrator to probe: it proves the process
is serving without being handed a credential and without reporting anyone's
activity. The image's own `HEALTHCHECK` uses it.

For a fuller check, `scripts/assert_container_enforces.py` drives a running
deployment and asserts what it refuses — an unauthenticated caller, a valid
token addressed to the wrong name — and then runs the whole loop against a deck
on the volume.

## Upgrading

Rebuild the image and restart the container. Nothing on the volume changes:
versions live beside the decks and are read by whatever version of the engine
opens them next.

The client is served with `Cache-Control: no-cache`, so a user who reloads after
an upgrade gets the new one rather than the version they had.
