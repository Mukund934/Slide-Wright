"""Provider abstraction for the reasoning layer.

Thin on purpose. A heavy abstraction that flattens every provider to a common
denominator costs more than it saves; this exists for three specific reasons:

  · it keeps provider SDK types out of the rest of the codebase, so switching
    is an edit in one directory rather than a refactor;
  · it is a **security control** — deck content is confidential, and being able
    to move providers if retention terms change is part of the mitigation;
  · it makes the planner testable without credentials or network access.

No provider SDK is imported at module import time. A machine with no API key
can still run the whole loop through StubProvider.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class BudgetExceeded(Exception):
    """A job hit its token ceiling. Stop and report; never silently continue."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cached_input_tokens + other.cached_input_tokens,
        )

    def cost_usd(self, in_per_mtok: float, out_per_mtok: float,
                 cache_read_per_mtok: float = 0.0) -> float:
        return (
            self.input_tokens / 1e6 * in_per_mtok
            + self.output_tokens / 1e6 * out_per_mtok
            + self.cached_input_tokens / 1e6 * cache_read_per_mtok
        )


@dataclass
class Completion:
    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    latency_s: float = 0.0

    def json(self) -> Any:
        """Parse the response as JSON, tolerating a fenced code block."""
        text = self.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0]
        return json.loads(text)


@dataclass
class Budget:
    """A hard per-job ceiling. Exceeding it stops the job."""

    max_output_tokens: int = 200_000
    max_calls: int = 40
    spent: Usage = field(default_factory=Usage)
    calls: int = 0

    def charge(self, usage: Usage) -> None:
        self.spent = self.spent + usage
        self.calls += 1
        if self.calls > self.max_calls:
            raise BudgetExceeded(f"exceeded {self.max_calls} model calls")
        if self.spent.output_tokens > self.max_output_tokens:
            raise BudgetExceeded(
                f"exceeded {self.max_output_tokens} output tokens "
                f"({self.spent.output_tokens} spent)"
            )


class Provider(ABC):
    """What the rest of the system is allowed to know about a model."""

    name: str = "provider"

    @abstractmethod
    def complete(self, system: str, prompt: str, *, max_tokens: int = 8192) -> Completion:
        ...


class StubProvider(Provider):
    """A scripted provider for tests and offline development.

    Not a mock in the pejorative sense — it makes the loop runnable end to end
    on a machine with no credentials, which is how the deterministic layers get
    exercised in CI.
    """

    name = "stub"

    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses or [])
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str, *, max_tokens: int = 8192) -> Completion:
        self.prompts.append((system, prompt))
        text = self.responses.pop(0) if self.responses else "[]"
        return Completion(
            text=text,
            usage=Usage(input_tokens=len(prompt) // 4, output_tokens=len(text) // 4),
            model="stub",
        )


class AnthropicProvider(Provider):
    """Claude, via the official SDK.

    Imported lazily so the package works with no SDK and no key installed.
    """

    name = "anthropic"

    def __init__(self, model: str = "claude-opus-5", api_key: str | None = None):
        self.model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            try:
                import anthropic  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover - depends on environment
                raise RuntimeError(
                    "the anthropic SDK is not installed; "
                    "use StubProvider or `pip install anthropic`"
                ) from exc
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def complete(self, system: str, prompt: str, *, max_tokens: int = 8192) -> Completion:  # pragma: no cover - needs network
        client = self._ensure_client()
        message = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "adaptive"},
        )
        text = "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )
        usage = Usage(
            input_tokens=getattr(message.usage, "input_tokens", 0),
            output_tokens=getattr(message.usage, "output_tokens", 0),
            cached_input_tokens=getattr(message.usage, "cache_read_input_tokens", 0) or 0,
        )
        return Completion(text=text, usage=usage, model=self.model)


def load_dotenv(path: str = ".env") -> None:
    """Read a local .env into the environment. Never logs a value."""
    import pathlib

    file = pathlib.Path(path)
    if not file.is_file():
        return
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if value and name not in os.environ:
            os.environ[name] = value


def default_provider() -> Provider:
    """Gemini when a key is present, then Anthropic, else the offline stub.

    Gemini is first because it has the free tier this project develops on.
    Falling back rather than failing means the loop is always runnable; the
    planner reports which provider produced a change set, so a stub result is
    never mistaken for a real one.
    """
    load_dotenv()
    if os.environ.get("GEMINI_API_KEY"):
        from slide_wright.llm.gemini import GeminiProvider

        return GeminiProvider()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicProvider()
    return StubProvider()
