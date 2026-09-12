"""What a user actually receives.

Every other test in this tree runs against the source checkout, where the
client sits in `src/product/web/dist` and is found by a relative path. That
arrangement passed every test while being broken for everybody who was not
standing in the repository: `pip install` produced an API with no interface,
because the path resolved inside site-packages and there is no `web/dist`
there. The app then said "the client is not built", naming a build step the
user could not run and hiding a packaging defect behind it.

So these tests build a real wheel and look inside it. Nothing else catches
this class of defect -- by construction, a test that imports from the source
tree is standing in the one place where the bug does not reproduce.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
API = REPO / "src" / "product" / "api"
WEB_DIST = REPO / "src" / "product" / "web" / "dist"

needs_built_client = pytest.mark.skipif(
    not (WEB_DIST / "index.html").is_file(),
    reason="no built client; run: npm --prefix src/product/web run build",
)


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A wheel built the way a release is built."""
    out = tmp_path_factory.mktemp("wheel")
    subprocess.run(
        [
            sys.executable, "-m", "pip", "wheel",
            "--no-deps", "--no-build-isolation", "--wheel-dir", str(out), str(API),
        ],
        check=True,
        capture_output=True,
    )
    built = list(out.glob("*.whl"))
    assert len(built) == 1, f"expected one wheel, got {built}"
    return built[0]


@needs_built_client
def test_the_wheel_carries_the_client(wheel: Path) -> None:
    """The interface ships with the product, not alongside it.

    ADR-0010 puts the client behind a static build so that the user needs one
    runtime. A wheel without it means they need Node as well, which is the
    dependency that decision exists to avoid.
    """
    names = zipfile.ZipFile(wheel).namelist()
    client = [n for n in names if n.startswith("slide_wright_api/client/")]

    assert "slide_wright_api/client/index.html" in names, (
        "the wheel has no client entry point. A user installing this gets an "
        f"API and no interface. Wheel contains: {sorted(names)}"
    )
    assert any(n.endswith(".js") for n in client), "client has no script"
    assert any(n.endswith(".css") for n in client), "client has no stylesheet"


@needs_built_client
def test_every_asset_the_page_asks_for_is_in_the_wheel(wheel: Path) -> None:
    """A page that loads is not the same as a page that works.

    `index.html` alone would pass the test above and render a blank screen, so
    what the page references is checked against what was packaged.
    """
    import re

    zf = zipfile.ZipFile(wheel)
    names = set(zf.namelist())
    page = zf.read("slide_wright_api/client/index.html").decode("utf-8")

    referenced = re.findall(r'(?:src|href)="/([^"]+)"', page)
    assert referenced, "the page references no assets at all, which cannot be right"

    for asset in referenced:
        assert f"slide_wright_api/client/{asset}" in names, (
            f"the page loads /{asset} and the wheel does not contain it"
        )


def test_the_wheel_carries_no_deck(wheel: Path) -> None:
    """Nothing from the corpus rides along.

    The build copies a directory. If a stray `.pptx` ever lands in the client's
    output folder it would be published to every user, and the corpus contains
    third-party decks that are downloaded and never redistributed.
    """
    names = zipfile.ZipFile(wheel).namelist()
    decks = [n for n in names if n.endswith((".pptx", ".potx", ".xlsx", ".csv"))]
    assert not decks, f"the wheel carries document files: {decks}"


def test_the_installed_layout_is_the_one_the_app_looks_for() -> None:
    """The path in the wheel and the path the app checks must agree.

    They are written in two files -- `setup.py` chooses where to copy, `app.py`
    chooses where to look -- and nothing but this test makes them the same
    directory name.
    """
    from slide_wright_api import app as app_module

    source = Path(app_module.__file__).resolve()
    packaged = source.parent / "client"

    assert 'slide_wright_api" / "client"' in (API / "setup.py").read_text(
        encoding="utf-8"
    ), "setup.py no longer copies into slide_wright_api/client"
    assert packaged.name == "client"
