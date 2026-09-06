"""The Gemini provider.

Every test here runs offline. A provider whose tests need network and a live
quota is a provider whose tests get skipped, and a skipped test protects
nothing.

The behaviour under most scrutiny is the error path: an API key must never
reach a log line, an exception message, or a terminal someone screenshots.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from slide_wright.llm.client import Provider
from slide_wright.llm.gemini import (
    DEFAULT_MODELS,
    GeminiError,
    GeminiProvider,
    RateLimited,
    _retry_after,
    _scrub,
)

FAKE_KEY = "AQ.Ab8RN6FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE"


def ok_payload(text="[]", prompt_tokens=100, out_tokens=20) -> dict:
    return {
        "candidates": [
            {"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": {
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": out_tokens,
        },
    }


class FakeHTTP:
    """Stands in for urlopen. Records requests; never touches the network."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Response(json.dumps(item).encode())


class _Response:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code: int, body: str) -> urllib.error.HTTPError:
    import io

    return urllib.error.HTTPError(
        "https://generativelanguage.googleapis.com/v1beta/models/x:generateContent",
        code, "err", {}, io.BytesIO(body.encode()),
    )


@pytest.fixture
def provider():
    return GeminiProvider(api_key=FAKE_KEY)


@pytest.fixture
def pinned():
    """A provider with the model fixed, so fallback does not consume responses.

    Used by tests about error handling, where trying four models would just
    exhaust the fake and obscure what is actually under test.
    """
    return GeminiProvider(model="gemini-flash-latest", api_key=FAKE_KEY)


class TestInterface:
    def test_is_a_provider(self, provider):
        assert isinstance(provider, Provider)
        assert provider.name == "gemini"

    def test_reports_configuration_state(self):
        assert GeminiProvider(api_key=FAKE_KEY).configured
        assert not GeminiProvider(api_key="").configured

    def test_unconfigured_call_explains_the_fix(self):
        with pytest.raises(GeminiError, match="GEMINI_API_KEY"):
            GeminiProvider(api_key="").complete("s", "p")


class TestCompletion:
    def test_returns_text_and_usage(self, provider, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen",
                            FakeHTTP([ok_payload('[{"op":"set_text"}]', 120, 30)]))
        result = provider.complete("system", "prompt")
        assert result.text == '[{"op":"set_text"}]'
        assert result.usage.input_tokens == 120
        assert result.usage.output_tokens == 30
        assert result.latency_s >= 0

    def test_parses_json_response(self, provider, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", FakeHTTP([ok_payload('[{"a":1}]')]))
        assert provider.complete("s", "p").json() == [{"a": 1}]

    def test_sends_the_key_as_a_header_never_in_the_url(self, provider, monkeypatch):
        fake = FakeHTTP([ok_payload()])
        monkeypatch.setattr("urllib.request.urlopen", fake)
        provider.complete("s", "p")
        request = fake.requests[0]
        assert request.get_header("X-goog-api-key") == FAKE_KEY
        assert FAKE_KEY not in request.full_url

    def test_system_instruction_is_sent_separately(self, provider, monkeypatch):
        fake = FakeHTTP([ok_payload()])
        monkeypatch.setattr("urllib.request.urlopen", fake)
        provider.complete("be terse", "the prompt")
        body = json.loads(fake.requests[0].data)
        assert body["systemInstruction"]["parts"][0]["text"] == "be terse"
        assert body["contents"][0]["parts"][0]["text"] == "the prompt"

    def test_requests_json_and_low_temperature(self, provider, monkeypatch):
        """Planning someone's deck edit should be repeatable, not creative."""
        fake = FakeHTTP([ok_payload()])
        monkeypatch.setattr("urllib.request.urlopen", fake)
        provider.complete("s", "p")
        config = json.loads(fake.requests[0].data)["generationConfig"]
        assert config["responseMimeType"] == "application/json"
        assert config["temperature"] <= 0.3


class TestModelFallback:
    """Version-pinned model names disappear; the product must not."""

    def test_latest_aliases_are_tried_first(self):
        assert DEFAULT_MODELS[0].endswith("-latest")

    def test_falls_through_a_404_to_the_next_model(self, provider, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", FakeHTTP([
            http_error(404, '{"error":{"message":"model not found"}}'),
            ok_payload("[]"),
        ]))
        result = provider.complete("s", "p")
        assert result.model == DEFAULT_MODELS[1]

    def test_remembers_the_model_that_worked(self, provider, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", FakeHTTP([
            http_error(404, "gone"), ok_payload(), ok_payload(),
        ]))
        provider.complete("s", "p")
        fake = FakeHTTP([ok_payload()])
        monkeypatch.setattr("urllib.request.urlopen", fake)
        provider.complete("s", "p")
        assert DEFAULT_MODELS[1] in fake.requests[0].full_url

    def test_an_explicit_model_is_not_second_guessed(self, monkeypatch):
        provider = GeminiProvider(model="gemini-custom", api_key=FAKE_KEY)
        monkeypatch.setattr("urllib.request.urlopen",
                            FakeHTTP([http_error(404, "nope")]))
        with pytest.raises(GeminiError):
            provider.complete("s", "p")

    def test_exhausting_every_model_raises(self, provider, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen",
                            FakeHTTP([http_error(404, "x")] * len(DEFAULT_MODELS)))
        with pytest.raises(GeminiError, match="no usable Gemini model"):
            provider.complete("s", "p")


