"""Phase 1 exit check: edit N real decks end to end and verify every one.

The roadmap's exit condition for Phase 1 is not "the tests pass" — it is that
real decks, authored by other people in other tools, survive a real edit with
verification intact. This runs that check and prints the evidence.

    python scripts/phase1_exit_check.py [--decks DIR] [--limit N]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "engine"))

from slide_wright.changeset import Change, Op  # noqa: E402
from slide_wright.package import UnsafePackageError  # noqa: E402
from slide_wright.session import Session, SessionError  # noqa: E402


def pick_target(deck):
    """Choose a text shape worth editing: the longest run of real prose."""
    best = None
    for slide in deck.slides:
        for shape in slide.shapes:
            if not shape.has_text or len(shape.text.strip()) < 12:
                continue
            if best is None or len(shape.text) > len(best[1].text):
                best = (slide.number, shape)
    return best


def run_one(path: Path) -> tuple[bool | None, str]:
    """Returns (True ok, False failed, None skipped) and a detail line."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            session = Session.open(path, workspace=Path(tmp) / "ws")
            deck = session.deck()
            target = pick_target(deck)
            if target is None:
                # An image-only deck has nothing this check can exercise.
                # Reporting it as a failure would overstate the problem.
                return None, "skipped: no editable text (image-only deck)"
            slide_no, shape = target

            changeset = session.propose("phase 1 exit check")
            changeset.add(Change(
                id="c1", op=Op.SET_TEXT, slide=slide_no, target=shape.id,
                before=shape.text, after="Slide-Wright verification marker",
            ))
            changeset.approve_all()
            report = session.apply()

            if not report.deliverable:
                return False, "; ".join(report.blocking_reasons)

            changed = len(report.fidelity.changed)
            score = report.fidelity.fidelity_score
            if changed != 1:
                return False, f"{changed} parts changed, expected 1"
            if report.fidelity.native_losses:
                return False, "native loss: " + "; ".join(report.fidelity.native_losses)

            session.export(Path(tmp) / "out.pptx")
            return True, (
                f"slide {slide_no}, 1 part changed, {score:.2f}% identical, "
                f"tables {report.fidelity.source_census.tables}->"
                f"{report.fidelity.output_census.tables}"
            )
    except (UnsafePackageError, SessionError) as exc:
        return False, f"refused: {exc}"
    except Exception as exc:  # noqa: BLE001 - the check must report, not crash
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--decks", default=r"C:/Users/mukun/Downloads", help="directory of .pptx")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    decks = sorted(Path(args.decks).glob("*.pptx"))[: args.limit]
    if not decks:
        print("no decks found", file=sys.stderr)
        return 1

    print(f"PHASE 1 EXIT CHECK — {len(decks)} real decks, edited end to end\n")
    passed = failed = skipped = 0
    for deck in decks:
        ok, detail = run_one(deck)
        label = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        passed += ok is True
        failed += ok is False
        skipped += ok is None
        print(f"  [{label}] {deck.name[:46]:<46} {detail}")

    eligible = passed + failed
    tail = f"  ({skipped} skipped)" if skipped else ""
    print(f"\n{passed}/{eligible} eligible decks edited and verified{tail}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
