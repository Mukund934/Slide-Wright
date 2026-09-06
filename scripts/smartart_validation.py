"""SmartArt validation across the third-party fixture corpus.

Closes the corpus gap that has been open since the project started: every
earlier fidelity result was measured on decks containing **zero** SmartArt,
so nothing was known about the construct most likely to be silently destroyed.

Four cases per fixture, matching the validation matrix:

  A  read        can we see the diagram at all — parts, nodes, structure
  B  preserve    edit something else on the deck; does the diagram survive
  C  refuse      target the diagram itself; does it fail closed
  D  round-trip  does the heavy engine survive the deck

    python scripts/smartart_validation.py
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "engine"))

from slide_wright.apply import ApplyError, apply_changes  # noqa: E402
from slide_wright.changeset import Change, ChangeSet, Op  # noqa: E402
from slide_wright.engines.pptmaster import EngineError, PptMasterEngine  # noqa: E402
from slide_wright.fidelity import compare  # noqa: E402
from slide_wright.inspect import inspect  # noqa: E402
from slide_wright.smartart import (  # noqa: E402
    SmartArtUnsupported,
    census,
    find_all,
    guard_edit,
)

FIXTURES = REPO / "tests" / "fixtures" / "third-party"


@dataclass
class Result:
    fixture: str
    diagrams: int = 0
    parts: int = 0
    nodes: int = 0
    points: int = 0
    read: str = ""
    preserve: str = ""
    refuse: str = ""
    roundtrip: str = ""

    def row(self) -> str:
        return (
            f"  {self.fixture[:34]:<36} "
            f"{self.diagrams:>2}d {self.parts:>2}p {self.nodes:>3}n {self.points:>4}pt  "
            f"{self.read:<10} {self.preserve:<22} {self.refuse:<10} {self.roundtrip}"
        )


def case_read(path: Path, r: Result) -> None:
    c = census(path)
    r.diagrams, r.parts = c["diagrams"], c["diagram_parts"]
    r.nodes, r.points = c["nodes"], c["points"]
    if c["diagrams"] == 0:
        r.read = "NO DIAGRAM"
    elif c["incomplete"]:
        r.read = "INCOMPLETE"
    else:
        r.read = "ok"


def case_preserve(path: Path, r: Result) -> None:
    """Edit a non-diagram shape; the diagram must be untouched."""
    deck = inspect(path)
    target = next(
        (s for s in deck.all_shapes()
         if s.has_text and s.kind not in {"smartart", "group"} and len(s.text.strip()) > 2),
        None,
    )
    if target is None:
        r.preserve = "n/a (no other text)"
        return

    slide_no = next(s.number for s in deck.slides if target in s.shapes)
    cs = ChangeSet(deck=str(path))
    cs.add(Change(id="c1", op=Op.SET_TEXT, slide=slide_no, target=target.id,
                  before=target.text, after="Slide-Wright marker"))
    cs.approve_all()

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.pptx"
        try:
            apply_changes(path, cs, out)
        except ApplyError as exc:
            r.preserve = f"apply refused: {str(exc)[:16]}"
            return
        before, after = census(path), census(out)
        rep = compare(path, out)
        intact = all(after[k] >= before[k] for k in ("diagrams", "diagram_parts", "nodes", "points"))
        r.preserve = (
            f"ok {rep.fidelity_score:.1f}% {len(rep.changed)}pt"
            if intact else "DIAGRAM DAMAGED"
        )


def case_refuse(path: Path, r: Result) -> None:
    """Targeting the diagram must fail closed."""
    arts = find_all(path)
    if not arts:
        r.refuse = "n/a"
        return
    try:
        guard_edit(path, arts[0].slide, arts[0].shape_id)
        r.refuse = "NOT GUARDED"
    except SmartArtUnsupported:
        r.refuse = "refused"


def case_roundtrip(path: Path, r: Result, engine: PptMasterEngine | None) -> None:
    if engine is None:
        r.roundtrip = "engine n/a"
        return
    with tempfile.TemporaryDirectory() as tmp:
        ws, out = Path(tmp) / "ws", Path(tmp) / "out.pptx"
        try:
            res = engine.ingest(path, ws)
            if not res.ok:
                r.roundtrip = f"ingest refused: {_reason(res.stderr)}"
                return
            res = engine.export(ws, out)
            if not res.ok:
                r.roundtrip = f"export refused: {_reason(res.stderr)}"
                return
        except EngineError as exc:
            r.roundtrip = f"engine error: {str(exc)[:28]}"
            return
        before, after = census(path), census(out)
        rep = compare(path, out)
        intact = all(after[k] >= before[k] for k in ("diagrams", "diagram_parts", "points"))
        r.roundtrip = (
            f"ok {rep.fidelity_score:.1f}%" if intact else "DIAGRAM LOST"
        )


def _reason(stderr: str) -> str:
    text = (stderr or "").strip().replace("\n", " ")
    for marker in ("Canonical authoring projection failed:", "Error:"):
        if marker in text:
            text = text.split(marker, 1)[1]
            break
    return text.strip()[:40] or "unknown"


def main() -> int:
    fixtures = sorted(FIXTURES.glob("*.pptx"))
    if not fixtures:
        print("no fixtures; run scripts/fetch_fixtures.py first", file=sys.stderr)
        return 1

    try:
        engine = PptMasterEngine()
    except EngineError:
        engine = None

    print(f"SMARTART VALIDATION — {len(fixtures)} third-party fixtures\n")
    print(f"  {'fixture':<36} {'structure':<18}  {'A read':<10} "
          f"{'B preserve':<22} {'C refuse':<10} D round-trip")
    print("  " + "-" * 116)

    results = []
    for path in fixtures:
        r = Result(fixture=path.name)
        case_read(path, r)
        case_preserve(path, r)
        case_refuse(path, r)
        case_roundtrip(path, r, engine)
        results.append(r)
        print(r.row())

    read_ok = sum(1 for r in results if r.read == "ok")
    preserved = sum(1 for r in results if r.preserve.startswith("ok"))
    damaged = sum(1 for r in results if "DAMAGED" in r.preserve or "LOST" in r.roundtrip)
    refused = sum(1 for r in results if r.refuse == "refused")
    rt_ok = sum(1 for r in results if r.roundtrip.startswith("ok"))

    print()
    print(f"  A read          {read_ok}/{len(results)} diagrams read completely")
    print(f"  B preserve      {preserved} edited with the diagram intact "
          f"({sum(1 for r in results if r.preserve.startswith('n/a'))} had nothing else to edit)")
    print(f"  C refuse        {refused}/{len(results)} diagram edits correctly refused")
    print(f"  D round-trip    {rt_ok}/{len(results)} survived the heavy engine")
    print()
    print(f"  DIAGRAMS DAMAGED ANYWHERE: {damaged}")
    return 0 if damaged == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
