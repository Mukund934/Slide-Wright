"""Run the product's core workflow against an installed copy.

    python -m venv smoke
    smoke/bin/pip install release/*.whl
    smoke/bin/python scripts/release_smoke_test.py

Everything else in this repository is tested from the source checkout. That is
the one environment where a packaging defect cannot reproduce, and it is how
`pip install` came to ship an API with no interface while the whole suite
stayed green. So this is deliberately the opposite: it imports nothing from the
tree, starts the real server over a real socket, and drives the loop a user
drives -- open a deck, propose an edit, approve it, apply it, read the
verification.

Standard library only, on purpose. A smoke test that needs its own dependencies
is testing an environment nobody ships.
"""

from __future__ import annotations

import json
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"


def _say(ok: bool, what: str, detail: str = "") -> bool:
    mark = "ok  " if ok else "FAIL"
    print(f"  {mark}  {what}" + (f"  -- {detail}" if detail else ""))
    return ok


def _get(path: str, host: str | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(BASE + path)
    if host:
        request.add_header("Host", host)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _post(path: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, {"detail": error.read().decode()[:400]}


def _wait_until_listening(seconds: float = 40.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", PORT)) == 0:
                return True
        time.sleep(0.25)
    return False


def main() -> int:
    import slide_wright
    import slide_wright_api
    from slide_wright_api.app import CLIENT, create_app

    print("Slide-Wright release smoke test")
    print(f"  python    {sys.version.split()[0]}")
    print(f"  engine    {slide_wright.__version__}")
    print(f"  api       {slide_wright_api.__version__}")
    print()

    results: list[bool] = []
    installed = Path(slide_wright_api.__file__).resolve()

    results.append(_say(
        "site-packages" in str(installed),
        "running from an installed copy, not a checkout",
        str(installed.parent),
    ))
    results.append(_say(
        CLIENT is not None and (CLIENT / "index.html").is_file(),
        "the installed package carries the client",
        str(CLIENT),
    ))

    # A deck, made by the installed engine.
    from slide_wright.corpus.synthesize import build_minimal

    work = Path(tempfile.mkdtemp(prefix="slide-wright-smoke-"))
    deck = build_minimal(work / "smoke.pptx")
    results.append(_say(
        deck.is_file() and deck.stat().st_size > 0,
        "the engine builds a deck",
        f"{deck.stat().st_size} bytes",
    ))

    # The real server, over a real socket.
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(
        create_app(), host="127.0.0.1", port=PORT, log_level="warning",
    ))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    if not _wait_until_listening():
        _say(False, "the server started listening")
        return 1

    try:
        status, body = _get("/api/health")
        health = json.loads(body)
        results.append(_say(
            status == 200 and health.get("ok") is True,
            "GET /api/health",
            f"engine {health.get('engine')}",
        ))

        status, page = _get("/")
        html = page.decode("utf-8", "replace")
        results.append(_say(
            status == 200 and 'id="root"' in html,
            "GET / serves the interface",
            f"{len(page)} bytes",
        ))

        # An index.html that loads and a page that works are different things.
        assets = re.findall(r'(?:src|href)="(/[^"]+)"', html)
        served = [a for a in assets if _get(a)[0] == 200]
        results.append(_say(
            bool(assets) and len(served) == len(assets),
            "every asset the page loads is served",
            f"{len(served)}/{len(assets)}",
        ))

        status, document = _post("/api/documents", {"path": str(deck)})
        results.append(_say(
            status == 200 and "id" in document,
            "open a deck by path",
            str(document.get("detail", ""))[:80],
        ))
        if status != 200:
            return 1

        doc_id = document["id"]
        slides = document["deck"]["slides"]
        results.append(_say(bool(slides), "the deck reads back",
                            f"{len(slides)} slide(s)"))

        target = slide_number = None
        for slide in slides:
            for shape in slide["shapes"]:
                if shape.get("text", "").strip():
                    target, slide_number = shape, slide["number"]
                    break
            if target:
                break
        if target is None:
            _say(False, "found a shape with text to edit")
            return 1

        status, proposed = _post(f"/api/documents/{doc_id}/propose", {
            "sets": [{
                "slide": slide_number,
                "target": target["id"],
                "op": "set_text",
                "after": "Release smoke test",
            }],
        })
        results.append(_say(
            status == 200 and proposed.get("proposed_count") == 1,
            "propose a typed edit",
            f"before={target['text'][:40]!r}",
        ))

        status, reviewed = _post(
            f"/api/documents/{doc_id}/review", {"approve_all": True}
        )
        results.append(_say(
            status == 200 and reviewed.get("approved_count") == 1,
            "approve it",
        ))

        status, verification = _post(f"/api/documents/{doc_id}/apply", {})
        results.append(_say(
            status == 200 and verification.get("deliverable") is not False,
            "apply and verify",
            f"deliverable={verification.get('deliverable')}",
        ))

        status, after = _get(f"/api/documents/{doc_id}/deck")
        deck_after = json.loads(after) if status == 200 else {}
        changed = any(
            shape.get("text") == "Release smoke test"
            for slide in deck_after.get("slides", [])
            for shape in slide["shapes"]
        )
        results.append(_say(changed, "the edit is in the deck"))

        # The promise, checked: a page that resolved a name to 127.0.0.1 still
        # sends the name it was loaded from, and must not reach a route.
        status, _ = _get("/api/health", host="evil.example")
        results.append(_say(
            status == 421,
            "a request addressed to another name is refused",
            f"HTTP {status}",
        ))

    finally:
        server.should_exit = True
        thread.join(timeout=10)

    print()
    failed = results.count(False)
    if failed:
        print(f"SMOKE TEST FAILED  {failed} of {len(results)} checks")
        return 1
    print(f"SMOKE TEST PASSED  {len(results)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
