"""The local API, exercised through the loop it exists to serve.

The tests that matter here are not "does the route return 200". They are:

  · nothing is written before someone approves it;
  · a rejected change stays rejected all the way to the file;
  · a blocked verification is reported, not raised away;
  · the client is never handed a deck the engine refused to deliver.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from slide_wright.inspect import inspect
from slide_wright_api.app import create_app
from slide_wright_api.workspace import Workspace


@pytest.fixture
def client(tmp_path, adversarial_deck):
    """A client over a deck copied into tmp, so versions land in tmp too."""
    app = create_app(workspace=Workspace(), serve_client=False)
    with TestClient(app, base_url="http://127.0.0.1:8787") as test_client:
        test_client.deck = _stage(adversarial_deck, tmp_path)
        yield test_client


def _stage(deck, tmp_path):
    staged = tmp_path / deck.name
    staged.write_bytes(deck.read_bytes())
    return staged


def open_document(client) -> dict:
    response = client.post("/api/documents", json={"path": str(client.deck)})
    assert response.status_code == 200, response.text
    return response.json()


def table_target(document) -> tuple[int, str]:
    for slide in document["deck"]["slides"]:
        for shape in slide["shapes"]:
            if shape["kind"] == "table":
                return slide["number"], f"{shape['id']}/r1/c1"
    raise AssertionError("the adversarial deck should contain a table")


class TestHealth:
    def test_reports_what_this_install_can_do(self, client):
        body = client.get("/api/health").json()
        assert body["ok"] is True
        assert body["engine"]
        # Absent a key this is the stub, and saying so is the point of the field.
        assert isinstance(body["model_configured"], bool)


class TestOpening:
    def test_opens_a_deck_and_returns_its_structure(self, client):
        document = open_document(client)
        assert document["name"] == client.deck.name
        assert document["deck"]["slides"], "a deck with no slides is not a deck"
        assert document["deck"]["slide_width"] > 0
        assert document["versions"][0]["is_original"]
        assert document["versions"][0]["is_current"]

    def test_the_same_path_reopens_the_same_document(self, client):
        first, second = open_document(client), open_document(client)
        assert first["id"] == second["id"], (
            "two sessions over one deck would each own its version numbering"
        )

    def test_a_missing_file_is_explained_not_500(self, client, tmp_path):
        response = client.post("/api/documents", json={"path": str(tmp_path / "nope.pptx")})
        assert response.status_code == 422
        assert "no file at" in response.json()["detail"]

    def test_a_non_deck_is_refused_with_a_reason(self, client, tmp_path):
        other = tmp_path / "notes.txt"
        other.write_text("not a deck")
        response = client.post("/api/documents", json={"path": str(other)})
        assert response.status_code == 422
        assert "PowerPoint" in response.json()["detail"]

    def test_an_unknown_document_is_404_with_an_explanation(self, client):
        response = client.get("/api/documents/deadbeef")
        assert response.status_code == 404
        assert "open the file again" in response.json()["detail"]


class TestProposeWritesNothing:
    """The defining property. If this ever fails, the product is a lie."""

    def test_proposing_leaves_the_deck_byte_identical(self, client):
        document = open_document(client)
        before = client.deck.read_bytes()
        slide, target = table_target(document)

        response = client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": target,
                            "op": "set_table_cell", "after": "11.8x"}]},
        )
        assert response.status_code == 200, response.text
        assert client.deck.read_bytes() == before

    def test_a_proposed_change_is_not_yet_approved(self, client):
        document = open_document(client)
        slide, target = table_target(document)
        body = client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": target,
                            "op": "set_table_cell", "after": "11.8x"}]},
        ).json()

        assert body["proposed_count"] == 1
        assert body["approved_count"] == 0

    def test_before_is_read_from_the_deck_not_taken_from_the_client(self, client):
        """A stale tab must fail at propose time, where it can be explained."""
        document = open_document(client)
        slide = document["deck"]["slides"][0]["number"]
        shape = next(
            s for s in document["deck"]["slides"][0]["shapes"] if s["text"].strip()
        )
        body = client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": shape["id"],
                            "op": "set_text", "after": "replaced"}]},
        ).json()

        assert body["changes"][0]["before"] == shape["text"]

    def test_an_edit_to_something_that_does_not_exist_is_refused(self, client):
        document = open_document(client)
        response = client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": 1, "target": "9999",
                            "op": "set_text", "after": "x"}]},
        )
        assert response.status_code == 422
        assert "no object" in response.json()["detail"]

    def test_proposing_nothing_is_an_error_not_an_empty_change_set(self, client):
        document = open_document(client)
        response = client.post(f"/api/documents/{document['id']}/propose", json={})
        assert response.status_code == 422


class TestLocks:
    def test_a_lock_rejects_the_change_it_forbids(self, client):
        document = open_document(client)
        slide, target = table_target(document)
        body = client.post(
            f"/api/documents/{document['id']}/propose",
            json={
                "locks": [{"scope": "numbers", "reason": "partner signed these off"}],
                "sets": [{"slide": slide, "target": target,
                          "op": "set_table_cell", "after": "11.8x"}],
            },
        ).json()

        assert body["rejected_count"] == 1
        assert "numbers lock" in body["changes"][0]["rationale"]

    def test_an_unknown_lock_scope_is_refused(self, client):
        document = open_document(client)
        response = client.post(
            f"/api/documents/{document['id']}/propose",
            json={"locks": [{"scope": "vibes"}]},
        )
        assert response.status_code == 422
        assert "unknown protection scope" in response.json()["detail"]


class TestReview:
    def test_approving_by_id_moves_only_that_change(self, client):
        document = open_document(client)
        slide, target = table_target(document)
        proposed = client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [
                {"slide": slide, "target": target, "op": "set_table_cell", "after": "11.8x"},
                {"slide": slide, "target": target, "op": "set_table_cell", "after": "12.0x"},
            ]},
        ).json()
        first = proposed["changes"][0]["id"]

        body = client.post(
            f"/api/documents/{document['id']}/review", json={"approve": [first]}
        ).json()

        assert body["approved_count"] == 1
        assert body["proposed_count"] == 1

    def test_an_unknown_id_is_an_error_not_a_silent_no_op(self, client):
        """A reviewer must never believe they rejected something they did not."""
        document = open_document(client)
        slide, target = table_target(document)
        client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": target,
                            "op": "set_table_cell", "after": "11.8x"}]},
        )
        response = client.post(
            f"/api/documents/{document['id']}/review", json={"reject": ["nope"]}
        )
        assert response.status_code == 422
        assert "no such change" in response.json()["detail"]

    def test_review_before_propose_is_refused(self, client):
        document = open_document(client)
        response = client.post(
            f"/api/documents/{document['id']}/review", json={"approve_all": True}
        )
        assert response.status_code == 422


class TestApply:
    def test_the_full_loop_commits_a_verified_version(self, client):
        document = open_document(client)
        slide, target = table_target(document)
        client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": target,
                            "op": "set_table_cell", "after": "11.8x"}]},
        )
        client.post(f"/api/documents/{document['id']}/review", json={"approve_all": True})

        verification = client.post(
            f"/api/documents/{document['id']}/apply", json={"note": "update the multiple"}
        ).json()

        assert verification["deliverable"] is True
        assert verification["blocking_reasons"] == []
        assert verification["fidelity_score"] > 90
        assert verification["changed_slides"] == [slide]

        after = client.get(f"/api/documents/{document['id']}").json()
        assert after["versions"][-1]["number"] == 1
        assert after["versions"][-1]["is_current"]

    def test_a_rejected_change_never_reaches_the_file(self, client):
        document = open_document(client)
        slide, target = table_target(document)
        proposed = client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [
                {"slide": slide, "target": target, "op": "set_table_cell", "after": "11.8x"},
                {"slide": slide, "target": target, "op": "set_table_cell", "after": "99.9x"},
            ]},
        ).json()
        keep, drop = (c["id"] for c in proposed["changes"])

        client.post(
            f"/api/documents/{document['id']}/review",
            json={"approve": [keep], "reject": [drop]},
        )
        client.post(f"/api/documents/{document['id']}/apply", json={})

        current = client.get(f"/api/documents/{document['id']}").json()
        text = "".join(
            shape["text"]
            for s in current["deck"]["slides"] for shape in s["shapes"]
        )
        assert "99.9x" not in text

    def test_applying_with_nothing_approved_is_refused(self, client):
        document = open_document(client)
        slide, target = table_target(document)
        client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": target,
                            "op": "set_table_cell", "after": "11.8x"}]},
        )
        response = client.post(f"/api/documents/{document['id']}/apply", json={})
        assert response.status_code == 422
        assert "no approved changes" in response.json()["detail"]

    def test_verification_names_what_the_user_asked_for(self, client):
        document = open_document(client)
        slide, target = table_target(document)
        client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": target,
                            "op": "set_table_cell", "after": "11.8x"}]},
        )
        client.post(f"/api/documents/{document['id']}/review", json={"approve_all": True})
        verification = client.post(
            f"/api/documents/{document['id']}/apply", json={}
        ).json()

        assert verification["requested"], "the report must list what was asked"
        assert verification["requested"][0]["slide"] == slide
        assert any(row["intact"] for row in verification["census"])


class TestApplyStream:
    def test_narrates_every_stage_then_the_verdict(self, client):
        document = open_document(client)
        slide, target = table_target(document)
        client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": target,
                            "op": "set_table_cell", "after": "11.8x"}]},
        )
        client.post(f"/api/documents/{document['id']}/review", json={"approve_all": True})

        with client.stream(
            "POST", f"/api/documents/{document['id']}/apply/stream", json={}
        ) as response:
            assert response.status_code == 200
            events = _parse_sse("".join(response.iter_text()))

        stages = [payload["stage"] for kind, payload in events if kind == "stage"]
        assert stages == ["applying", "applied", "verifying", "verified"]

        kind, result = events[-1]
        assert kind == "result"
        assert result["deliverable"] is True

    def test_a_refusal_arrives_as_an_error_event_not_a_dropped_stream(self, client):
        """A stream that just stops is indistinguishable from a lost connection.

        The refusal has to arrive *as an event*, so a client can tell "the
        engine said no" from "the network went away" -- one of those is worth
        retrying and the other never is.
        """
        document = open_document(client)
        slide, _ = table_target(document)
        # A change set that exists but holds nothing approvable: the propose is
        # rejected, and the empty set is what the apply then refuses.
        client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": "9999",
                            "op": "set_text", "after": "x"}]},
        )
        client.post(f"/api/documents/{document['id']}/review", json={"approve_all": True})

        with client.stream(
            "POST", f"/api/documents/{document['id']}/apply/stream", json={}
        ) as response:
            assert response.status_code == 200
            events = _parse_sse("".join(response.iter_text()))

        assert events[-1][0] == "error"
        assert events[-1][1]["message"]

    def test_applying_twice_needs_a_new_proposal(self, client):
        """A change set describes the version it was built against.

        Once applied it describes the *previous* version, and every `before` in
        it was read from a file that is no longer current. Reusing it wrote
        coordinates computed against the wrong version and still reported
        VERIFIED, because the slide was in the change set.
        """
        document = open_document(client)
        slide, target = table_target(document)
        client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": target,
                            "op": "set_table_cell", "after": "11.8x"}]},
        )
        client.post(f"/api/documents/{document['id']}/review", json={"approve_all": True})
        assert client.post(f"/api/documents/{document['id']}/apply", json={}).status_code == 200

        again = client.post(f"/api/documents/{document['id']}/apply", json={})
        assert again.status_code == 422
        assert "proposed" in again.json()["detail"]


class TestHistoryAndRevert:
    def test_history_lists_every_version_with_the_current_one_marked(self, client):
        document = open_document(client)
        slide, target = table_target(document)
        client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": target,
                            "op": "set_table_cell", "after": "11.8x"}]},
        )
        client.post(f"/api/documents/{document['id']}/review", json={"approve_all": True})
        client.post(f"/api/documents/{document['id']}/apply", json={"note": "the edit"})

        versions = client.get(f"/api/documents/{document['id']}/history").json()
        assert [v["number"] for v in versions] == [0, 1]
        assert versions[-1]["is_current"]
        assert versions[-1]["note"] == "the edit"
        assert versions[0]["is_original"]

    def test_reverting_returns_the_earlier_version(self, client):
        document = open_document(client)
        slide, target = table_target(document)
        client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": target,
                            "op": "set_table_cell", "after": "11.8x"}]},
        )
        client.post(f"/api/documents/{document['id']}/review", json={"approve_all": True})
        client.post(f"/api/documents/{document['id']}/apply", json={})

        reverted = client.post(
            f"/api/documents/{document['id']}/revert", json={"to": 0}
        ).json()

        assert reverted["versions"][-1]["number"] == 0
        assert reverted["changeset"] is None, "reverting clears the change set"

    def test_reverting_to_a_version_that_does_not_exist_says_which_do(self, client):
        document = open_document(client)
        response = client.post(
            f"/api/documents/{document['id']}/revert", json={"to": 99}
        )
        assert response.status_code == 422
        assert "no version 99" in response.json()["detail"]


class TestDiff:
    def test_names_what_a_reader_would_notice(self, client):
        document = open_document(client)
        slide, target = table_target(document)
        client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": target,
                            "op": "set_table_cell", "after": "11.8x"}]},
        )
        client.post(f"/api/documents/{document['id']}/review", json={"approve_all": True})
        client.post(f"/api/documents/{document['id']}/apply", json={})

        body = client.get(f"/api/documents/{document['id']}/diff?source=0").json()
        assert body["changed"] is True
        assert body["deltas"]
        assert any(d["is_content"] for d in body["deltas"])

    def test_an_unknown_version_says_which_exist(self, client):
        document = open_document(client)
        response = client.get(f"/api/documents/{document['id']}/diff?source=42")
        assert response.status_code == 404
        assert "no version 42" in response.json()["detail"]


class TestExport:
    def test_writes_the_current_version_where_asked(self, client, tmp_path):
        document = open_document(client)
        destination = tmp_path / "out" / "final.pptx"

        body = client.post(
            f"/api/documents/{document['id']}/export",
            json={"destination": str(destination)},
        ).json()

        assert destination.is_file()
        assert body["path"] == str(destination)
        assert inspect(destination).slides


class TestAudit:
    def test_returns_findings_the_client_can_group(self, client):
        document = open_document(client)
        body = client.get(f"/api/documents/{document['id']}/audit").json()
        assert isinstance(body["passed"], bool)
        for finding in body["findings"]:
            assert finding["severity"] in ("error", "warning")
            assert finding["message"]


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    for block in raw.strip().split("\n\n"):
        if not block.strip():
            continue
        kind, data = "", "{}"
        for line in block.splitlines():
            if line.startswith("event: "):
                kind = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        events.append((kind, json.loads(data)))
    return events


class TestTheEngineDoesNotDependOnTheProduct:
    """The dependency runs one way, and CI is arranged to prove it.

    The engine job installs `src/engine` alone. If anything under
    `slide_wright` ever imported `slide_wright_api`, that job would start
    failing on an import and the reason would be a packaging error rather than
    the architectural fact it is meant to catch. Asserting it here keeps the
    statement where someone reading the product code can see it.
    """

    def test_no_engine_module_imports_the_product(self):
        from pathlib import Path

        engine = Path(__file__).resolve().parents[2] / "src" / "engine" / "slide_wright"
        offenders = [
            str(path.relative_to(engine))
            for path in engine.rglob("*.py")
            if "slide_wright_api" in path.read_text(encoding="utf-8")
        ]
        assert not offenders, f"the engine imports the product surface: {offenders}"


class TestAudit:
    """What is wrong with this deck, asked of one nobody has touched yet."""

    def test_returns_the_structural_audit_not_only_the_gate(self, client):
        """`Session.audit()` is the gate, which answers a different question.

        The gate asks "may this be delivered", about an edit that already
        happened. Someone opening an inherited deck is asking the other one, and
        for a while this route answered the wrong question with a straight face.
        """
        document = open_document(client)
        body = client.get(f"/api/documents/{document['id']}/audit").json()

        assert body["slide_count"] > 0
        assert body["word_count"] > 0
        assert "observations" in body
        assert "gate" in body, "the gate is carried alongside, not instead"

    def test_every_observation_carries_where_and_why(self, client):
        document = open_document(client)
        for observation in client.get(f"/api/documents/{document['id']}/audit").json()[
            "observations"
        ]:
            assert observation["message"]
            assert observation["where"]
            assert observation["area"]
            assert observation["severity"] in ("error", "warning")

    def test_automatable_is_the_engine_s_answer_not_a_guess(self, client):
        """`is_automatable` must agree with the remedy the engine attached.

        A surface that decided for itself which findings are fixable would
        eventually offer a fix the engine cannot perform.
        """
        document = open_document(client)
        body = client.get(f"/api/documents/{document['id']}/audit").json()
        for observation in body["observations"]:
            assert observation["is_automatable"] == bool(observation["remedy"])
        assert body["automatable_count"] == sum(
            1 for o in body["observations"] if o["is_automatable"]
        )

    def test_reading_the_audit_writes_nothing(self, client):
        document = open_document(client)
        before = client.deck.read_bytes()
        client.get(f"/api/documents/{document['id']}/audit")
        assert client.deck.read_bytes() == before


class TestTidy:
    """The wedge as one action: change presentation, change no content, prove it.

    Run against `untidy_deck` rather than the adversarial one, which happens to
    be immaculate in exactly these two dimensions. Written against it, every
    test here skipped — and a test that skips is a test that is not run.
    """

    @pytest.fixture
    def client(self, tmp_path, untidy_deck):
        app = create_app(workspace=Workspace(), serve_client=False)
        with TestClient(app, base_url="http://127.0.0.1:8787") as test_client:
            test_client.deck = _stage(untidy_deck, tmp_path)
            yield test_client

    def test_the_plan_is_bounded_by_its_own_tolerance(self, client):
        """Alignment may only move a shape onto a line its neighbours share.

        Showing both numbers is what makes "nothing here is visible" checkable
        rather than asserted.
        """
        document = open_document(client)
        plan = client.get(f"/api/documents/{document['id']}/tidy").json()
        assert plan["worst_shift_in"] <= plan["tolerance_in"]
        assert plan["conforms_to"]

    def test_planning_writes_nothing(self, client):
        document = open_document(client)
        before = client.deck.read_bytes()
        client.get(f"/api/documents/{document['id']}/tidy")
        assert client.deck.read_bytes() == before

    def test_a_proposal_approves_nothing_by_itself(self, client):
        """The CLI approves its own tidy; here consent must still precede it.

        A button that silently proposed and applied would be the one place in
        this product where mutation happens without a person saying yes.
        """
        document = open_document(client)
        response = client.post(f"/api/documents/{document['id']}/tidy", json={})
        assert response.status_code == 200, response.text

        body = response.json()
        assert body["changes"], "the untidy deck must have something to tidy"
        assert body["approved_count"] == 0
        assert body["applied_count"] == 0
        assert body["proposed_count"] == len(body["changes"])

    def test_a_tidy_proposes_only_presentational_changes(self, client):
        """The promise inverted: every pixel of formatting, not one word."""
        document = open_document(client)
        response = client.post(f"/api/documents/{document['id']}/tidy", json={})
        assert response.status_code == 200, response.text

        presentational = {"set_font", "set_color", "set_font_size", "move", "resize"}
        for change in response.json()["changes"]:
            assert change["op"] in presentational, f"{change['op']} changes content"

    def test_the_content_guarantee_is_a_lock_not_an_afterthought(self, client):
        """Declared before any change is added, so the engine refuses one.

        The CLI proves this afterwards by diffing the written file. A lock is
        stronger: a content-changing op is rejected the moment it is added,
        rather than caught once the bytes are on disk.
        """
        document = open_document(client)
        response = client.post(f"/api/documents/{document['id']}/tidy", json={})
        assert response.status_code == 200, response.text

        assert "wording" in {lock["scope"] for lock in response.json()["locks"]}

    def test_a_users_own_locks_are_honoured_too(self, client):
        document = open_document(client)
        response = client.post(
            f"/api/documents/{document['id']}/tidy",
            json={"locks": [{"scope": "layout", "reason": "nothing may move"}]},
        )
        assert response.status_code == 200, response.text

        body = response.json()
        assert "layout" in {lock["scope"] for lock in body["locks"]}
        # A layout lock forbids moves, so any alignment nudge must arrive rejected.
        moves = [c for c in body["changes"] if c["op"] == "move"]
        assert all(c["status"] == "rejected" for c in moves)

    def test_a_deck_with_nothing_to_tidy_says_so(self, client, tmp_path, minimal_deck):
        staged = tmp_path / "clean.pptx"
        staged.write_bytes(minimal_deck.read_bytes())
        document = client.post("/api/documents", json={"path": str(staged)}).json()

        response = client.post(f"/api/documents/{document['id']}/tidy", json={})
        assert response.status_code == 422
        assert "nothing to tidy" in response.json()["detail"]

    def test_a_tidy_applied_end_to_end_changes_no_word_or_number(
        self, client, untidy_deck
    ):
        """The wedge's whole promise, proved on a written file.

        The lock stops a content op being added and verification stops an
        unattributed part changing, but neither of those is the claim. The claim
        is that after tidying, every word and number a reader would notice is
        exactly where it was — so it is checked by reading the text back out.
        """
        from slide_wright.diff import diff

        document = open_document(client)
        before_text = _all_text(document)

        client.post(f"/api/documents/{document['id']}/tidy", json={})
        client.post(f"/api/documents/{document['id']}/review", json={"approve_all": True})
        verification = client.post(
            f"/api/documents/{document['id']}/apply", json={"note": "tidy"}
        ).json()

        assert verification["deliverable"] is True, verification["blocking_reasons"]

        after = client.get(f"/api/documents/{document['id']}").json()
        assert _all_text(after) == before_text, "tidying altered what the deck says"

        # And the engine's own reader agrees: presentation moved, content did not.
        edited = sorted(Path(after["workspace"]).glob("v0*-edited.pptx"))[-1]
        result = diff(untidy_deck, edited)
        assert result.deltas, "a tidy that changed nothing proves nothing"
        assert not result.content_deltas, [d.description for d in result.content_deltas]


def _all_text(document: dict) -> list[str]:
    return [
        shape["text"]
        for slide in document["deck"]["slides"]
        for shape in slide["shapes"]
    ]


class TestDeckAtAVersion:
    """Comparing two versions needs both of them readable, not just the current one.

    A before/after that reconstructed "before" by subtracting the diff from
    "after" would be a second implementation of the diff, and the two would
    eventually disagree about the one thing they exist to agree on.
    """

    def _edit(self, client, document) -> None:
        slide, target = table_target(document)
        client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": target,
                            "op": "set_table_cell", "after": "11.8x"}]},
        )
        client.post(f"/api/documents/{document['id']}/review", json={"approve_all": True})
        client.post(f"/api/documents/{document['id']}/apply", json={})

    def test_defaults_to_the_current_version(self, client):
        document = open_document(client)
        body = client.get(f"/api/documents/{document['id']}/deck").json()
        assert body["slides"] == document["deck"]["slides"]

    def test_an_earlier_version_still_reads_as_it_was(self, client):
        document = open_document(client)
        original = document["deck"]
        self._edit(client, document)

        before = client.get(f"/api/documents/{document['id']}/deck?version=0").json()
        after = client.get(f"/api/documents/{document['id']}/deck").json()

        assert before["slides"] == original["slides"], "version 0 must not move"
        assert after["slides"] != original["slides"], "the edit must be visible"

    def test_an_unknown_version_says_which_exist(self, client):
        document = open_document(client)
        response = client.get(f"/api/documents/{document['id']}/deck?version=42")
        assert response.status_code == 404
        assert "no version 42" in response.json()["detail"]

    def test_the_two_sides_of_a_comparison_agree_with_the_diff(self, client):
        """The canvas and the delta list must not tell different stories.

        Every shape the diff names as changed has to actually differ between the
        two decks the canvas draws, or a reviewer sees a ring around something
        that looks identical and stops believing the rings.
        """
        document = open_document(client)
        self._edit(client, document)

        before = client.get(f"/api/documents/{document['id']}/deck?version=0").json()
        after = client.get(f"/api/documents/{document['id']}/deck?version=1").json()
        deltas = client.get(
            f"/api/documents/{document['id']}/diff?source=0&output=1"
        ).json()["deltas"]
        assert deltas, "the edit should have produced at least one delta"

        def shape_of(deck, slide_number, shape_id):
            slide = next(s for s in deck["slides"] if s["number"] == slide_number)
            return next(s for s in slide["shapes"] if s["id"] == shape_id)

        for delta in deltas:
            old = shape_of(before, delta["slide"], delta["shape_id"])
            new = shape_of(after, delta["slide"], delta["shape_id"])
            assert old != new, f"diff names {delta['shape_id']} but the shapes match"


@pytest.fixture
def comps(tmp_path):
    """This quarter's numbers: Alpha's multiple moved, Beta's margin moved.

    Deliberately not a full restatement. The interesting outcomes are the three
    the engine distinguishes — updated, confirmed unchanged, and not found — and
    a source that changed everything would only exercise the first.
    """
    path = tmp_path / "comps.csv"
    path.write_text(
        "Company,EV/EBITDA,Margin\n"
        "Alpha Corp,11.8x,22.1%\n"
        "Beta Industries,11.2x,21.4%\n",
        encoding="utf-8",
    )
    return path


class TestRefresh:
    """Last quarter's deck plus this quarter's numbers.

    The most valuable thing this product does and the most dangerous, because a
    wrong number here is invisible — it looks exactly like a right one. So every
    change must carry the coordinate it came from, and anything the source
    cannot justify must be left alone rather than guessed at.
    """

    def test_the_preview_separates_all_three_outcomes(self, client, comps):
        document = open_document(client)
        plan = client.post(
            f"/api/documents/{document['id']}/refresh/preview",
            json={"sources": [str(comps)]},
        ).json()

        assert plan["sources"] == ["comps.csv"]
        assert plan["updates"], "Alpha's multiple moved and should be offered"
        # Confirmed cells are positive evidence a figure is still right. A tool
        # that reports only what it changed cannot tell "checked and correct"
        # from "never looked at".
        assert isinstance(plan["confirmed"], list)
        assert isinstance(plan["unmatched"], list)

    def test_every_update_carries_the_coordinate_it_came_from(self, client, comps):
        document = open_document(client)
        plan = client.post(
            f"/api/documents/{document['id']}/refresh/preview",
            json={"sources": [str(comps)]},
        ).json()

        for match in plan["updates"]:
            assert match["citation"].startswith("comps.csv!"), match
            assert match["current"] != match["proposed"]

    def test_a_confirmed_cell_proposes_nothing(self, client, comps):
        """Showing an "after" identical to the "before" reads as a change."""
        document = open_document(client)
        plan = client.post(
            f"/api/documents/{document['id']}/refresh/preview",
            json={"sources": [str(comps)]},
        ).json()

        for match in plan["confirmed"]:
            assert match["proposed"] == ""
            assert match["citation"]

    def test_previewing_writes_nothing(self, client, comps):
        document = open_document(client)
        before = client.deck.read_bytes()
        client.post(
            f"/api/documents/{document['id']}/refresh/preview",
            json={"sources": [str(comps)]},
        )
        assert client.deck.read_bytes() == before

    def test_a_proposal_is_grounded_and_approves_nothing(self, client, comps):
        """SOURCE origin: grounded in a coordinate, with no model involved.

        Grounded is not the same as approved. It means the change traces to
        something checkable, which is why it needs no model review — a person
        still has to say yes.
        """
        document = open_document(client)
        body = client.post(
            f"/api/documents/{document['id']}/refresh",
            json={"sources": [str(comps)]},
        ).json()

        assert body["approved_count"] == 0
        assert body["needs_review_count"] == 0
        for change in body["changes"]:
            assert change["origin"] == "source"
            assert change["is_grounded"] is True
            assert change["citation"]

    def test_a_numbers_lock_stops_a_refresh_like_anything_else(self, client, comps):
        """A refresh changes figures, so a numbers lock must refuse all of it."""
        document = open_document(client)
        body = client.post(
            f"/api/documents/{document['id']}/refresh",
            json={
                "sources": [str(comps)],
                "locks": [{"scope": "numbers", "reason": "signed off"}],
            },
        ).json()

        assert body["approved_count"] == 0
        assert all(c["status"] == "rejected" for c in body["changes"])

    def test_a_source_that_explains_nothing_says_what_it_did_find(self, client, tmp_path):
        unrelated = tmp_path / "weather.csv"
        unrelated.write_text("City,Rainfall\nOslo,700mm\n", encoding="utf-8")
        document = open_document(client)

        response = client.post(
            f"/api/documents/{document['id']}/refresh",
            json={"sources": [str(unrelated)]},
        )
        assert response.status_code == 422
        assert "already agree" in response.json()["detail"]

    def test_a_missing_source_fails_by_name_rather_than_silently(self, client, tmp_path):
        """A file that did not load is not a file with no rows.

        Continuing would produce a refresh that looks complete and is missing
        half its evidence.
        """
        document = open_document(client)
        response = client.post(
            f"/api/documents/{document['id']}/refresh/preview",
            json={"sources": [str(tmp_path / "nope.csv")]},
        )
        assert response.status_code == 422
        assert "no file at" in response.json()["detail"]

    def test_naming_no_source_is_refused(self, client):
        document = open_document(client)
        response = client.post(
            f"/api/documents/{document['id']}/refresh/preview", json={"sources": []}
        )
        assert response.status_code == 422
        assert "No source was given" in response.json()["detail"]

    def test_a_file_that_is_not_a_spreadsheet_is_refused_with_a_reason(
        self, client, tmp_path
    ):
        junk = tmp_path / "notes.txt"
        junk.write_text("not a spreadsheet", encoding="utf-8")
        document = open_document(client)

        response = client.post(
            f"/api/documents/{document['id']}/refresh/preview",
            json={"sources": [str(junk)]},
        )
        assert response.status_code == 422
        assert "could not be read" in response.json()["detail"]


class TestTemplateConformance:
    """Conform an inherited deck to a house standard rather than to itself.

    The other real workflow. With no template a deck's own theme is the
    authority, which is right for something assembled from several sources; with
    one, the standard it has to end up matching becomes the authority instead.
    """

    @pytest.fixture
    def client(self, tmp_path, untidy_deck):
        app = create_app(workspace=Workspace(), serve_client=False)
        with TestClient(app, base_url="http://127.0.0.1:8787") as test_client:
            test_client.deck = _stage(untidy_deck, tmp_path)
            yield test_client

    def test_the_decks_own_theme_is_named_as_the_default(self, client):
        document = open_document(client)
        plan = client.get(f"/api/documents/{document['id']}/tidy").json()
        assert plan["conforms_to"] == "the deck's own theme"

    def test_a_template_becomes_the_authority_and_is_named(self, client, adversarial_deck):
        """Naming it matters more than it looks.

        "the deck's own theme" and "House.pptx" produce different changes, and a
        reviewer approving two hundred typeface corrections needs to know which
        standard they are approving.
        """
        document = open_document(client)
        plan = client.get(
            f"/api/documents/{document['id']}/tidy",
            params={"template": str(adversarial_deck)},
        ).json()

        assert plan["conforms_to"] == adversarial_deck.name
        assert plan["conforms_to"] != "the deck's own theme"

    def test_the_template_fonts_are_reported(self, client, adversarial_deck):
        document = open_document(client)
        plan = client.get(
            f"/api/documents/{document['id']}/tidy",
            params={"template": str(adversarial_deck)},
        ).json()
        assert isinstance(plan["fonts"], list)

    def test_every_change_cites_whichever_authority_produced_it(
        self, client, adversarial_deck
    ):
        """A citation naming the wrong file is worse than none.

        The reviewer would check the change against a standard that did not
        produce it, and find it reasonable.
        """
        document = open_document(client)
        body = client.post(
            f"/api/documents/{document['id']}/tidy",
            json={"template": str(adversarial_deck)},
        ).json()

        fonts = [c for c in body["changes"] if c["op"] == "set_font"]
        assert fonts, "the untidy deck hardcodes typefaces and should be corrected"
        for change in fonts:
            assert change["citation"] == adversarial_deck.name

    def test_a_template_that_is_not_a_deck_is_refused(self, client, tmp_path):
        junk = tmp_path / "house.txt"
        junk.write_text("brand guidelines", encoding="utf-8")
        document = open_document(client)

        response = client.get(
            f"/api/documents/{document['id']}/tidy", params={"template": str(junk)}
        )
        assert response.status_code == 422
        assert "not a PowerPoint template" in response.json()["detail"]

    def test_a_missing_template_is_refused_by_name(self, client, tmp_path):
        document = open_document(client)
        response = client.get(
            f"/api/documents/{document['id']}/tidy",
            params={"template": str(tmp_path / "House.potx")},
        )
        assert response.status_code == 422
        assert "no file at" in response.json()["detail"]


class TestExportRefusesTheSideDoor:
    """A deck that failed verification must not leave by any route.

    The engine enforces it; these check that the surface does not quietly
    provide a way around, and that a refusal reads as a verdict about the deck
    rather than a mistake in what the user typed.
    """

    def test_suggests_a_name_beside_the_original(self, client):
        document = open_document(client)
        target = client.get(f"/api/documents/{document['id']}/export").json()

        suggested = Path(target["suggested"])
        assert suggested.parent == client.deck.parent
        assert suggested != client.deck, "the default must never be the original"
        assert suggested.suffix == client.deck.suffix

    def test_says_whether_an_export_is_allowed_before_a_path_is_typed(self, client):
        """Offering the field and rejecting the submission is much worse.

        It makes the refusal look like a typo rather than a verdict.
        """
        document = open_document(client)
        target = client.get(f"/api/documents/{document['id']}/export").json()
        assert target["deliverable"] is True
        assert target["blocking_reasons"] == []

    def test_writes_where_asked(self, client, tmp_path):
        document = open_document(client)
        destination = tmp_path / "out" / "final.pptx"

        body = client.post(
            f"/api/documents/{document['id']}/export",
            json={"destination": str(destination)},
        ).json()

        assert destination.is_file()
        assert body["path"] == str(destination)

    def test_writes_to_the_suggestion_when_no_path_is_given(self, client):
        document = open_document(client)
        body = client.post(f"/api/documents/{document['id']}/export", json={}).json()
        assert Path(body["path"]).is_file()
        assert Path(body["path"]) != client.deck

    def test_refuses_to_overwrite_the_file_that_was_opened(self, client):
        """Every guarantee rests on the original still existing to verify against."""
        document = open_document(client)
        before = client.deck.read_bytes()

        response = client.post(
            f"/api/documents/{document['id']}/export",
            json={"destination": str(client.deck)},
        )

        assert response.status_code == 422
        assert "will not overwrite your original" in response.json()["detail"]
        assert client.deck.read_bytes() == before

    def test_a_blocked_verification_stops_the_export_and_says_why(self, client):
        document = open_document(client)
        slide, target = table_target(document)
        client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": target,
                            "op": "set_table_cell", "after": "11.8x"}]},
        )
        client.post(f"/api/documents/{document['id']}/review", json={"approve_all": True})
        client.post(f"/api/documents/{document['id']}/apply", json={})

        # Force the last report to a blocked verdict, as a native object loss
        # would. The route must consult the engine, not its own memory of it.
        session = client.app.state.workspace.require(document["id"])
        session.last_report.fidelity.output_census.tables = 0

        target_info = client.get(f"/api/documents/{document['id']}/export").json()
        assert target_info["deliverable"] is False
        assert target_info["blocking_reasons"]

        response = client.post(f"/api/documents/{document['id']}/export", json={})
        assert response.status_code == 422
        assert "blocked" in response.json()["detail"]


class TestAWorkspaceThatWentAway:
    """The session workspace lives on disk beside the deck, so it can vanish.

    People delete folders. Sync clients move them. Backup software restores
    them half-formed. A cached session whose files have gone answers every
    question with a path that no longer resolves, and the failure surfaced as a
    500 from deep inside the package reader — which tells the user nothing and
    looks like the product is broken.
    """

    def test_reopening_after_the_workspace_is_deleted_recovers(self, client):
        import shutil

        document = open_document(client)
        workspace = Path(document["workspace"])
        assert workspace.is_dir()

        shutil.rmtree(workspace)

        # Same path, same process, same cached session — and it must work.
        response = client.post("/api/documents", json={"path": str(client.deck)})
        assert response.status_code == 200, response.text
        assert response.json()["deck"]["slides"]
        assert Path(response.json()["workspace"]).is_dir(), "rebuilt from the source"

    def test_the_recovered_session_starts_from_the_original_again(self, client):
        import shutil

        document = open_document(client)
        slide, target = table_target(document)
        client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": target,
                            "op": "set_table_cell", "after": "11.8x"}]},
        )
        client.post(f"/api/documents/{document['id']}/review", json={"approve_all": True})
        client.post(f"/api/documents/{document['id']}/apply", json={})

        shutil.rmtree(Path(document["workspace"]))
        recovered = client.post("/api/documents", json={"path": str(client.deck)}).json()

        # The history went with the folder, which is the truth: those versions
        # are gone. Claiming otherwise would offer a revert that cannot happen.
        assert [v["number"] for v in recovered["versions"]] == [0]
        assert recovered["versions"][0]["is_original"]

    def test_a_deck_deleted_underneath_us_is_explained_not_a_500(self, client, tmp_path):
        staged = tmp_path / "vanishing.pptx"
        staged.write_bytes(client.deck.read_bytes())
        opened = client.post("/api/documents", json={"path": str(staged)}).json()

        import shutil
        shutil.rmtree(Path(opened["workspace"]))
        staged.unlink()

        response = client.post("/api/documents", json={"path": str(staged)})
        assert response.status_code == 422
        assert "no file at" in response.json()["detail"]


class TestItAnswersOnlyToItsOwnMachine:
    """Binding to loopback stops another machine. It does not stop a web page.

    In a DNS rebinding attack, evil.example resolves to 127.0.0.1 once its TTL
    expires, so the browser treats requests to evil.example:8787 as same-origin
    and never consults CORS at all. Every route is then reachable from a page
    the user merely visited: read the deck, drive an edit, write a copy out.

    The Host header is what survives it, because the browser still sends the
    name the page was loaded from. It matters more here than for most local
    servers — the promise is that the document does not leave this machine
    (ADR-0008), and rebinding is precisely a way to make it leave.
    """

    @pytest.fixture
    def app(self, tmp_path):
        from slide_wright_api.app import create_app
        return create_app(workspace=Workspace(), serve_client=False)

    @pytest.mark.parametrize("host", ["127.0.0.1:8787", "localhost:8787",
                                      "localhost", "[::1]:8787", "LOCALHOST"])
    def test_its_own_client_is_served(self, app, host):
        with TestClient(app, base_url="http://127.0.0.1:8787") as client:
            assert client.get("/api/health", headers={"Host": host}).status_code == 200

    @pytest.mark.parametrize("host", ["attacker.example", "deck-stealer.io:8787",
                                      "0.0.0.0:8787", "localhost.evil.example",
                                      "127.0.0.1.evil.example"])
    def test_a_request_addressed_elsewhere_is_refused(self, app, host):
        with TestClient(app, base_url="http://127.0.0.1:8787") as client:
            response = client.get("/api/health", headers={"Host": host})
            assert response.status_code == 421
            assert "localhost" in response.json()["detail"]

    def test_the_refusal_covers_every_route_not_only_the_api(self, app):
        """The attack does not care which URL it lands on."""
        with TestClient(app, base_url="http://127.0.0.1:8787") as client:
            for path in ["/api/documents", "/", "/openapi.json"]:
                assert client.get(
                    path, headers={"Host": "attacker.example"}
                ).status_code == 421

    def test_a_mutating_route_is_refused_before_it_runs(self, app, adversarial_deck):
        with TestClient(app, base_url="http://127.0.0.1:8787") as client:
            response = client.post(
                "/api/documents",
                json={"path": str(adversarial_deck)},
                headers={"Host": "attacker.example"},
            )
            assert response.status_code == 421
            assert client.get("/api/health").json()["open_documents"] == 0


class TestExportingIntoAFolder:
    """The engine refuses a folder because it cannot know the naming convention.
    The API knows it, so it names the file — once, visibly, and using the same
    name the export field offers by default."""

    def test_a_folder_gets_the_suggested_name(self, client, tmp_path):
        document = open_document(client)
        folder = tmp_path / "out"
        folder.mkdir()
        response = client.post(
            f"/api/documents/{document['id']}/export",
            json={"destination": str(folder)},
        )
        assert response.status_code == 200
        written = Path(response.json()["path"])
        assert written.is_file(), "the path it reported has to be the file it wrote"
        assert written.parent == folder

    def test_the_written_name_is_not_the_workspace_s_own(self, client, tmp_path):
        """v000-original.pptx is an internal name; the user never chose it."""
        document = open_document(client)
        folder = tmp_path / "out2"
        folder.mkdir()
        response = client.post(
            f"/api/documents/{document['id']}/export",
            json={"destination": str(folder)},
        )
        assert "original" not in Path(response.json()["path"]).name


class TestWhenTheWorkspaceGoesAwayUnderneath:
    """A workspace lives on disk beside the deck.

    It gets deleted, synced, restored and moved by people and by software that
    knows nothing about this app, so it going away mid-session is ordinary
    rather than exceptional. `_intact` was checked when opening and nowhere
    else, so every other route — read, audit, diff, apply, export — served a
    cached session whose files had gone and the failure came back as a bare
    **500 Internal Server Error** from deep inside the package reader.
    """

    @pytest.fixture
    def opened(self, tmp_path, adversarial_deck):
        from slide_wright_api.app import create_app

        deck = tmp_path / "deck.pptx"
        shutil.copy(adversarial_deck, deck)
        app = create_app(workspace=Workspace(), serve_client=False)
        client = TestClient(app, base_url="http://127.0.0.1:8787",
                            raise_server_exceptions=False)
        document = client.post("/api/documents", json={"path": str(deck)}).json()
        return client, document, deck, Path(document["workspace"])

    def test_a_deleted_workspace_is_rebuilt_rather_than_crashing(self, opened):
        client, document, _, workspace = opened
        shutil.rmtree(workspace, ignore_errors=True)
        response = client.get(f"/api/documents/{document['id']}")
        assert response.status_code == 200
        assert Path(response.json()["workspace"]).is_dir()

    def test_every_route_recovers_not_just_the_first(self, opened):
        client, document, _, workspace = opened
        shutil.rmtree(workspace, ignore_errors=True)
        for route in ("", "/audit", "/history", "/sources"):
            response = client.get(f"/api/documents/{document['id']}{route}")
            assert response.status_code != 500, f"{route or '/'} returned a 500"

    def test_a_version_file_removed_is_recovered_from_the_source(self, opened):
        client, document, _, workspace = opened
        next(workspace.glob("v*.pptx")).unlink()
        assert client.get(f"/api/documents/{document['id']}").status_code == 200

    def test_with_the_source_gone_too_it_says_so(self, opened):
        client, document, deck, workspace = opened
        shutil.rmtree(workspace, ignore_errors=True)
        deck.unlink()
        response = client.get(f"/api/documents/{document['id']}")
        assert response.status_code == 404
        assert "gone from disk" in response.json()["detail"]

    def test_the_second_request_tells_the_same_story_as_the_first(self, opened):
        """A user clicks something else immediately, so the second message is
        the one they act on. Left to the generic path it read "it may have been
        opened by an earlier run of the app" — plausible, and the wrong
        explanation for a file that had just been deleted."""
        client, document, deck, workspace = opened
        shutil.rmtree(workspace, ignore_errors=True)
        deck.unlink()
        first = client.get(f"/api/documents/{document['id']}").json()["detail"]
        second = client.get(f"/api/documents/{document['id']}/audit").json()["detail"]
        assert first == second

    def test_closing_on_purpose_leaves_no_explanation_behind(self, opened):
        """Closing is not a failure, so the next open must not inherit one."""
        client, document, deck, _ = opened
        client.delete(f"/api/documents/{document['id']}")
        reopened = client.post("/api/documents", json={"path": str(deck)})
        assert reopened.status_code == 200


class TestALockMustHaveSomethingToProtect:
    """A lock naming a slide or an object that is not in the deck is refused.

    It cannot block anything, so accepting it shows a user a guarantee they
    asked for, in an interface that says it is held, over a deck where it does
    nothing. That is worse than not offering the lock at all.

    The engine cannot check this — a change set knows its locks and its changes,
    not the deck they refer to — so it is checked at the one layer where both
    are in scope.
    """

    def _propose(self, client, document, lock):
        slide, target = table_target(document)
        return client.post(
            f"/api/documents/{document['id']}/propose",
            json={
                "sets": [{"slide": slide, "target": target,
                          "op": "set_table_cell", "after": "11.8x"}],
                "locks": [lock],
            },
        )

    def test_a_slide_that_is_not_there_is_refused(self, client):
        document = open_document(client)
        response = self._propose(client, document, {"scope": "slide", "target": "999"})
        assert response.status_code == 422
        assert "no slide 999" in response.json()["detail"]

    def test_the_refusal_says_which_slides_there_are(self, client):
        document = open_document(client)
        detail = self._propose(
            client, document, {"scope": "slide", "target": "999"}
        ).json()["detail"]
        assert "this deck has 1" in detail

    def test_an_object_that_is_not_there_is_refused(self, client):
        document = open_document(client)
        response = self._propose(client, document, {"scope": "shape", "target": "9999"})
        assert response.status_code == 422
        assert "no object" in response.json()["detail"]

    def test_a_real_slide_lock_is_accepted(self, client):
        document = open_document(client)
        slide, _ = table_target(document)
        response = self._propose(
            client, document, {"scope": "slide", "target": str(slide)}
        )
        assert response.status_code == 200

    def test_a_deck_wide_lock_needs_no_target(self, client):
        document = open_document(client)
        assert self._propose(client, document, {"scope": "wording"}).status_code == 200

    def test_a_deck_wide_lock_still_blocks(self, client):
        document = open_document(client)
        body = self._propose(client, document, {"scope": "wording"}).json()
        assert all(c["status"] == "rejected" for c in body["changes"])


class TestWhenTheModelProviderFails:
    """The engine writes careful, actionable messages for each of these.

    "Gemini free-tier quota reached", "Check GEMINI_API_KEY is current and the
    API is enabled", "could not reach Gemini" — and every one of them arrived at
    the client as `500 Internal Server Error`. Seven distinct causes, one
    useless answer, and the two a user can actually act on (a quota that resets,
    a key that needs renewing) were indistinguishable from a bug in the product.
    """

    def _failing(self, client, exc, monkeypatch):
        import slide_wright.llm.client as llm

        class Failing(llm.Provider):
            name = "gemini"

            def complete(self, system, prompt):
                raise exc

        monkeypatch.setattr(llm, "default_provider", lambda: Failing())
        document = open_document(client)
        return client.post(
            f"/api/documents/{document['id']}/propose",
            json={"instruction": "make the title shorter"},
        )

    def test_a_quota_limit_is_429_not_500(self, client, monkeypatch):
        """The request was fine and will be fine again. A client that wants to
        back off has to be able to tell that from a failure that will not."""
        from slide_wright.llm.gemini import RateLimited

        response = self._failing(
            client, RateLimited("Gemini free-tier quota reached (HTTP 429)", 41.0),
            monkeypatch,
        )
        assert response.status_code == 429
        assert "quota" in response.json()["detail"]

    def test_it_says_how_long_to_wait(self, client, monkeypatch):
        from slide_wright.llm.gemini import RateLimited

        response = self._failing(
            client, RateLimited("quota reached", 41.0), monkeypatch
        )
        assert "41s" in response.json()["detail"]

    def test_a_rejected_credential_reaches_the_user_verbatim(self, client, monkeypatch):
        """It is the one failure with a fix the user owns."""
        from slide_wright.llm.gemini import GeminiError

        response = self._failing(
            client,
            GeminiError("Gemini rejected the credential (HTTP 401). "
                        "Check GEMINI_API_KEY is current and the API is enabled."),
            monkeypatch,
        )
        assert response.status_code == 502
        assert "GEMINI_API_KEY" in response.json()["detail"]

    def test_an_unreachable_provider_says_so(self, client, monkeypatch):
        from slide_wright.llm.gemini import GeminiError

        response = self._failing(
            client, GeminiError("could not reach Gemini: getaddrinfo failed"),
            monkeypatch,
        )
        assert response.status_code == 502
        assert "could not reach" in response.json()["detail"]

    def test_an_unexpected_provider_bug_is_still_not_a_bare_500(self, client, monkeypatch):
        """A provider is a foreign boundary. Whatever comes out of it, the user
        gets told what happened to their deck — which is nothing."""
        response = self._failing(client, RuntimeError("no attribute 'candidates'"),
                                 monkeypatch)
        assert response.status_code == 502
        detail = response.json()["detail"]
        assert "Nothing was changed" in detail
        assert "RuntimeError" in detail

    def test_the_translation_is_provider_neutral(self):
        """The API must not import a vendor module to classify a failure, or a
        second provider means finding every place the first one is named."""
        from slide_wright.llm.client import ProviderError, ProviderRateLimited
        from slide_wright.llm.gemini import GeminiError, RateLimited

        assert issubclass(GeminiError, ProviderError)
        assert issubclass(RateLimited, ProviderRateLimited)

    def test_nothing_is_written_when_the_provider_fails(self, client, monkeypatch):
        from slide_wright.llm.gemini import GeminiError

        before = client.deck.read_bytes()
        self._failing(client, GeminiError("could not reach Gemini"), monkeypatch)
        assert client.deck.read_bytes() == before


class TestADamagedVersionIsAsBadAsAMissingOne:
    """`is_file()` passed a version file that had been damaged in place.

    Truncated, emptied, or replaced with text — every presence check in the
    product said yes and the failure arrived as a **500** from inside the
    reader, on every route. Present and damaged is the same situation as absent
    and was the one nobody had checked.
    """

    @pytest.fixture
    def opened(self, tmp_path, adversarial_deck):
        from slide_wright_api.app import create_app

        deck = tmp_path / "deck.pptx"
        shutil.copy(adversarial_deck, deck)
        client = TestClient(
            create_app(workspace=Workspace(), serve_client=False),
            base_url="http://127.0.0.1:8787", raise_server_exceptions=False,
        )
        document = client.post("/api/documents", json={"path": str(deck)}).json()
        return client, document, deck, Path(document["workspace"])

    @pytest.mark.parametrize("payload", [b"", b"not a zip", b"PK\x03\x04truncated"])
    def test_it_is_never_a_500(self, opened, payload):
        client, document, _, workspace = opened
        next(workspace.glob("v*.pptx")).write_bytes(payload)
        for route in ("", "/audit", "/history"):
            response = client.get(f"/api/documents/{document['id']}{route}")
            assert response.status_code != 500, f"{route or '/'} returned a 500"

    def test_it_says_which_version_and_what_to_do(self, opened):
        client, document, _, workspace = opened
        next(workspace.glob("v*.pptx")).write_bytes(b"broken")
        detail = client.get(f"/api/documents/{document['id']}").json()["detail"]
        assert "damaged" in detail
        assert "v000-original.pptx" in detail
        assert "the original file is untouched" in detail

    def test_the_workspace_outlives_a_ruined_source(self, opened):
        """The other direction, and it should keep working: the workspace holds
        the original, so the deck being replaced with garbage costs nothing."""
        client, document, deck, _ = opened
        deck.write_bytes(b"nope")
        assert client.get(f"/api/documents/{document['id']}").status_code == 200

    def test_a_healthy_workspace_is_not_slowed_into_uselessness(self, opened):
        """The check runs on every request, so it reads the zip directory and
        decompresses nothing."""
        client, document, _, _ = opened
        for _ in range(20):
            assert client.get(f"/api/documents/{document['id']}").status_code == 200


class TestHowASourceWasReadIsShown:
    """The encoding and delimiter are guesses when the file does not say.

    The engine records which guess it made, and recording it without showing it
    would be pointless — the reader looking at a mangled character is the only
    person the note is for.
    """

    def _preview(self, client, document, path):
        return client.post(
            f"/api/documents/{document['id']}/refresh/preview",
            json={"sources": [str(path)]},
        )

    def test_an_unusual_encoding_and_delimiter_are_named(self, client, tmp_path):
        document = open_document(client)
        odd = tmp_path / "comps.csv"
        odd.write_bytes("Company;Margin\nCaf\u00e9 Ltd;21%\n".encode("cp1252"))
        sources = self._preview(client, document, odd).json()["sources"]
        assert "cp1252" in sources[0]
        assert "semicolon" in sources[0]

    def test_a_plain_utf8_file_is_named_and_nothing_more(self, client, tmp_path):
        """Saying "decoded as utf-8" on every ordinary file is noise, and noise
        is how a note that matters gets skipped."""
        document = open_document(client)
        plain = tmp_path / "clean.csv"
        plain.write_bytes(b"Company,Margin\nAlpha,21%\n")
        sources = self._preview(client, document, plain).json()["sources"]
        assert sources == ["clean.csv"]

    def test_an_excel_csv_from_windows_is_no_longer_refused(self, client, tmp_path):
        """It used to be a 422: "could not read: 'utf-8' codec can't decode byte
        0xe9" — the workbook an analyst emails you, rejected outright."""
        document = open_document(client)
        excel = tmp_path / "from-excel.csv"
        excel.write_bytes("Company,Margin\nZ\u00fcrich AG,18%\n".encode("cp1252"))
        assert self._preview(client, document, excel).status_code == 200
