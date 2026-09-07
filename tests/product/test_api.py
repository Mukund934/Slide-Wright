"""The local API, exercised through the loop it exists to serve.

The tests that matter here are not "does the route return 200". They are:

  · nothing is written before someone approves it;
  · a rejected change stays rejected all the way to the file;
  · a blocked verification is reported, not raised away;
  · the client is never handed a deck the engine refused to deliver.
"""

from __future__ import annotations

import json
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
    with TestClient(app) as test_client:
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
        document = open_document(client)
        slide, target = table_target(document)
        client.post(
            f"/api/documents/{document['id']}/propose",
            json={"sets": [{"slide": slide, "target": target,
                            "op": "set_table_cell", "after": "11.8x"}]},
        )
        # Approved, then applied once; the second apply has nothing left to do.
        client.post(f"/api/documents/{document['id']}/review", json={"approve_all": True})
        client.post(f"/api/documents/{document['id']}/apply", json={})

        with client.stream(
            "POST", f"/api/documents/{document['id']}/apply/stream", json={}
        ) as response:
            events = _parse_sse("".join(response.iter_text()))

        assert events[-1][0] == "error"
        assert events[-1][1]["message"]


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
        with TestClient(app) as test_client:
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
        with TestClient(app) as test_client:
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
        assert "failed verification" in response.json()["detail"]


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
