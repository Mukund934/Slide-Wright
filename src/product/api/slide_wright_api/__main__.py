"""Run the app.

    python -m slide_wright_api

With nothing configured this is the local product: loopback, no accounts, and
the document never leaves the machine (ADR-0008).

`--host` is still deliberately absent, and now for a sharper reason than
before. The bind address is not a flag because it is not a per-run decision --
it is a property of the deployment, resolved from the environment once at
startup (ADR-0011). A flag invites somebody to type `--host 0.0.0.0` into a
terminal to see what happens; an environment that must also name the hosts it
answers to and carry a token does not.

To run a server other people reach, read
`docs/architecture/08-deployment-models.md` and then set
`SLIDE_WRIGHT_MODE=self-hosted`.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser

import uvicorn

from slide_wright_api.app import CLIENT, create_app
from slide_wright_api.deployment import DEFAULT_PORT, DeploymentError, resolve

#: Kept because the CI guard and ADR-0010 both name it, and because it is still
#: true: this is the only interface the local mode will ever bind.
HOST = "127.0.0.1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="slide-wright-app",
        description="Slide-Wright. Local by default; the document stays put.",
    )
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument(
        "--no-browser", action="store_true", help="do not open a browser window"
    )
    parser.add_argument(
        "--reload", action="store_true", help="restart on source changes (development)"
    )
    args = parser.parse_args(argv)

    try:
        here = resolve(port=args.port)
    except DeploymentError as error:
        # Printed rather than raised. A traceback for a configuration mistake
        # buries the sentence that says how to fix it under a stack that says
        # nothing, and this runs in a terminal or a container log where the
        # last line is the one somebody reads.
        print("Slide-Wright will not start.\n", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        return 2

    built = CLIENT is not None

    print("Slide-Wright")
    for line in here.describe():
        print(f"  {line}" if not line.startswith(" ") else line)

    if not built:
        # Which of the two situations this is matters, because only one of
        # them is the user's to fix. A source checkout is one command away
        # from an interface; an installed copy that lands here is a wheel
        # built without its client, and no command the user runs repairs it.
        print()
        print("  no built client found, so only /api is served.")
        print("  in a source checkout, build it:")
        print("    npm --prefix src/product/web install")
        print("    npm --prefix src/product/web run build")
        print("  in an installed copy this should not happen -- the wheel")
        print("  carries the client. Please report it.")

    # Only ever on the machine the browser is on. Opening one on a server is
    # at best useless and at worst starts a browser nobody asked for as root.
    if built and here.is_local and not args.no_browser:
        webbrowser.open(f"http://{here.bind}:{here.port}")

    uvicorn.run(
        # A string and a factory under --reload, because the reloader has to be
        # able to re-import; the resolved app object otherwise, so the
        # deployment above is the one that is served rather than a second one
        # resolved again from the environment.
        "slide_wright_api.app:create_app" if args.reload else create_app(deployment=here),
        factory=args.reload,
        host=here.bind,
        port=here.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
