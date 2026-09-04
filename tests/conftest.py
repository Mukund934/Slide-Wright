"""Shared fixtures.

Corpus decks are generated rather than committed where possible: a generated
deck is reproducible on any machine and carries no confidentiality question.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "engine"))

from slide_wright.corpus.synthesize import build_adversarial, build_minimal  # noqa: E402

CORPUS = REPO / "tests" / "corpus"


@pytest.fixture(scope="session")
def adversarial_deck() -> Path:
    """A deck containing every hard construct we can generate (difficulty 7)."""
    path = CORPUS / "synthetic-adversarial.pptx"
    if not path.is_file():
        build_adversarial(path)
    return path


@pytest.fixture(scope="session")
def minimal_deck() -> Path:
    """The control. Difficulty 0 — passing on this alone proves nothing."""
    path = CORPUS / "synthetic-minimal.pptx"
    if not path.is_file():
        build_minimal(path)
    return path
