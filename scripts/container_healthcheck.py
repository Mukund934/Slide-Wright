"""Is this container serving? Asked without a credential.

`/api/ping` is the only route that answers unauthenticated (ADR-0011), which is
exactly what a health check needs: it proves the process is up without handing
an orchestrator a token, and without reporting anybody's activity.

The subtlety is the Host header. A self-hosted deployment answers only to the
names it was configured with, and loopback is *not* silently added to that set --
so a probe addressed to `127.0.0.1` is refused with 421 forever. A health check
that can never pass is worse than no health check, because the orchestrator
restarts a container that was working.

So the probe connects to loopback, where the socket is, and addresses the
request to the first configured name, which is what the server will accept.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    port = os.environ.get("SLIDE_WRIGHT_PORT", "8787")
    names = [n.strip() for n in os.environ.get("SLIDE_WRIGHT_HOSTNAME", "").split(",")]
    configured = next((n for n in names if n), None)

    # In local mode there is no configured name and loopback is the whole
    # allowlist, so `localhost` is right there and wrong everywhere else.
    host = configured or "localhost"

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/ping", headers={"Host": host}
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            if response.status == 200:
                return 0
            print(f"ping returned {response.status}", file=sys.stderr)
            return 1
    except urllib.error.HTTPError as error:
        if error.code == 421:
            # Serving, and refusing us by name. That is a misconfiguration
            # worth saying out loud rather than a process that has died.
            print(
                f"the server is up but does not answer to {host!r}. "
                "SLIDE_WRIGHT_HOSTNAME and the name this probe uses disagree.",
                file=sys.stderr,
            )
        else:
            print(f"ping failed: HTTP {error.code}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"not serving: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
