"""Vendor the ppt-master engine at a pinned version, with our safety edits applied.

ADR-0001 tracks ppt-master upstream rather than forking it. But an upstream that
ships daily, has no CI, and contains two items we must not ship commercially is
not something to depend on live. So we vendor a pinned copy and record exactly
what we changed.

Three hygiene actions are applied on every vendor, and each is a licence or
safety requirement rather than a preference:

  1. DELETE gemini_watermark_remover.py and its assets.
     Stripping provenance watermarks from AI-generated images runs against
     Google's generative-AI terms and against EU AI Act Article 50 transparency
     obligations. It is not load-bearing for anything we do.

  2. QUARANTINE the Simple Icons brand set (3,675 logos).
     Upstream's own notice is correct: "Neither CC0 nor inclusion in this
     repository grants trademark permission." Putting a third party's logo on a
     slide we were paid for is a trademark question, so the set is moved out of
     the default search path and requires explicit opt-in.

  3. RECORD attribution obligations that survive vendoring — notably CHUNK
     Icons (CC BY 4.0), which requires credit and a statement of modification.

The vendored tree is gitignored: it is large, it is not ours, and the pin plus
this script reproduce it exactly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENDOR_ROOT = REPO / "vendor" / "ppt-master"
MANIFEST = REPO / "vendor" / "pin.json"

# Files removed on every vendor. Hygiene action 1.
DELETE = [
    "scripts/gemini_watermark_remover.py",
    "scripts/assets/bg_48.png",
    "scripts/assets/bg_96.png",
]

# Directories moved out of the default path. Hygiene action 2.
QUARANTINE = [
    ("templates/icons/simple-icons", "quarantine/simple-icons"),
]

# Attribution that survives vendoring. Hygiene action 3.
ATTRIBUTION = [
    {
        "asset": "CHUNK Icons",
        "licence": "CC BY 4.0",
        "author": "Noah Jacobus",
        "obligation": "credit the author and state that modifications were made",
    },
    {
        "asset": "Tabler Icons",
        "licence": "MIT",
        "author": "Pawel Kuna",
        "obligation": "retain copyright and licence notice",
    },
    {
        "asset": "Phosphor Icons",
        "licence": "MIT",
        "author": "Phosphor Icons",
        "obligation": "retain copyright and licence notice",
    },
    {
        "asset": "Apache POI preset geometry",
        "licence": "Apache-2.0",
        "author": "The Apache Software Foundation",
        "obligation": "retain NOTICE",
    },
    {
        "asset": "ppt-master",
        "licence": "MIT",
        "author": "Hugo He",
        "obligation": "retain copyright and licence notice",
    },
]


def vendor(source: Path, version: str) -> dict:
    if not (source / "SKILL.md").is_file():
        raise SystemExit(f"not a ppt-master skill directory: {source}")

    if VENDOR_ROOT.exists():
        shutil.rmtree(VENDOR_ROOT)
    VENDOR_ROOT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, VENDOR_ROOT)

    removed, quarantined = [], []

    for rel in DELETE:
        target = VENDOR_ROOT / rel
        if target.exists():
            target.unlink()
            removed.append(rel)

    for rel_from, rel_to in QUARANTINE:
        src, dst = VENDOR_ROOT / rel_from, VENDOR_ROOT / rel_to
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            quarantined.append({"from": rel_from, "to": rel_to})

    manifest = {
        "upstream": "https://github.com/hugohe3/ppt-master",
        "licence": "MIT",
        "version": version,
        "source_path": str(source),
        "vendored_at": VENDOR_ROOT.relative_to(REPO).as_posix(),
        "tree_sha256": _tree_hash(VENDOR_ROOT),
        "file_count": sum(1 for _ in VENDOR_ROOT.rglob("*") if _.is_file()),
        "hygiene": {
            "removed": removed,
            "quarantined": quarantined,
            "forced_export_flags": ["--native-charts-and-tables"],
        },
        "attribution": ATTRIBUTION,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _tree_hash(root: Path) -> str:
    """Order-independent hash of the vendored tree, so the pin is verifiable."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def verify() -> int:
    """Confirm the vendored tree still matches its pin and stays hygienic."""
    if not MANIFEST.is_file():
        print("no vendor pin; run vendor_engine.py --source ...", file=sys.stderr)
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    problems = []
    if not VENDOR_ROOT.is_dir():
        problems.append("vendored tree missing")
    else:
        actual = _tree_hash(VENDOR_ROOT)
        if actual != manifest["tree_sha256"]:
            problems.append(f"tree hash drift: {actual[:12]} != {manifest['tree_sha256'][:12]}")
        for rel in DELETE:
            if (VENDOR_ROOT / rel).exists():
                problems.append(f"hygiene violation: {rel} is present and must be deleted")
        for rel_from, _ in QUARANTINE:
            if (VENDOR_ROOT / rel_from).exists():
                problems.append(f"hygiene violation: {rel_from} is not quarantined")

    if problems:
        for p in problems:
            print(f"FAIL  {p}", file=sys.stderr)
        return 1
    print(f"vendor pin OK  version={manifest['version']}  files={manifest['file_count']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, help="path to the ppt-master skill directory")
    ap.add_argument("--version", default="unpinned", help="upstream version or commit")
    ap.add_argument("--verify", action="store_true", help="check the existing pin")
    args = ap.parse_args()

    if args.verify:
        return verify()
    if not args.source:
        ap.error("--source is required unless --verify is given")

    m = vendor(args.source.resolve(), args.version)
    print(f"vendored {m['file_count']} files  tree={m['tree_sha256'][:12]}")
    print(f"  removed:     {len(m['hygiene']['removed'])} file(s)")
    print(f"  quarantined: {len(m['hygiene']['quarantined'])} director(ies)")
    print(f"  pin written: {MANIFEST.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
