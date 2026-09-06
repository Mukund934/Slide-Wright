"""A ledger of every model call.

This project develops at zero cost on a free tier. That is a deliberate
constraint, and a constraint you cannot measure is one you will breach without
noticing — so every model call is recorded: provider, model, tokens, latency,
and what it cost at published rates.

Two things this makes answerable that guesswork cannot:

  · **"can we afford this at scale?"** — we can price a deck edit from real
    measurements instead of the $5–15 estimate we have been carrying unverified;
  · **"are we near the free-tier ceiling?"** — request counts per day, before a
    429 tells us the hard way.

The ledger is append-only JSONL under `private/` — it records usage patterns
over a customer's decks, which is not something to publish.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from slide_wright.llm.client import Completion, Usage

# Published rates, USD per million tokens. Free tiers cost nothing, but a
# *rate* is still needed to answer "what would this cost at scale?" — which is
# the question that decides whether the product's unit economics work.
RATES: dict[str, tuple[float, float]] = {
    # model prefix: (input, output)
    "gemini-flash-lite": (0.10, 0.40),
    "gemini-flash": (0.30, 2.50),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-pro": (1.25, 10.00),
    "claude-opus": (5.00, 25.00),
    "claude-sonnet": (2.00, 10.00),
    "claude-haiku": (1.00, 5.00),
    "stub": (0.0, 0.0),
}

FREE_TIER_NOTE = (
    "Gemini free tier bills nothing. Costs below are what the same calls would "
    "cost at published paid rates — the number that matters for unit economics."
)


def rate_for(model: str) -> tuple[float, float]:
    """Longest-prefix match, so a new point release inherits its family's rate."""
    best, rate = 0, (0.0, 0.0)
    for prefix, value in RATES.items():
        if model.startswith(prefix) and len(prefix) > best:
            best, rate = len(prefix), value
    return rate


@dataclass
class Entry:
    """One model call."""

    operation: str          # "plan" | "critique" | "audit" | …
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    latency_s: float = 0.0
    deck: str = ""
    ok: bool = True
    error: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def notional_cost_usd(self) -> float:
        """What this call would cost at published paid rates."""
        rate_in, rate_out = rate_for(self.model)
        return self.input_tokens / 1e6 * rate_in + self.output_tokens / 1e6 * rate_out


@dataclass
class Ledger:
    """Append-only record of model usage."""

    path: Path | None = None
    entries: list[Entry] = field(default_factory=list)

    @classmethod
    def open(cls, path: str | Path = "private/operations/usage.jsonl") -> Ledger:
        path = Path(path)
        ledger = cls(path=path)
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        ledger.entries.append(Entry(**json.loads(line)))
                    except (json.JSONDecodeError, TypeError):
                        continue  # a corrupt line must not lose the rest
        return ledger

    def record(
        self,
        operation: str,
        provider: str,
        completion: Completion | None = None,
        *,
        deck: str = "",
        usage: Usage | None = None,
        model: str = "",
        ok: bool = True,
        error: str = "",
    ) -> Entry:
        used = usage or (completion.usage if completion else Usage())
        entry = Entry(
            operation=operation,
            provider=provider,
            model=model or (completion.model if completion else ""),
            input_tokens=used.input_tokens,
            output_tokens=used.output_tokens,
            cached_input_tokens=used.cached_input_tokens,
            latency_s=(completion.latency_s if completion else 0.0),
            deck=Path(deck).name if deck else "",
            ok=ok,
            error=error[:200],
        )
        self.entries.append(entry)
        self._append(entry)
        return entry

    def _append(self, entry: Entry) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(entry)) + "\n")

    # ── analysis ─────────────────────────────────────────────────────────────

    @property
    def calls(self) -> int:
        return len(self.entries)

    @property
    def failures(self) -> list[Entry]:
        return [e for e in self.entries if not e.ok]

    def total_tokens(self) -> int:
        return sum(e.total_tokens for e in self.entries)

    def notional_cost_usd(self) -> float:
        return sum(e.notional_cost_usd for e in self.entries)

    def by_operation(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for entry in self.entries:
            row = out.setdefault(
                entry.operation,
                {"calls": 0, "input": 0, "output": 0, "cost": 0.0, "latency": 0.0, "failures": 0},
            )
            row["calls"] += 1
            row["input"] += entry.input_tokens
            row["output"] += entry.output_tokens
            row["cost"] += entry.notional_cost_usd
            row["latency"] += entry.latency_s
            row["failures"] += 0 if entry.ok else 1
        return out

    def cost_per_deck(self) -> float:
        """Notional cost divided by distinct decks touched.

        This is the number that decides whether the product's unit economics
        work. Until it is measured it stays an estimate, and estimates that
        never get checked become facts by repetition.
        """
        decks = {e.deck for e in self.entries if e.deck}
        return self.notional_cost_usd() / len(decks) if decks else 0.0

    def render(self) -> str:
        if not self.entries:
            return "USAGE LEDGER — no model calls recorded."

        lines = [
            "USAGE LEDGER",
            "",
            f"  {self.calls} call(s) · {self.total_tokens():,} tokens · "
            f"{len(self.failures)} failure(s)",
            f"  notional cost at paid rates: ${self.notional_cost_usd():.4f}",
        ]
        decks = {e.deck for e in self.entries if e.deck}
        if decks:
            lines.append(
                f"  {len(decks)} deck(s) touched · ${self.cost_per_deck():.4f} per deck"
            )
        lines += ["", "  by operation", ""]
        for name, row in sorted(self.by_operation().items()):
            avg = row["latency"] / row["calls"] if row["calls"] else 0.0
            lines.append(
                f"    {name:<12} {row['calls']:>4} call(s)  "
                f"in {row['input']:>7,}  out {row['output']:>7,}  "
                f"${row['cost']:.4f}  {avg:.1f}s avg"
                + (f"  {row['failures']} failed" if row["failures"] else "")
            )
        lines += ["", f"  {FREE_TIER_NOTE}"]
        return "\n".join(lines)
