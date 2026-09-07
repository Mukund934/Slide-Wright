"""What a plan call costs, and the property that makes the answer stable.

`usage.py` carried a complaint about this project for weeks: that it was holding
a "$5–15 per deck" estimate unverified, and that "estimates that never get
checked become facts by repetition". The check found it wrong by three orders of
magnitude.

The number itself is not what these tests protect — it will move. What they
protect is the reason it is small, which is a structural property and can be
broken by an ordinary-looking change: **the prompt describes the deck's text
objects, and nothing else.** The moment something puts part bytes, an image, or
a slide's full XML into it, cost starts tracking file size and every figure
published about it becomes wrong at once.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from slide_wright.inspect import inspect
from slide_wright.planner import SYSTEM, summarise

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from cost_report import CHARS_PER_TOKEN, measure  # noqa: E402


class TestTheCostDoesNotTrackFileSize:
    """A 55 MB deck and a 12 MB deck cost about the same to plan against."""

    def test_the_prompt_holds_no_part_bytes(self, adversarial_deck):
        """The property, stated as a size bound rather than as a promise.

        The adversarial fixture carries charts, embedded workbooks, media and
        diagrams. If any of them reached the prompt it would be larger than the
        file, not a fraction of it.
        """
        prompt = summarise(inspect(adversarial_deck))
        assert len(prompt) < adversarial_deck.stat().st_size / 10

    def test_no_media_bytes_leak_into_it(self, adversarial_deck):
        prompt = summarise(inspect(adversarial_deck))
        for marker in ("\x89PNG", "JFIF", "base64", "PK\x03\x04"):
            assert marker not in prompt

    def test_a_real_deck_is_priced_in_thousandths_of_a_dollar(self, adversarial_deck):
        row = measure(adversarial_deck)
        assert row["cost_usd_est"] < 0.05, (
            "a plan call costing more than five cents means something now scales "
            "with the deck rather than with its text"
        )

    def test_the_measurement_is_deterministic(self, adversarial_deck):
        """It has to be, or the published figure is a sample rather than a fact."""
        assert measure(adversarial_deck) == measure(adversarial_deck)


class TestTheEstimateSaysWhichHalfIsMeasured:
    def test_the_character_count_is_exact(self, adversarial_deck):
        row = measure(adversarial_deck)
        info = inspect(adversarial_deck)
        prompt = f"{summarise(info)}\n\ninstruction: update the revenue figures"
        assert row["prompt_chars"] == len(SYSTEM) + len(prompt)

    def test_tokens_are_derived_from_it_by_a_stated_divisor(self, adversarial_deck):
        """Not a hidden model of a tokeniser nobody can inspect."""
        row = measure(adversarial_deck)
        assert row["input_tokens_est"] == round(row["prompt_chars"] / CHARS_PER_TOKEN)

    def test_the_divisor_is_on_the_pessimistic_side(self):
        """A cost model that flatters itself is worse than none.

        The usual rule of thumb is 4 characters per token; anything above that
        would make the published figure smaller than reality.
        """
        assert CHARS_PER_TOKEN <= 4.0


class TestItReportsOnEveryDeckItCanRead:
    def test_a_deck_that_cannot_be_read_is_not_a_crash(self, tmp_path):
        broken = tmp_path / "broken.pptx"
        broken.write_bytes(b"not a zip")
        with pytest.raises(Exception):
            measure(broken)  # the caller catches this; it must not pass silently
