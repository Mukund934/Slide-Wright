"""ADR-0008: the document does not leave the machine.

The claim is that every deterministic capability runs with no network at all,
and that a deployment which cannot reach the internet still has a working
product. A claim like that decays silently — someone adds a font download, a
version check, a telemetry ping, and nothing fails visibly because the
developer's machine has a network.

So the network is removed and the whole deterministic pipeline is run against
it. Any socket attempt raises, and the test fails naming what tried to connect.
"""

from __future__ import annotations

import socket

import pytest

from slide_wright.apply import apply_changes
from slide_wright.audit import audit
from slide_wright.brand import check_conformance, read_profile
from slide_wright.changeset import Change, ChangeSet, Op
from slide_wright.diff import diff
from slide_wright.fidelity import compare
from slide_wright.gate import check
from slide_wright.inspect import inspect
from slide_wright.package import Package
from slide_wright.session import Session


class NetworkUsed(AssertionError):
    """Raised the moment anything tries to open a connection."""


@pytest.fixture
def no_network(monkeypatch):
    """Make every outbound connection impossible, not merely unlikely."""

    def refuse(*args, **kwargs):
        raise NetworkUsed(
            "the deterministic engine attempted a network connection; "
            "ADR-0008 requires it to work with the network unplugged"
        )

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    return refuse


class TestDeterministicEngineIsOffline:
    """Each capability ADR-0008 names, exercised with no network available."""

    def test_reading_a_package_is_offline(self, no_network, adversarial_deck):
        assert Package.open(adversarial_deck).part_count > 0

    def test_inspect_is_offline(self, no_network, adversarial_deck):
        assert inspect(adversarial_deck).slide_count > 0

    def test_gate_is_offline(self, no_network, adversarial_deck):
        assert check(inspect(adversarial_deck)) is not None

    def test_audit_is_offline(self, no_network, adversarial_deck):
        assert audit(inspect(adversarial_deck), "deck.pptx").render()

    def test_brand_conformance_is_offline(self, no_network, adversarial_deck):
        profile = read_profile(adversarial_deck)
        assert check_conformance(inspect(adversarial_deck), profile, "deck.pptx")

    def test_apply_and_verify_are_offline(self, no_network, adversarial_deck, tmp_path):
        table = next(s for s in inspect(adversarial_deck).all_shapes() if s.kind == "table")
        cs = ChangeSet(deck=str(adversarial_deck))
        cs.add(Change(id="c1", op=Op.SET_TABLE_CELL, slide=3,
                      target=f"{table.id}/r1/c1", before="9.4x", after="11.8x"))
        cs.approve_all()

        out = tmp_path / "out.pptx"
        assert apply_changes(adversarial_deck, cs, out).ok
        assert compare(adversarial_deck, out).fidelity_score > 90

    def test_diff_is_offline(self, no_network, adversarial_deck, tmp_path):
        import shutil

        copy = tmp_path / "copy.pptx"
        shutil.copy(adversarial_deck, copy)
        assert not diff(adversarial_deck, copy).changed

    def test_the_whole_session_loop_is_offline(self, no_network, adversarial_deck, tmp_path):
        """propose -> apply -> verify -> revert, with the network unplugged."""
        session = Session.open(adversarial_deck, workspace=tmp_path / "ws")
        table = next(s for s in session.deck().all_shapes() if s.kind == "table")

        cs = session.propose("update the multiple")
        cs.add(Change(id="c1", op=Op.SET_TABLE_CELL, slide=3,
                      target=f"{table.id}/r1/c1", before="9.4x", after="11.8x"))
        cs.approve_all()

        report = session.apply("update the multiple")
        assert report.deliverable
        assert session.current.number == 1

        session.rollback(0)
        assert session.current.number == 0
        assert session.export(tmp_path / "back.pptx").read_bytes() == \
            adversarial_deck.read_bytes()


class TestTheGuardItself:
    """A test that cannot fail proves nothing."""

    def test_the_network_block_actually_blocks(self, no_network):
        with pytest.raises(NetworkUsed):
            socket.create_connection(("example.com", 80))

    def test_it_blocks_name_resolution_too(self, no_network):
        with pytest.raises(NetworkUsed):
            socket.getaddrinfo("example.com", 80)


class TestNoImplicitUpload:
    """ADR-0008: no telemetry, no crash reporting, no sample collection.

    Checked by reading the engine's own source rather than by running it, since
    a pathway that only fires on an unhandled exception would not show up in any
    normal run.
    """

    def test_the_engine_contains_no_outbound_calls_outside_the_provider(self):
        """Scanned as code, not as text.

        The first version grepped the raw file and fired on a docstring
        containing the words "a server handling two requests." -- `requests.`
        with a full stop after it. That is the shape of guard that teaches
        people to reword their comments, and once a check can be satisfied by
        editing prose it stops meaning anything about the code.

        So comments and string literals are stripped before the scan. What it
        catches is what it is for: someone adding a font download, a version
        check, a telemetry ping. It is not defending against an author who
        wants to hide one, and could not.
        """
        import io
        import tokenize
        from pathlib import Path

        engine = Path(__file__).resolve().parents[2] / "src" / "engine" / "slide_wright"
        forbidden = ("requests.", "urlopen", "urllib.request", "http.client",
                     "httpx.", "socket.create_connection")

        def code_only(source: str) -> str:
            kept = []
            try:
                for token in tokenize.generate_tokens(io.StringIO(source).readline):
                    if token.type in (tokenize.COMMENT, tokenize.STRING):
                        continue
                    kept.append(token.string)
            except (tokenize.TokenError, IndentationError):
                return source  # unparseable: scan it whole rather than skip it
            return " ".join(kept)

        offenders = []
        for path in engine.rglob("*.py"):
            # The provider layer is the one sanctioned network boundary, and it
            # is optional: with no key configured nothing there is reached.
            if path.parent.name == "llm":
                continue
            text = code_only(path.read_text(encoding="utf-8", errors="ignore"))
            for token in forbidden:
                if token in text:
                    offenders.append(f"{path.relative_to(engine)}: {token}")

        assert not offenders, (
            "outbound network code outside the provider layer: " + "; ".join(offenders)
        )

    def test_that_scan_still_catches_a_real_call(self, tmp_path):
        """Stripping comments must not have stripped the teeth.

        Verified against a planted call rather than trusted: the tokeniser
        joins tokens with spaces, so `urllib.request` becomes `urllib . request`
        unless the check accounts for it.
        """
        import io
        import tokenize

        planted = (
            "import urllib.request\n"
            "\n"
            "def fetch(url):\n"
            "    return urllib.request.urlopen(url).read()\n"
        )
        kept = []
        for token in tokenize.generate_tokens(io.StringIO(planted).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
        scanned = " ".join(kept)
        assert any(t in scanned for t in ("urlopen", "urllib.request", "urllib . request")), (
            "the scan would not notice a real outbound call"
        )
