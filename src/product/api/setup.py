"""Put the built client inside the wheel.

The product is one `pip install`. ADR-0010 says the client builds to static
files and that only one runtime should be a dependency for the user -- and a
user who has to install Node and run a Vite build to get an interface has two.

So the wheel carries the client. This hook copies `src/product/web/dist` into
`slide_wright_api/client/` as the wheel is built, which is the location
`app._find_client()` looks in first.

It copies into the *build* directory rather than into the source tree, so a
checkout is never left holding a stale copy of its own client, and an editable
install keeps reading `web/dist` directly -- which is what the dev loop wants,
because Vite rewrites it on every build.

**It does not fail when the client is missing.** CI installs this package
before it builds the client, and the dev loop legitimately runs without one.
The guard against shipping an interface-less wheel is not here: it is
`tests/product/test_packaging.py`, which builds a real wheel and looks inside.
A check that cannot tell a release apart from an editable install is a check
that has to be disabled, and a disabled check protects nothing.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

HERE = Path(__file__).resolve().parent
WEB_DIST = HERE.parent / "web" / "dist"


class build_py(_build_py):
    def run(self) -> None:
        super().run()

        if not (WEB_DIST / "index.html").is_file():
            self.announce(
                f"slide-wright: no built client at {WEB_DIST}; "
                "the wheel will serve /api only",
                level=3,  # distutils WARN
            )
            return

        target = Path(self.build_lib) / "slide_wright_api" / "client"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(WEB_DIST, target)

        count = sum(1 for _ in target.rglob("*") if _.is_file())
        self.announce(f"slide-wright: packaged {count} client file(s)", level=2)


setup(cmdclass={"build_py": build_py})
