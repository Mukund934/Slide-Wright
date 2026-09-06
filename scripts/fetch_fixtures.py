"""Fetch third-party PPTX fixtures, recording provenance and hashes.

The files themselves are **not committed**. They belong to their projects, and
redistributing someone else's test corpus inside ours is both a licensing
question and a 50 MB one. Instead the manifest below is tracked: it names every
file, its upstream URL, its licence, and the SHA-256 we verified. Anyone can
reproduce the corpus exactly by running this script.

Licences of the sources used here:

  Apache POI      Apache-2.0   — permissive, attribution required
  LibreOffice     MPL-2.0      — permissive, file-level copyleft on modification

We only ever read these files. We never modify or redistribute them, so the
copyleft terms are not engaged; attribution is recorded in the manifest and in
`docs/guides/testing-strategy.md`.

    python scripts/fetch_fixtures.py            # download and verify
    python scripts/fetch_fixtures.py --verify   # check what is already here
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "third-party"
MANIFEST = REPO / "tests" / "fixtures" / "manifest.json"

POI = "https://raw.githubusercontent.com/apache/poi/trunk/test-data/slideshow"
OXSDK = ("https://raw.githubusercontent.com/dotnet/Open-XML-SDK/main/test/"
         "DocumentFormat.OpenXml.Tests.Assets/assets/TestDataStorage/v2FxTestFiles/presentation")
PYPPTX = "https://raw.githubusercontent.com/scanny/python-pptx/master/features/steps/test_files"
TSPPTX = "https://raw.githubusercontent.com/shbernal/ts-pptx/master/test/read/fixtures"
AUTOMIZER = "https://raw.githubusercontent.com/singerla/pptx-automizer/main/__tests__/pptx-templates"
LO_SD = "https://raw.githubusercontent.com/LibreOffice/core/master/sd/qa/unit/data/pptx"
LO_OOX = "https://raw.githubusercontent.com/LibreOffice/core/master/oox/qa/unit/data"

# Chosen for coverage, not volume. Each entry earns its place by exercising
# something the others do not.
SOURCES = [
    # ── Apache POI (Apache-2.0) — large, non-stub drawing caches ─────────────
    (f"{POI}/SmartArt.pptx", "poi-smartart.pptx", "Apache-2.0", "Apache POI",
     "complete diagram quad with an 8 KB cached drawing"),
    (f"{POI}/smartart-rotated-text.pptx", "poi-smartart-rotated-text.pptx", "Apache-2.0", "Apache POI",
     "14 KB cached drawing; rotated text inside diagram nodes"),
    (f"{POI}/smartart-simple.pptx", "poi-smartart-simple.pptx", "Apache-2.0", "Apache POI",
     "diagram rels pointing at media; largest layout part"),

    # ── LibreOffice (MPL-2.0) — structural edge cases ────────────────────────
    (f"{LO_SD}/smartart-org-chart.pptx", "lo-smartart-org-chart.pptx", "MPL-2.0", "LibreOffice",
     "hierarchy layout — the canonical org chart"),
    (f"{LO_SD}/smartart-autoTxRot.pptx", "lo-smartart-three-diagrams.pptx", "MPL-2.0", "LibreOffice",
     "THREE diagram sets in one deck; no drawing cache — part-numbering stress"),
    (f"{LO_SD}/smartart-picture-strip.pptx", "lo-smartart-picture-strip.pptx", "MPL-2.0", "LibreOffice",
     "diagram relationships pointing at image parts"),
    (f"{LO_OOX}/smartart-groupshape.pptx", "lo-smartart-groupshape.pptx", "MPL-2.0", "LibreOffice",
     "SmartArt rendered as a group shape"),
    (f"{LO_SD}/smartart-cycle-matrix.pptx", "lo-smartart-cycle-matrix.pptx", "MPL-2.0", "LibreOffice",
     "cycle-matrix layout"),
    (f"{LO_SD}/smartart-pyramid-1child.pptx", "lo-smartart-pyramid.pptx", "MPL-2.0", "LibreOffice",
     "pyramid layout with a single child node"),
    (f"{LO_SD}/tdf149551_SmartArt_Gear.pptx", "lo-smartart-gear.pptx", "MPL-2.0", "LibreOffice",
     "gear layout; regression fixture for a real rendering bug"),

    # ── MIT-licensed multi-diagram decks — the cleanest licence, richest content ──
    (f"{AUTOMIZER}/SlideWithDiagrams.pptx", "automizer-three-diagrams.pptx", "MIT", "pptx-automizer",
     "THREE SmartArt graphics (matrix3, venn2, AlternatingHexagons); one has no text at all; German-locale layout names"),
    (f"{TSPPTX}/smartart-families.pptx", "tspptx-smartart-families.pptx", "MIT", "ts-pptx",
     "FOUR SmartArt graphics across 20 diagram parts — the densest diagram fixture found"),
    (f"{TSPPTX}/mixed.pptx", "tspptx-mixed.pptx", "MIT", "ts-pptx",
     "mixed shape kinds in one deck"),
    (f"{OXSDK}/SmartArt_OrgChart1.pptx", "oxsdk-smartart-orgchart.pptx", "MIT", "dotnet/Open-XML-SDK",
     "Microsoft's own SmartArt test asset — the reference implementation's fixture"),

    # ── Other hard constructs still listed as unproven ───────────────────────
    (f"{PYPPTX}/shp-access-ole-object.pptx", "pypptx-ole-object.pptx", "MIT", "python-pptx",
     "OLE embedded object — previously untested"),
    (f"{OXSDK}/Chart_2D.pptx", "oxsdk-chart-2d.pptx", "MIT", "dotnet/Open-XML-SDK",
     "native 2D charts"),
    (f"{OXSDK}/Table_Large.pptx", "oxsdk-table-large.pptx", "MIT", "dotnet/Open-XML-SDK",
     "large native table"),
    (f"{PYPPTX}/cht-chart-type.pptx", "pypptx-chart-types.pptx", "MIT", "python-pptx",
     "several chart types in one deck"),
    (f"{PYPPTX}/shp-shapes.pptx", "pypptx-shapes.pptx", "MIT", "python-pptx",
     "assorted shape kinds"),

    # ── Real professional decks. US federal works are public domain
    #    (17 U.S.C. sec.105) — genuine output, not synthetic fixtures. ────────
    ("https://www.eia.gov/outlooks/archive/aeo23/ppt/AEO2023_Release_Presentation.pptx",
     "eia-aeo2023-release.pptx", "Public domain (US federal work)", "US EIA",
     "real government release deck — chart-heavy professional output"),
    ("https://ntrs.nasa.gov/api/citations/20230012455/downloads/"
     "2023_Raven_St_Clair_ES6_ExitPresentation_STRIVES.pptx",
     "nasa-es6-exit.pptx", "Public domain (US federal work)", "NASA NTRS",
     "real NASA project presentation"),
]


def fetch(url: str, dest: Path) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Slide-Wright fixture fetcher"}
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        data = response.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return data


def looks_like_pptx(data: bytes) -> bool:
    return data[:2] == b"PK" and b"[Content_Types].xml" in data[:4096]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true",
                    help="check existing files against the manifest")
    args = ap.parse_args()

    if args.verify:
        return verify()

    entries, failures = [], []
    for url, name, licence, project, why in SOURCES:
        dest = FIXTURES / name
        try:
            data = fetch(url, dest)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            failures.append(f"{name}: {exc}")
            print(f"  [FAIL] {name:<38} {exc}")
            continue

        if not looks_like_pptx(data):
            dest.unlink(missing_ok=True)
            failures.append(f"{name}: not a valid OPC package")
            print(f"  [FAIL] {name:<38} not a .pptx")
            continue

        digest = hashlib.sha256(data).hexdigest()
        entries.append({
            "name": name, "url": url, "licence": licence, "project": project,
            "why": why, "sha256": digest, "bytes": len(data),
        })
        print(f"  [ok]   {name:<38} {len(data):>7,} bytes  {digest[:12]}")

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(
            {
                "note": (
                    "Third-party PPTX fixtures. NOT committed — they belong to their "
                    "projects. Reproduce with `python scripts/fetch_fixtures.py`."
                ),
                "attribution": {
                    "Apache POI": "Apache-2.0 — https://poi.apache.org/",
                    "LibreOffice": "MPL-2.0 — https://www.libreoffice.org/",
                    "dotnet/Open-XML-SDK": "MIT — https://github.com/dotnet/Open-XML-SDK",
                    "python-pptx": "MIT — https://github.com/scanny/python-pptx",
                    "ts-pptx": "MIT — https://github.com/shbernal/ts-pptx",
                    "pptx-automizer": "MIT — https://github.com/singerla/pptx-automizer",
                    "US EIA / NASA": "US federal works, public domain (17 U.S.C. 105)",
                },
                "fixtures": entries,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\n{len(entries)}/{len(SOURCES)} fetched · manifest: {MANIFEST.relative_to(REPO)}")
    if failures:
        print("failures:")
        for f in failures:
            print(f"  {f}")
    return 0 if entries else 1


def verify() -> int:
    if not MANIFEST.is_file():
        print("no manifest; run without --verify first", file=sys.stderr)
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    missing, altered, ok = [], [], 0
    for entry in manifest["fixtures"]:
        path = FIXTURES / entry["name"]
        if not path.is_file():
            missing.append(entry["name"])
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            altered.append(entry["name"])
            continue
        ok += 1
    print(f"{ok}/{len(manifest['fixtures'])} fixtures verified")
    for name in missing:
        print(f"  missing: {name}")
    for name in altered:
        print(f"  ALTERED: {name}")
    return 0 if not (missing or altered) else 1


if __name__ == "__main__":
    raise SystemExit(main())
