"""Fail if the local deployment can be talked out of being local.

    python scripts/assert_local_mode_is_sealed.py

This replaces three CI greps that asserted a module constant still read
`HOST = "127.0.0.1"` and that no line anywhere bound `0.0.0.0`. Those were real
safeguards with a specific weakness: they protected the promise by making one
deployment impossible to *express*, so the first time somebody legitimately
needed a server, the only available move was to delete the guard.

ADR-0011 made the mode explicit instead, which means the guard has to become
stronger rather than disappear. It no longer asks "does this text still appear
in this file". It runs the resolver and asserts the properties that matter:

  · nothing configured is local, on loopback, with no token;
  · local refuses to be pointed anywhere else, rather than ignoring the attempt;
  · self-hosted will not start without authentication, or with a weak token,
    or without naming the hosts it answers to;
  · the loopback names are not something a configuration can add to.

A grep can be satisfied by a comment. This cannot.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "product" / "api"))
sys.path.insert(0, str(REPO / "src" / "engine"))

from slide_wright_api.deployment import (  # noqa: E402
    LOOPBACK_BIND,
    LOOPBACK_HOSTS,
    DeploymentError,
    Mode,
    generate_token,
    resolve,
)

MAIN = REPO / "src" / "product" / "api" / "slide_wright_api" / "__main__.py"
GOOD_TOKEN = generate_token()

failures: list[str] = []


def must(condition: bool, what: str, detail: str = "") -> None:
    if condition:
        print(f"  ok    {what}")
        return
    failures.append(what)
    print(f"  FAIL  {what}{'  -- ' + detail if detail else ''}")


def must_refuse(env: dict[str, str], what: str) -> None:
    try:
        got = resolve(env)
    except DeploymentError:
        print(f"  ok    {what}")
        return
    failures.append(what)
    print(f"  FAIL  {what}  -- it started anyway: mode={got.mode.value} bind={got.bind}")


def main() -> int:
    print("Local mode is sealed?")

    default = resolve({})
    must(default.mode is Mode.LOCAL, "nothing configured means local mode")
    must(default.bind == LOOPBACK_BIND, "local binds loopback", default.bind)
    must(default.token is None, "local has no token to check")
    must(default.answers_to == LOOPBACK_HOSTS, "local answers only to loopback names")

    print("\nLocal refuses to be moved:")
    must_refuse({"SLIDE_WRIGHT_BIND": "0.0.0.0"}, "a bind address is refused, not ignored")
    must_refuse({"SLIDE_WRIGHT_BIND": "127.0.0.1"}, "even a harmless bind is refused")
    must_refuse({"SLIDE_WRIGHT_HOSTNAME": "deck.example"}, "an extra hostname is refused")
    must_refuse({"SLIDE_WRIGHT_TOKEN": GOOD_TOKEN}, "a token is refused")

    print("\nSelf-hosted fails closed:")
    must_refuse({"SLIDE_WRIGHT_MODE": "self-hosted"}, "no token: refuses to start")
    must_refuse(
        {"SLIDE_WRIGHT_MODE": "self-hosted", "SLIDE_WRIGHT_TOKEN": "short"},
        "a weak token: refuses to start",
    )
    must_refuse(
        {"SLIDE_WRIGHT_MODE": "self-hosted", "SLIDE_WRIGHT_TOKEN": GOOD_TOKEN},
        "no hostname: refuses to start",
    )
    must_refuse({"SLIDE_WRIGHT_MODE": "public"}, "an invented mode: refuses to start")

    print("\nSelf-hosted, configured properly:")
    hosted = resolve({
        "SLIDE_WRIGHT_MODE": "self-hosted",
        "SLIDE_WRIGHT_TOKEN": GOOD_TOKEN,
        "SLIDE_WRIGHT_HOSTNAME": "deck.internal.example",
    })
    must(hosted.requires_auth, "it authenticates")
    must(
        hosted.answers_to == frozenset({"deck.internal.example"}),
        "it answers only to the names it was given",
        str(sorted(hosted.answers_to)),
    )
    must(
        "localhost" not in hosted.answers_to,
        "loopback is not silently added to a hosted deployment",
    )

    print("\nThe flag that must not exist:")
    main_py = MAIN.read_text(encoding="utf-8")
    must(
        "add_argument(\"--host" not in main_py and "add_argument('--host" not in main_py,
        "there is no --host flag",
    )

    print()
    if failures:
        print(f"FAILED  {len(failures)} of the local guarantees do not hold:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("local mode is sealed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
