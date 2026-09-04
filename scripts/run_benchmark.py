"""Run the fidelity benchmark over a corpus and write results.

Usage:
    python scripts/run_benchmark.py [deck ...] [--json PATH]

With no decks, runs the registered corpus (tests/corpus/ plus the external
decks listed in CORPUS_EXTERNAL). External decks are referenced by path and
never copied into the repository — they are not ours to redistribute.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "engine"))

from slide_wright.benchmark import Benchmark, write_json  # noqa: E402

CORPUS_DIR = REPO / "tests" / "corpus"

# Real decks used as corpus members. Referenced, never copied: they belong to
# their authors. Selected by profiler difficulty, not by filename.
CORPUS_EXTERNAL = [
    Path(r"C:/Users/mukun/Downloads/Mobile_Antenna.pptx"),            # 30 groups, 24 tables
    Path(r"C:/Users/mukun/Downloads/HACK4CROWN.pptx"),                # 13 groups, 15 custGeom
    Path(r"C:/Users/mukun/Downloads/AgroLens - Project Phase-I final.pptx"),  # Google export
    Path(r"C:/Users/mukun/Downloads/Lecture 1 & 2 NEW.pptx"),         # 64 slides, 289 parts
]


def corpus() -> list[Path]:
    decks = sorted(CORPUS_DIR.glob("*.pptx"))
    decks += [p for p in CORPUS_EXTERNAL if p.is_file()]
    return decks


def main(argv: list[str]) -> int:
    json_path = None
    if "--json" in argv:
        i = argv.index("--json")
        json_path = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]

    decks = [Path(a) for a in argv] or corpus()
    if not decks:
        print("no decks found", file=sys.stderr)
        return 1

    bench = Benchmark()
    results = []
    print(f"running fidelity benchmark over {len(decks)} deck(s)\n")
    for deck in decks:
        res = bench.run_deck(deck)
        results.append(res)
        print(res.report())
        print()

    passed = sum(1 for r in results if r.passed)
    print("=" * 72)
    print(f"RESULT  {passed}/{len(results)} decks passed all cases")
    for r in results:
        if not r.passed:
            for c in r.cases:
                if not c.passed:
                    print(f"  FAIL  {Path(r.deck).name}  {c.case}: {c.detail}")

    if json_path:
        print(f"\nwrote {write_json(results, json_path)}")
    return 0 if passed == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
