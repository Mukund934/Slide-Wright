"""The engine's wire types and the client's must describe the same thing.

`types.ts` is maintained by hand, which is a recorded debt: generating it needs a
schema step in both toolchains, and while the surface was still moving that step
would have cost more than the drift. What the debt actually needs is not a
generator but a *creditor* — something that notices when the two sides diverge.

That is this file. It compares the fields of every pydantic wire model against
the TypeScript interface that mirrors it, and fails on either half being ahead.

The failure it exists to catch is quiet in the worst way. A field renamed in
Python and not in TypeScript does not break a build: `undefined` flows into the
client and renders as an empty count, a missing citation, or a verdict that
looks like `false`. On a surface whose whole job is reporting whether a deck is
safe, a silently absent field is a wrong answer delivered confidently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import BaseModel

import slide_wright_api.contracts as contracts

TYPES = Path(__file__).resolve().parents[2] / "src" / "product" / "web" / "src" / "api" / "types.ts"

# Every wire model, against the interface that mirrors it. A model added without
# an entry here fails `test_every_wire_model_is_accounted_for` below, so the map
# cannot quietly fall behind either.
MIRRORS = {
    "RunOut": "Run",
    "ShapeOut": "Shape",
    "SlideOut": "Slide",
    "DeckOut": "Deck",
    "ChangeOut": "Change",
    "LockOut": "Lock",
    "ChangeSetOut": "ChangeSet",
    "FindingOut": "Finding",
    "GateOut": "Gate",
    "ObservationOut": "Observation",
    "AuditOut": "Audit",
    "CensusOut": "CensusRow",
    "RequestedOut": "RequestedChange",
    "VerificationOut": "Verification",
    "VersionOut": "Version",
    "DocumentOut": "SlideDocument",
    "MatchOut": "Match",
    "RefreshPlanOut": "RefreshPlan",
    "TidyPlanOut": "TidyPlan",
    "SetSpec": "SetSpec",
    "LockSpec": "LockSpec",
}

# Requests the client builds inline rather than typing. Listed so that "no
# interface" is a recorded decision rather than an oversight.
NO_INTERFACE = {
    "OpenRequest", "ProposeRequest", "ReviewRequest", "ApplyRequest",
    "RevertRequest", "ExportRequest", "TidyRequest", "RefreshRequest",
}


def wire_models() -> dict[str, type[BaseModel]]:
    return {
        name: value
        for name in dir(contracts)
        if isinstance(value := getattr(contracts, name), type)
        and issubclass(value, BaseModel)
        and value is not BaseModel
    }


def interface_fields(source: str, name: str) -> set[str] | None:
    match = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.S)
    if match is None:
        return None
    return set(re.findall(r"^\s*(\w+)\??:", match.group(1), re.M))


@pytest.fixture(scope="module")
def types_source() -> str:
    assert TYPES.is_file(), f"the client's types are missing: {TYPES}"
    return TYPES.read_text(encoding="utf-8")


@pytest.mark.parametrize("model_name,interface", sorted(MIRRORS.items()))
def test_the_two_sides_describe_the_same_fields(model_name, interface, types_source):
    model = wire_models().get(model_name)
    assert model is not None, f"{model_name} is in MIRRORS but not in contracts.py"

    fields = interface_fields(types_source, interface)
    assert fields is not None, f"types.ts has no interface {interface}"

    python_only = set(model.model_fields) - fields
    typescript_only = fields - set(model.model_fields)

    assert not python_only, (
        f"{model_name} sends {sorted(python_only)} and {interface} does not declare it. "
        "The client will read undefined, which renders as an empty count or a "
        "verdict that looks like false."
    )
    assert not typescript_only, (
        f"{interface} declares {sorted(typescript_only)} and {model_name} never "
        "sends it. The client is reading a field that is always undefined."
    )


def test_every_wire_model_is_accounted_for():
    """A new model must be mirrored or explicitly excused, never merely absent."""
    unaccounted = set(wire_models()) - set(MIRRORS) - NO_INTERFACE
    assert not unaccounted, (
        f"these wire models are neither mirrored nor listed as request-only: "
        f"{sorted(unaccounted)}"
    )


def test_the_excused_ones_really_are_requests():
    """Guards the excuse itself.

    `NO_INTERFACE` exists because the client builds those bodies inline. If a
    *response* model were added to that set it would escape the check entirely,
    so the name has to earn the exemption.
    """
    for name in NO_INTERFACE:
        assert name.endswith("Request"), (
            f"{name} is excused from having a TypeScript interface, but only "
            "request bodies may be — a response model must be mirrored"
        )
