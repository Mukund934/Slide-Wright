"""The local API, exercised through the loop it exists to serve.

The tests that matter here are not "does the route return 200". They are:

  · nothing is written before someone approves it;
  · a rejected change stays rejected all the way to the file;
  · a blocked verification is reported, not raised away;
  · the client is never handed a deck the engine refused to deliver.
"""

from __future__ import annotations

import json

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
