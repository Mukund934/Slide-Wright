# Changelog

What changed between releases, and why it mattered. Entries are written for
somebody deciding whether to upgrade, not for somebody reconstructing the
commit history — the history is in the repository and is deliberately fine
grained.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are the two distributions together: `slide-wright-engine` and
`slide-wright-api` are released at the same version, because the API imports
the engine directly (ADR-0010) and a mismatched pair has never been tested.

## [Unreleased]

Nothing is released yet. The first tag is waiting on a licence — the repository
is public with none, which means nobody may legally use what it publishes. See
`docs/decisions/README.md`.

### Added

- **A mode that can actually be deployed.** The bind address was a constant
  guarded by three CI greps, which protected the promise by making a legitimate
  server impossible to express — so the first time one was needed, the only move
  was to delete the guard. It is now an explicit deployment mode. `local` stays
  the default and became *stronger*: it refuses a bind address rather than
  ignoring one, so it cannot be configured on to a public interface at all.
  `self-hosted` requires a token and the hostnames it answers to, and refuses to
  start without either. See ADR-0011 and `docs/guides/self-hosting.md`.
- **A container image**, built and driven in CI, with a health check that
  addresses the server by a name it will actually answer to.
- **A token screen** for shared deployments, which verifies the token against a
  real request before accepting it rather than storing it and letting the next
  call fail.
- **The wheel carries the interface.** `pip install` used to produce an API and
  no client: the app looked for it at a path that only resolves inside a source
  checkout, so an installed copy served `/api` and reported "the client is not
  built" — naming a build step the user could not run. The client is now copied
  into the package at build time and found there first.
- **A way to put a deck down.** `DELETE /api/documents/{id}` has been a route
  throughout and nothing in the workspace reached it, so opening the wrong deck
  was a dead end: no control closed it, and a reload restored it from the
  remembered id. There is now an `open another` control in the header.
- **Release artifacts.** A tag builds both distributions, checks the interface
  is inside the wheel, installs them into a clean environment and runs the core
  workflow against them before publishing.
- **What a file discloses.** A new `DISCLOSURE` audit area reports contact
  addresses and directory ids in `ppt/authors.xml`, co-authoring history in
  `ppt/changesInfos/`, and external links pointing at a path on somebody's
  machine. Reported, never corrected: removing a part is a change to the
  package that verification exists to refuse.

### Changed

- **Tests run on Windows and macOS.** The engine opens zip archives, writes
  beside the user's deck and holds file handles across a save. All three behave
  differently on Windows and nothing had ever run there.
- **Engine-only installs no longer break the suite.** `pip install -e
  "src/engine[dev]"` followed by `pytest tests` ended in a collection error,
  because `tests/product` imports fastapi. The product tests now skip with a
  reason that names the fix.
- **The README documents the install that exists.** Its first two commands
  failed, and the workspace section asked the user to install Node and run a
  Vite build that the wheel makes unnecessary.

### Fixed

- **Apply was the one route that would have failed on a shared deployment.**
  `applyStreaming` reads a Server-Sent Events body, so it cannot go through the
  shared request helper — and it built its own headers, without the token. The
  operation the whole product exists to perform would have returned 401 while
  everything around it worked.
- An empty `dist/` satisfied the client's presence check, mounting a static
  handler over nothing and serving 404s from the product's own root. Presence
  is now decided by `index.html`.
- The `Comments` row of the compatibility matrix matched both the legacy and
  the 2021 comment schemas, counting two different constructs as one.
