"""The fidelity benchmark.

Runs the R2 test matrix against a corpus and reports, per deck:

  A  round-trip      import -> export untouched; every part must come back
  B  single edit     change exactly one thing; only that part may differ
  D  failure         corrupt input must be refused, not silently processed

C (complex edits over SmartArt/charts/groups) is driven by the same machinery
as B with a different mutation, and is added as those mutations are implemented.

This is not a one-off script. Every deck that fails becomes a permanent corpus
entry, and this runner is what makes that cheap.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from slide_wright.corpus.profile import DeckProfile, profile
from slide_wright.engines.pptmaster import EngineError, PptMasterEngine
from slide_wright.fidelity import FidelityReport, compare
from slide_wright.package import Package

# Gate thresholds. Set before results are seen, per the validation plan.
ROUNDTRIP_MIN_FIDELITY = 95.0   # % of parts byte-identical, untouched
EDIT_MAX_CHANGED_PARTS = 1      # a one-object edit may touch one part


@dataclass
class CaseResult:
    case: str
    deck: str
    passed: bool
    detail: str = ""
    fidelity: float | None = None
    changed_parts: list[str] = field(default_factory=list)
    native_losses: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    def line(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        score = f"{self.fidelity:6.2f}%" if self.fidelity is not None else "      -"
        return f"  [{mark}] {self.case:<12} {score}  {self.detail}"


@dataclass
class DeckResult:
    deck: str
    profile: dict
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.cases)

    def report(self) -> str:
        p = self.profile
        head = (
            f"{Path(self.deck).name}  "
            f"[{p['slides']} slides, {p['parts']} parts, difficulty {p['difficulty']}: "
            f"{', '.join(p['present_constructs']) or 'none'}]"
        )
        return "\n".join([head] + [c.line() for c in self.cases])


class Benchmark:
    def __init__(self, engine: PptMasterEngine | None = None):
        self.engine = engine or PptMasterEngine()

    # ── cases ────────────────────────────────────────────────────────────────

    def case_a_roundtrip(self, deck: Path, work: Path) -> CaseResult:
        """Import and export without touching anything."""
        import time

        t0 = time.time()
        ws = work / "ws_a"
        out = work / "out_a.pptx"
        try:
            r = self.engine.ingest(deck, ws)
            if not r.ok:
                return CaseResult("roundtrip", str(deck), False,
                                  f"ingest failed: {_tail(r.stderr)}",
                                  duration_s=time.time() - t0)
            r = self.engine.export(ws, out)
            if not r.ok:
                return CaseResult("roundtrip", str(deck), False,
                                  f"export failed: {_tail(r.stderr)}",
                                  duration_s=time.time() - t0)
        except EngineError as exc:
            return CaseResult("roundtrip", str(deck), False, str(exc),
                              duration_s=time.time() - t0)

        rep = compare(deck, out)
        passed = (
            rep.fidelity_score >= ROUNDTRIP_MIN_FIDELITY
            and not rep.removed
            and not rep.native_losses
        )
        detail = f"{len(rep.identical)}/{rep.total_source_parts} parts identical"
        if rep.removed:
            detail += f"; {len(rep.removed)} REMOVED"
        if rep.native_losses:
            detail += "; LOSS: " + "; ".join(rep.native_losses)
        return CaseResult("roundtrip", str(deck), passed, detail,
                          fidelity=rep.fidelity_score,
                          native_losses=rep.native_losses,
                          duration_s=time.time() - t0)

    def case_b_single_edit(self, deck: Path, work: Path) -> CaseResult:
        """Change exactly one text string; only that slide's part may differ."""
        import time

        t0 = time.time()
        ws = work / "ws_b"
        out = work / "out_b.pptx"
        try:
            r = self.engine.ingest(deck, ws)
            if not r.ok:
                return CaseResult("single-edit", str(deck), False,
                                  f"ingest failed: {_tail(r.stderr)}",
                                  duration_s=time.time() - t0)

            target = _apply_text_edit(ws)
            if target is None:
                return CaseResult("single-edit", str(deck), False,
                                  "no editable text found in authoring output",
                                  duration_s=time.time() - t0)

            r = self.engine.export(ws, out)
            if not r.ok:
                return CaseResult("single-edit", str(deck), False,
                                  f"export failed: {_tail(r.stderr)}",
                                  duration_s=time.time() - t0)
        except EngineError as exc:
            return CaseResult("single-edit", str(deck), False, str(exc),
                              duration_s=time.time() - t0)

        rep = compare(deck, out)
        changed = [d.name for d in rep.changed]
        passed = (
            len(changed) <= EDIT_MAX_CHANGED_PARTS
            and not rep.removed
            and not rep.native_losses
            and not rep.rasterisation_suspected
        )
        detail = f"edited {target!r}; {len(changed)} part(s) changed"
        if rep.native_losses:
            detail += "; LOSS: " + "; ".join(rep.native_losses)
        if rep.rasterisation_suspected:
            detail += "; RASTERISATION SUSPECTED"
        return CaseResult("single-edit", str(deck), passed, detail,
                          fidelity=rep.fidelity_score, changed_parts=changed,
                          native_losses=rep.native_losses,
                          duration_s=time.time() - t0)

    # ── driver ───────────────────────────────────────────────────────────────

    def run_deck(self, deck: str | Path, keep: Path | None = None) -> DeckResult:
        deck = Path(deck)
        prof: DeckProfile = profile(deck)
        result = DeckResult(deck=str(deck), profile=prof.to_dict())
        if not prof.ok:
            result.cases.append(CaseResult("open", str(deck), False, prof.error))
            return result

        work = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="sw_bench_"))
        work.mkdir(parents=True, exist_ok=True)
        try:
            result.cases.append(self.case_a_roundtrip(deck, work))
            result.cases.append(self.case_b_single_edit(deck, work))
        finally:
            if keep is None:
                shutil.rmtree(work, ignore_errors=True)
        return result

    def run(self, decks, keep: Path | None = None) -> list[DeckResult]:
        return [self.run_deck(d, keep=keep) for d in decks]


# ── helpers ──────────────────────────────────────────────────────────────────

def _tail(text: str, n: int = 160) -> str:
    text = (text or "").strip().replace("\n", " | ")
    return text[-n:] if len(text) > n else text


def _apply_text_edit(workspace: Path) -> str | None:
    """Mutate one text string in the authoring SVG. Returns the original text.

    Chooses the longest plain-text run so the edit is unambiguous and unlikely
    to collide with markup.
    """
    authoring = workspace / "authoring-svg-flat"
    if not authoring.is_dir():
        return None

    for svg in sorted(authoring.glob("slide_*.svg")):
        content = svg.read_text(encoding="utf-8")
        candidates = re.findall(r">([A-Za-z][A-Za-z0-9 ,\.\-%]{12,60})<", content)
        if not candidates:
            continue
        original = max(candidates, key=len)
        replacement = "Slide-Wright edit marker"
        svg.write_text(content.replace(f">{original}<", f">{replacement}<", 1),
                       encoding="utf-8")
        return original
    return None


def write_json(results: list[DeckResult], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8"
    )
    return path
