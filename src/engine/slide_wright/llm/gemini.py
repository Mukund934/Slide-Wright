"""Gemini, behind the existing provider interface.

Chosen as the first live provider because it has a usable free tier, and this
project runs at zero cost until there is evidence worth spending on.

Two deliberate choices:

  · **stdlib HTTP, no SDK.** `urllib` is enough for one POST, and adding a
    dependency to send one JSON body is a cost with no return. It also keeps
    the engine installable anywhere without a vendor package.

  · **the key is never an argument that gets logged.** It is read from the
    environment and sent in a header. It never appears in a prompt, a log line,
    a repr, or an exception message — the error path scrubs it explicitly,
    because a 400 that echoes the request URL is a classic way to leak a key
    into a terminal someone screenshots.

Free-tier rate limits are expected, not exceptional. A 429 raises
`RateLimited` carrying the retry delay so the caller can back off, batch, or
fall back to the offline stub rather than escalating to paid billing.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from slide_wright.llm.client import (
    Completion,
    Provider,
    ProviderError,
    ProviderRateLimited,
    Usage,
)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

# Tried in order; the first that answers is used.
#
# The `-latest` aliases come first deliberately. Version-pinned names go away:
# measured 6 Sep 2026, `gemini-2.5-flash` and `gemini-2.0-flash` both returned
# HTTP 404 for a key whose project could list 40 models, while
# `gemini-flash-latest` answered normally. Pinning a number here would have
# meant the product broke on someone else's release schedule.
#
# Numbered names are kept as a tail so an explicitly-configured model still
# resolves if an alias is ever withdrawn.
DEFAULT_MODELS = (
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
)


class GeminiError(ProviderError):
    """A Gemini call failed. Never carries the API key.

    Kept as a name because the messages are Gemini's and say so, but it *is* a
    `ProviderError` -- so a caller translating failures does not have to know
    which provider is configured.
    """


class RateLimited(GeminiError, ProviderRateLimited):
    """Free-tier quota reached. Back off, batch, or use the offline path."""

    def __init__(self, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.retry_after = retry_after


class GeminiProvider(Provider):
    """Google Gemini via the REST API."""

    name = "gemini"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        timeout: int = 120,
    ):
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = model or DEFAULT_MODELS[0]
        self.timeout = timeout
        self._resolved = bool(model)  # an explicit model is taken on trust

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def complete(self, system: str, prompt: str, *, max_tokens: int = 8192) -> Completion:
        if not self.configured:
            raise GeminiError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and fill it in, "
                "or use StubProvider for offline work."
            )

        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                # Deterministic-leaning: this model plans edits to someone's
                # deck, and we want the same input to propose the same change.
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        candidates = (self.model,) if self._resolved else DEFAULT_MODELS
        last: Exception | None = None
        for model in candidates:
            try:
                started = time.monotonic()
                payload = self._post(f"models/{model}:generateContent", body)
                elapsed = time.monotonic() - started
                self.model, self._resolved = model, True
                return _to_completion(payload, model, elapsed)
            except RateLimited:
                raise  # quota is not a reason to try another model
            except GeminiError as exc:
                last = exc
                continue
        raise GeminiError(f"no usable Gemini model; last error: {last}")

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _post(self, path: str, body: dict) -> dict:
        request = urllib.request.Request(
            f"{API_ROOT}/{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key,  # header, never the URL
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = _scrub(exc.read().decode("utf-8", errors="replace")[:400])
            if exc.code == 429:
                raise RateLimited(
                    f"Gemini free-tier quota reached (HTTP 429): {detail}",
                    retry_after=_retry_after(detail),
                ) from None
            if exc.code in (401, 403):
                raise GeminiError(
                    f"Gemini rejected the credential (HTTP {exc.code}). "
                    "Check GEMINI_API_KEY is current and the API is enabled."
                ) from None
            raise GeminiError(f"Gemini HTTP {exc.code}: {detail}") from None
        except urllib.error.URLError as exc:
            raise GeminiError(f"could not reach Gemini: {exc.reason}") from None
        except TimeoutError:
            raise GeminiError(f"Gemini timed out after {self.timeout}s") from None

    def list_models(self) -> list[str]:
        """Models this key can actually call. Useful when a default 404s."""
        if not self.configured:
            raise GeminiError("GEMINI_API_KEY is not set")
        request = urllib.request.Request(
            f"{API_ROOT}/models",
            headers={"x-goog-api-key": self._api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise GeminiError(f"Gemini HTTP {exc.code} listing models") from None
        return [
            m.get("name", "").removeprefix("models/")
            for m in data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]


def _to_completion(payload: dict, model: str, elapsed: float) -> Completion:
    candidates = payload.get("candidates") or []
    if not candidates:
        blocked = (payload.get("promptFeedback") or {}).get("blockReason")
        raise GeminiError(f"Gemini returned no candidate{f' ({blocked})' if blocked else ''}")

    first = candidates[0]
    if first.get("finishReason") == "MAX_TOKENS":
        raise GeminiError("Gemini hit the output cap; the response is truncated")

    text = "".join(
        part.get("text", "")
        for part in (first.get("content") or {}).get("parts") or []
    )

    meta = payload.get("usageMetadata") or {}
    usage = Usage(
        input_tokens=meta.get("promptTokenCount", 0),
        output_tokens=meta.get("candidatesTokenCount", 0),
        cached_input_tokens=meta.get("cachedContentTokenCount", 0) or 0,
    )
    completion = Completion(text=text, usage=usage, model=model)
    completion.latency_s = elapsed  # attached for the usage ledger
    return completion


def _scrub(text: str) -> str:
    """Remove anything key-shaped from an error before it reaches a log."""
    import re

    return re.sub(r"(AQ\.|AIza)[A-Za-z0-9_\-]{10,}", "[redacted]", text)


def _retry_after(detail: str) -> float:
    import re

    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', detail)
    return float(m.group(1)) if m else 0.0