class TestRateLimits:
    """Free-tier quota is an expected condition, not an exception."""

    def test_429_raises_rate_limited(self, provider, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen",
                            FakeHTTP([http_error(429, '{"error":{"message":"quota"}}')]))
        with pytest.raises(RateLimited):
            provider.complete("s", "p")

    def test_quota_does_not_fall_through_to_another_model(self, provider, monkeypatch):
        """Trying model B on model A's quota error just burns the quota twice."""
        fake = FakeHTTP([http_error(429, "quota"), ok_payload()])
        monkeypatch.setattr("urllib.request.urlopen", fake)
        with pytest.raises(RateLimited):
            provider.complete("s", "p")
        assert len(fake.requests) == 1

    def test_retry_delay_is_extracted(self):
        assert _retry_after('{"retryDelay":"37s"}') == 37.0
        assert _retry_after("no delay here") == 0.0

    def test_retry_delay_reaches_the_caller(self, provider, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen",
                            FakeHTTP([http_error(429, '{"retryDelay":"12s"}')]))
        with pytest.raises(RateLimited) as caught:
            provider.complete("s", "p")
        assert caught.value.retry_after == 12.0


class TestSecretHygiene:
    """An API key must never survive into anything a human can read."""

    def test_scrub_removes_key_shaped_strings(self):
        assert "AQ.Ab8RN6" not in _scrub("failed for key AQ.Ab8RN6abcdefghijklmnop")
        assert "AIzaSy" not in _scrub("bad key AIzaSyABCDEFGHIJKLMNOPQRSTUVWX")
        assert "[redacted]" in _scrub("key AQ.Ab8RN6abcdefghijklmnop rejected")

    def test_scrub_leaves_ordinary_text_alone(self):
        assert _scrub("model not found") == "model not found"

    def test_http_error_body_is_scrubbed(self, pinned, monkeypatch):
        leaky = f'{{"error":{{"message":"invalid key {FAKE_KEY}"}}}}'
        monkeypatch.setattr("urllib.request.urlopen", FakeHTTP([http_error(400, leaky)]))
        with pytest.raises(GeminiError) as caught:
            pinned.complete("s", "p")
        assert FAKE_KEY not in str(caught.value)

    def test_auth_failure_never_echoes_the_key(self, pinned, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen",
                            FakeHTTP([http_error(403, f"denied {FAKE_KEY}")]))
        with pytest.raises(GeminiError) as caught:
            pinned.complete("s", "p")
        assert FAKE_KEY not in str(caught.value)
        assert "GEMINI_API_KEY" in str(caught.value)


class TestFailureModes:
    def test_no_candidate_raises(self, pinned, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen",
                            FakeHTTP([{"promptFeedback": {"blockReason": "SAFETY"}}]))
        with pytest.raises(GeminiError, match="SAFETY"):
            pinned.complete("s", "p")

    def test_truncated_output_is_an_error_not_a_partial_plan(self, pinned, monkeypatch):
        """A half-parsed change set is more dangerous than no change set."""
        monkeypatch.setattr("urllib.request.urlopen", FakeHTTP([{
            "candidates": [{"content": {"parts": [{"text": '[{"op":'}]},
                            "finishReason": "MAX_TOKENS"}],
        }]))
        with pytest.raises(GeminiError, match="truncated"):
            pinned.complete("s", "p")

    def test_unreachable_host_is_reported_clearly(self, pinned, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen",
                            FakeHTTP([urllib.error.URLError("no route to host")]))
        with pytest.raises(GeminiError, match="could not reach Gemini"):
            pinned.complete("s", "p")


class TestProviderSelection:
    def test_gemini_is_preferred_when_a_key_is_present(self, monkeypatch):
        from slide_wright.llm import client

        monkeypatch.setattr(client, "load_dotenv", lambda *a, **k: None)
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        assert client.default_provider().name == "gemini"

    def test_falls_back_to_the_offline_stub(self, monkeypatch):
        from slide_wright.llm import client

        monkeypatch.setattr(client, "load_dotenv", lambda *a, **k: None)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert client.default_provider().name == "stub"


class TestDotenv:
    def test_reads_values_without_overriding_the_environment(self, tmp_path, monkeypatch):
        from slide_wright.llm.client import load_dotenv

        env = tmp_path / ".env"
        env.write_text('GEMINI_API_KEY="from-file"\nOTHER=x\n', encoding="utf-8")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("OTHER", "already-set")
        load_dotenv(str(env))
        import os

        assert os.environ["GEMINI_API_KEY"] == "from-file"
        assert os.environ["OTHER"] == "already-set"

    def test_ignores_comments_and_blank_values(self, tmp_path):
        from slide_wright.llm.client import load_dotenv

        env = tmp_path / ".env"
        env.write_text("# a comment\nEMPTY=\n\n", encoding="utf-8")
        load_dotenv(str(env))  # must not raise

    def test_missing_file_is_not_an_error(self, tmp_path):
        from slide_wright.llm.client import load_dotenv

        load_dotenv(str(tmp_path / "absent"))
