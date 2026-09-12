"""Skip the product tests when only the engine is installed.

The README's first instruction is to install the engine and run the suite. Done
literally, that used to end in a collection error: `tests/product` imports
fastapi and pydantic, neither of which the engine depends on, and a collection
error interrupts the whole run rather than failing one directory. The engine
tests never got a chance to report.

Engine-only is a supported arrangement, not a broken one. ADR-0008 makes the
deterministic engine a delivery surface in its own right, and CI has a job that
installs the engine alone precisely to prove it does not reach for the product.
So the honest behaviour here is to skip with a reason that names the fix.

This is a `conftest.py` rather than a guard inside each test module because
collection errors happen at import, which is before anything in the module runs.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "slide_wright_api",
    reason=(
        "the product surface is not installed; it is optional. "
        'Install it with:  pip install -e "src/product/api[dev]"'
    ),
)
pytest.importorskip(
    "fastapi",
    reason=(
        "fastapi is not installed, so the product surface cannot be tested. "
        'Install it with:  pip install -e "src/product/api[dev]"'
    ),
)
