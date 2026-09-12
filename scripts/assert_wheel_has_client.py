"""Fail if the wheel a user would install has no interface in it.

    python -m pip wheel --no-deps --no-build-isolation -w wheelout src/product/api
    python scripts/assert_wheel_has_client.py wheelout

The product surface shipped without its client for its whole life and every
test stayed green, because every test runs from the source checkout -- the one
place where the client is found by a relative path that only works there. The
only check that catches this is one that reads the built artifact, so this
reads the built artifact.

It lives in a file rather than inline in the workflow because a check nobody
can run locally is a check that gets debugged by pushing commits.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

PREFIX = "slide_wright_api/client/"
ENTRY = PREFIX + "index.html"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2

    built = sorted(Path(argv[0]).glob("*.whl"))
    if not built:
        print(f"FAIL no wheel in {argv[0]} -- nothing was built to check")
        return 1

    # The engine wheel is expected to be here too: building both into one
    # directory is what the README tells people to do, and demanding exactly
    # one wheel made that documented command fail. Only the API wheel carries
    # an interface, so only the API wheel is the one to ask about.
    wheels = [w for w in built if w.name.startswith("slide_wright_api-")]
    if not wheels:
        print(f"FAIL no slide_wright_api wheel in {argv[0]}. Found: "
              f"{[w.name for w in built]}")
        return 1
    if len(wheels) > 1:
        print(f"FAIL {len(wheels)} API wheels in {argv[0]}, so it is not clear "
              f"which would be released: {[w.name for w in wheels]}")
        return 1

    wheel = wheels[0]
    names = zipfile.ZipFile(wheel).namelist()
    client = [n for n in names if n.startswith(PREFIX)]

    if ENTRY not in names:
        print(f"FAIL {wheel.name} carries no client.")
        print("     Installing it gives an API and no interface.")
        print("     Was the client built before the wheel? It needs:")
        print("       npm --prefix src/product/web run build")
        print("     The wheel contains:")
        for n in sorted(names):
            print(f"       {n}")
        return 1

    scripts = [n for n in client if n.endswith(".js")]
    styles = [n for n in client if n.endswith(".css")]
    if not scripts:
        print(f"FAIL {wheel.name} has an index.html and no script; "
              "that renders a blank page")
        return 1

    # A deck must never ride along. The corpus holds third-party decks that are
    # downloaded and never redistributed, and this step copies a directory.
    documents = [n for n in names if n.endswith((".pptx", ".potx", ".xlsx", ".csv"))]
    if documents:
        print(f"FAIL {wheel.name} carries document files: {documents}")
        return 1

    print(f"wheel OK  {wheel.name}")
    print(f"  {len(client)} client file(s): "
          f"{len(scripts)} script, {len(styles)} stylesheet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
