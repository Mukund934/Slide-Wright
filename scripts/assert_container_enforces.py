"""Drive a running self-hosted deployment and check what it actually refuses.

    python scripts/assert_container_enforces.py

Reads `SW_TOKEN` and talks to `127.0.0.1:8787`, addressing every request to
`slidewright.ci` -- the hostname the container under test was configured with.

This is the counterpart to `release_smoke_test.py`. That one proves the *wheels*
work by installing them into an empty environment and driving the loop. This one
proves the *deployment mode* works by driving a container and checking the three
things a hosted deployment must get right, none of which the local product has
any code path for:

  · an unauthenticated caller is refused, on every route but `/api/ping`;
  · a caller with a valid token addressed to another name is still refused,
    because the rebinding guard is not an authentication fallback;
  · a caller with a valid token, correctly addressed, gets the whole loop --
    open a deck from the mounted volume, propose, approve, apply, verify.

The last one matters most. A deployment that authenticates and then cannot edit
a deck is a login screen, not a product.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# Overridable so this can be run against a deployment that is not the CI
# container -- it was written by pointing it at a self-hosted process on a
# laptop, which is the only way to know the script itself works.
BASE = os.environ.get("SW_BASE", "http://127.0.0.1:8787")
HOST = os.environ.get("SW_HOSTNAME", "slidewright.ci")
TOKEN = os.environ.get("SW_TOKEN", "")
DECK = os.environ.get("SW_DECK", "/decks/ci.pptx")

results: list[bool] = []


def say(ok: bool, what: str, detail: str = "") -> bool:
    print(f"  {'ok  ' if ok else 'FAIL'}  {what}" + (f"  -- {detail}" if detail else ""))
    results.append(ok)
    return ok


def call(
    path: str, *, method: str = "GET", body: dict | None = None,
    token: str | None = None, host: str = HOST,
) -> tuple[int, dict]:
    request = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Host": host,
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        # The body is the whole diagnostic. This runs in CI, where nobody can
        # attach a debugger to the container -- a bare status code turns a
        # permissions problem and a routing problem into the same 500.
        body = error.read().decode("utf-8", "replace")[:400]
        try:
            return error.code, json.loads(body)
        except ValueError:
            return error.code, {"detail": body}
    except OSError as error:
        print(f"        transport failure: {error}", file=sys.stderr)
        return 0, {}


def main() -> int:
    if not TOKEN:
        print("SW_TOKEN is not set; there is nothing to authenticate with.")
        return 2

    print("A self-hosted container, asked what it allows")

    status, answer = call("/api/ping")
    say(status == 200 and answer.get("auth") == "required",
        "/api/ping answers unauthenticated and says a token is needed",
        f"HTTP {status} {answer}")

    say(call("/api/health")[0] == 401, "/api/health without a token is refused")
    say(call("/api/health", token="not-the-token")[0] == 401,
        "a wrong token is refused")
    say(call("/api/documents", method="POST", body={"path": DECK})[0] == 401,
        "opening a deck without a token is refused")

    say(call("/api/health", token=TOKEN, host="evil.example")[0] == 421,
        "a valid token does not excuse the wrong Host")

    status, health = call("/api/health", token=TOKEN)
    say(status == 200 and health.get("ok") is True,
        "the right token, correctly addressed, gets through",
        f"engine {health.get('engine')}")

    # The loop, over a deck on the mounted volume. A deployment that
    # authenticates and cannot edit a deck is a login screen.
    status, document = call("/api/documents", method="POST", body={"path": DECK},
                            token=TOKEN)
    if not say(status == 200 and "id" in document,
               "a deck opens from the mounted volume",
               f"HTTP {status} {document.get('detail', document)}"):
        return 1

    doc_id = document["id"]
    slides = document["deck"]["slides"]
    say(bool(slides), "the deck reads back", f"{len(slides)} slide(s)")

    target = slide_number = None
    for slide in slides:
        for shape in slide["shapes"]:
            if shape.get("text", "").strip():
                target, slide_number = shape, slide["number"]
                break
        if target:
            break
    if target is None:
        return 1 if not say(False, "found a shape with text to edit") else 0

    status, proposed = call(
        f"/api/documents/{doc_id}/propose", method="POST", token=TOKEN,
        body={"sets": [{"slide": slide_number, "target": target["id"],
                        "op": "set_text", "after": "Container proof"}]},
    )
    say(status == 200 and proposed.get("proposed_count") == 1, "propose a typed edit",
        f"HTTP {status} {proposed.get('detail', '')}")

    status, reviewed = call(f"/api/documents/{doc_id}/review", method="POST",
                            token=TOKEN, body={"approve_all": True})
    say(status == 200 and reviewed.get("approved_count") == 1, "approve it")

    status, verification = call(f"/api/documents/{doc_id}/apply", method="POST",
                                token=TOKEN, body={})
    say(status == 200 and verification.get("deliverable") is not False,
        "apply and verify",
        f"HTTP {status} deliverable={verification.get('deliverable')} "
        f"{verification.get('detail', '')}")

    # The version the engine wrote lives on the volume, which is the customer's
    # filesystem. If this is empty the container edited something it invented.
    status, history = call(f"/api/documents/{doc_id}/history", token=TOKEN)
    say(status == 200 and len(history if isinstance(history, list) else []) >= 1,
        "a version was written to the mounted volume")

    print()
    failed = results.count(False)
    if failed:
        print(f"CONTAINER CHECK FAILED  {failed} of {len(results)}")
        return 1
    print(f"CONTAINER CHECK PASSED  {len(results)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
