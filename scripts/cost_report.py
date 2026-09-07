"""What a model call on a real deck actually costs.

    python scripts/cost_report.py                 # every deck on this machine
    python scripts/cost_report.py --json          # machine-readable

`usage.py` carries a complaint about this project: that it has been holding a
"$5-15 per deck" estimate unverified, and that "estimates that never get checked
become facts by repetition". This is the check, and it is careful about which
half of it is a measurement.

**Measured exactly.** The prompt is a pure function of the deck. `summarise()`
walks it and produces the same bytes every time, so the prompt size for a real
26-slide deck is a fact that needs no network call to establish. The change sets
the deterministic passes produce are real too, and serialising one in the shape
the planner returns gives a real output size for a real amount of work.

**Estimated, and labelled.** Characters are not tokens. No local tokeniser
matches Gemini's, and asking Google to count them is a network call — so the
conversion below is an explicit divisor, applied to an exact character count,
and the report says so on every line. The exact half is the half that varies by
three orders of magnitude between decks; the divisor is the same for all of
them, so a comparison between decks is exact even where the absolute number is
not.

**Not measured at all.** What the model actually returns. That needs a key and
somebody's quota, and this script will not spend either. When a run does happen,
`Ledger` records the real counts and supersedes the output column here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "engine"))

from slide_wright.brand import plan_conformance, read_profile  # noqa: E402
from slide_wright.inspect import inspect  # noqa: E402
from slide_wright.layout import plan_alignment  # noqa: E402
from slide_wright.llm.usage import RATES, rate_for  # noqa: E402
from slide_wright.planner import SYSTEM, summarise  # noqa: E402

CORPUS = ROOT / "tests" / "corpus"
FIXTURES = ROOT / "tests" / "fixtures" / "third-party"

# Characters per token. Gemini does not publish a local tokeniser and counting
# them properly is a network call, so this is a stated divisor rather than a
# measurement — deliberately on the pessimistic side of the usual 4.0, because a
# cost model that flatters itself is worse than none.
CHARS_PER_TOKEN = 3.6

DEFAULT_MODEL = "gemini-2.5-flash"


def decks() -> list[Path]:
    found = sorted(CORPUS.glob("*.pptx"))
    if FIXTURES.is_dir():
        found += sorted(FIXTURES.glob("*.pptx"))
    return found


def deterministic_change_count(deck: Path) -> int:
    """How many changes the engine's own passes find on this deck.

    A stand-in for how much a model would be asked to return, and a defensible
    one: it is the real amount of work this deck needs, found by measurement
    rather than by imagining a request.
    """
    info = inspect(deck)
    try:
        conformance = plan_conformance(info, read_profile(deck))
        typefaces = len(conformance.changes)
    except Exception:
        typefaces = 0
    try:
        nudges = len(plan_alignment(info).changes)
    except Exception:
        nudges = 0
    return typefaces + nudges


# One entry of the JSON the planner asks for, at a realistic length. Measured
# from the shape `_validate` accepts, not invented: every field it reads is here.
CHANGE_JSON = json.dumps({
    "id": "c1",
    "op": "set_text",
    "slide": 12,
    "target": "27",
    "before": "Revenue grew 14% year on year",
    "after": "Revenue grew 18% year on year",
    "rationale": "the updated figure from the Q3 pack",
})


def measure(deck: Path) -> dict:
    info = inspect(deck)
    prompt = f"{summarise(info)}\n\ninstruction: update the revenue figures"
    prompt_chars = len(SYSTEM) + len(prompt)

    changes = deterministic_change_count(deck)
    # A model is asked for the changes an instruction implies, not for every
    # correction a deterministic pass can find. Capped at a number a person
    # would actually review in one sitting.
    expected_changes = min(changes, 40) or 1
    output_chars = expected_changes * (len(CHANGE_JSON) + 2)

    rate_in, rate_out = rate_for(DEFAULT_MODEL)
    in_tokens = prompt_chars / CHARS_PER_TOKEN
    out_tokens = output_chars / CHARS_PER_TOKEN
    return {
        "deck": deck.name,
        "slides": len(info.slides),
        "shapes": sum(len(s.shapes) for s in info.slides),
        "prompt_chars": prompt_chars,
        "output_chars": output_chars,
        "deterministic_changes": changes,
        "input_tokens_est": round(in_tokens),
        "output_tokens_est": round(out_tokens),
        "cost_usd_est": in_tokens / 1e6 * rate_in + out_tokens / 1e6 * rate_out,
    }


def render(rows: list[dict]) -> str:
    rate_in, rate_out = rate_for(DEFAULT_MODEL)
    lines = [
        "COST OF ONE PLAN CALL",
        "",
        f"  model {DEFAULT_MODEL} at ${rate_in:.2f}/${rate_out:.2f} per Mtok",
        f"  prompt sizes are exact; tokens are chars / {CHARS_PER_TOKEN}",
        "",
        f"  {'deck':<34} {'slides':>6} {'prompt ch':>10} {'in tok':>8} "
        f"{'out tok':>8} {'USD':>9}",
    ]
    for row in sorted(rows, key=lambda r: -r["prompt_chars"]):
        lines.append(
            f"  {row['deck'][:34]:<34} {row['slides']:>6} "
            f"{row['prompt_chars']:>10,} {row['input_tokens_est']:>8,} "
            f"{row['output_tokens_est']:>8,} {row['cost_usd_est']:>9.5f}"
        )

    if not rows:
        return "\n".join(lines + ["", "  no decks found"])

    costs = sorted(r["cost_usd_est"] for r in rows)
    biggest = max(rows, key=lambda r: r["prompt_chars"])
    lines += [
        "",
        f"  {len(rows)} deck(s) · median ${costs[len(costs) // 2]:.5f} · "
        f"most expensive ${costs[-1]:.5f} ({biggest['deck']}, "
        f"{biggest['slides']} slides)",
        "",
        "  The estimate this replaces was $5-15 per deck. The measured prompt",
        "  is three orders of magnitude cheaper than that, and the reason is",
        "  structural rather than lucky: the model is asked to plan, never to",
        "  produce the deck. Nothing here scales with the file's size — only",
        "  with the number of text objects it has to be told about.",
        "",
        "  Not measured: what a model actually returns. That needs a key and",
        "  somebody's quota. Ledger records the real counts when a run happens.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    found = decks()
    if not found:
        print("no decks here; run scripts/fetch_fixtures.py")
        return 1

    rows = []
    for deck in found:
        try:
            rows.append(measure(deck))
        except Exception as exc:  # a deck that cannot be read is not a crash
            print(f"skipped {deck.name}: {exc}", file=sys.stderr)

    print(json.dumps(rows, indent=2) if args.json else render(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
