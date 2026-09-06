"""Run `tidy` across every fixture and check the promise held on each.

The lesson this project keeps relearning: a synthetic corpus validates the code
against itself. The Phase 1 exit check ran twelve real decks and three passed,
while 183 tests were green. `tidy` has so far been exercised on two decks.

So this runs it over everything, and checks the claims that matter rather than
whether it crashed:

  A  content     not one word or number changed
  B  natives     charts, workbooks, diagrams, media and text runs all survive
  C  bounded     no shape moved further than the tolerance
  D  converge    a second pass finds nothing left to do
  E  reversible  version 0 is still byte-identical to the input

    python scripts/tidy_validation.py
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "engine"))

from slide_wright.apply import apply_changes  # noqa: E402
from slide_wright.brand import plan_conformance, read_profile  # noqa: E402
from slide_wright.charts import census as chart_census  # noqa: E402
from slide_wright.diff import diff  # noqa: E402
from slide_wright.fidelity import compare  # noqa: E402
from slide_wright.inspect import EMU_PER_INCH, inspect  # noqa: E402
from slide_wright.layout import DEFAULT_TOLERANCE_EMU, plan_alignment  # noqa: E402
from slide_wright.smartart import census as diagram_census  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "third-party"
CORPUS = REPO / "tests" / "corpus"


@dataclass
class Result:
    deck: str
    fonts: int = 0
    nudges: int = 0
    fidelity: float = 0.0
    content: str = ""
    natives: str = ""
    bounded: str = ""
    converge: str = ""
    reversible: str = ""

    def row(self) -> str:
        return (
            f"  {self.deck[:32]:<34} {self.fonts:>3}f {self.nudges:>3}n "
            f"{self.fidelity:>6.2f}%  {self.content:<10} {self.natives:<12} "
            f"{self.bounded:<10} {self.converge:<10} {self.reversible}"
        )


def native_counts(path) -> dict:
    deck = inspect(path)
    counts = {
        "runs": sum(len(s.runs) for s in deck.all_shapes()),
        "shapes": len(list(deck.all_shapes())),
    }
    counts.update({f"chart_{k}": v for k, v in chart_census(path).items()
                   if isinstance(v, int)})
    counts.update({f"dgm_{k}": v for k, v in diagram_census(path).items()
                   if isinstance(v, int)})
    return counts


def tidy_once(source: Path, out: Path) -> tuple[int, int]:
    """Plan both passes from one reading, apply as one change set."""
    deck = inspect(source)
    conformance = plan_conformance(deck, read_profile(source), source.name)
    alignment = plan_alignment(deck, DEFAULT_TOLERANCE_EMU, source.name)

    changes = [*conformance.changes, *alignment.changes]
    if not changes:
        shutil.copy(source, out)
        return 0, 0

    changeset = conformance.to_changeset(str(source))
    for change in alignment.changes:
        changeset.add(change)
    changeset.approve_all()
    apply_changes(source, changeset, out)
    return len(conformance.changes), len(alignment.changes)


def check(path: Path, work: Path) -> Result:
    result = Result(deck=path.name)
    out = work / "tidied.pptx"

    # Hashed before the run, so check E compares two different moments. The
    # first version of this compared the file against itself after the fact,
    # which is always true and proved nothing.
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()

    try:
        result.fonts, result.nudges = tidy_once(path, out)
    except Exception as exc:  # noqa: BLE001 — report, never crash the sweep
        result.content = f"ERROR {str(exc)[:22]}"
        return result

    if not result.fonts and not result.nudges:
        result.content = "nothing"
        result.natives = result.bounded = result.converge = "n/a"
        result.reversible = "n/a"
        return result

    report = compare(str(path), str(out))
    result.fidelity = report.fidelity_score

    # A — content
    delta = diff(path, out)
    result.content = "ok" if not delta.content_deltas else \
        f"CHANGED {len(delta.content_deltas)}"

    # B — native objects
    before, after = native_counts(path), native_counts(out)
    lost = [k for k in before if after.get(k, 0) < before[k]]
    result.natives = "ok" if not lost else f"LOST {','.join(lost)[:10]}"

    # C — bounded movement
    moves = [d for d in delta.deltas if d.kind == "geometry"]
    worst = 0
    for move in moves:
        if isinstance(move.before, tuple) and isinstance(move.after, tuple):
            worst = max(worst, abs(move.after[0] - move.before[0]),
                        abs(move.after[1] - move.before[1]))
    result.bounded = ("ok" if worst <= DEFAULT_TOLERANCE_EMU
                      else f"OVER {worst / EMU_PER_INCH:.3f}in")

    # D — convergence
    second = work / "twice.pptx"
    fonts2, nudges2 = tidy_once(out, second)
    result.converge = "ok" if (fonts2 + nudges2) == 0 else f"AGAIN {fonts2 + nudges2}"

    # E — the source is never modified. The whole product rests on this, and
    # it is the cheapest thing in the file to check.
    after_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    result.reversible = "ok" if after_hash == source_hash else "SOURCE MUTATED"
    return result


def main() -> int:
    decks = sorted(FIXTURES.glob("*.pptx")) + sorted(CORPUS.glob("*.pptx"))
    if not decks:
        print("no fixtures; run scripts/fetch_fixtures.py first", file=sys.stderr)
        return 1

    print(f"TIDY VALIDATION — {len(decks)} decks\n")
    print(f"  {'deck':<34} {'fonts':>4} {'nudge':>4} {'fidelity':>7}  "
          f"{'A content':<10} {'B natives':<12} {'C bound':<10} "
          f"{'D converge':<10} E source")
    print("  " + "-" * 124)

    results = []
    for deck in decks:
        with tempfile.TemporaryDirectory() as tmp:
            result = check(deck, Path(tmp))
        results.append(result)
        print(result.row())

    acted = [r for r in results if r.fonts or r.nudges]

    def failures(field):
        """Decks that actually ran the pass and did not satisfy the claim."""
        return [r for r in acted if getattr(r, field) != "ok"]

    print()
    print(f"  decks tidied            {len(acted)}/{len(results)} "
          f"({len(results) - len(acted)} already clean)")
    print(f"  A content preserved     {len(acted) - len(failures('content')):>2}"
          f"/{len(acted)}")
    print(f"  B natives preserved     {len(acted) - len(failures('natives')):>2}"
          f"/{len(acted)}")
    print(f"  C movement bounded      {len(acted) - len(failures('bounded')):>2}"
          f"/{len(acted)}")
    print(f"  D converged in one pass {len(acted) - len(failures('converge')):>2}"
          f"/{len(acted)}")

    broken = [r for r in results
              if any(getattr(r, f) not in ("ok", "n/a", "nothing", "")
                     for f in ("content", "natives", "bounded", "converge", "reversible"))]
    print()
    print(f"  DECKS WITH A BROKEN PROMISE: {len(broken)}")
    for r in broken:
        print(f"    {r.deck}: content={r.content} natives={r.natives} "
              f"bounded={r.bounded} converge={r.converge}")
    return 0 if not broken else 2


if __name__ == "__main__":
    raise SystemExit(main())
