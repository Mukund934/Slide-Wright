"""Run the local app.

    python -m slide_wright_api

Binds to loopback and stays there. `--host` is deliberately absent: a product
whose governing constraint is that the document does not leave the machine
(ADR-0008) must not ship the flag that lets it.
"""

from __future__ import annotations

import argparse
import webbrowser

import uvicorn

from slide_wright_api.app import CLIENT, create_app

HOST = "127.0.0.1"
DEFAULT_PORT = 8787


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="slide-wright-app",
        description="Slide-Wright, running on this machine only.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--no-browser", action="store_true", help="do not open a browser window"
    )
    parser.add_argument(
        "--reload", action="store_true", help="restart on source changes (development)"
    )
    args = parser.parse_args(argv)

    url = f"http://{HOST}:{args.port}"
    built = CLIENT is not None

    print(f"Slide-Wright — {url}")
    print("  the document does not leave this machine (ADR-0008)")
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

    if built and not args.no_browser:
        webbrowser.open(url)

    uvicorn.run(
        "slide_wright_api.app:app" if args.reload else create_app(),
        host=HOST,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
