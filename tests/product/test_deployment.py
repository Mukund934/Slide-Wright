"""Where this process listens, and who may reach it.

ADR-0011. The rule that matters is not "loopback is the default" -- a default
is a thing that can be overridden. It is that **the local mode cannot be
configured on to a public interface at all**, and that choosing any other mode
is explicit, refuses to start unauthenticated, and says so.

Every refusal below is a refusal the previous design could not have had,
because the previous design expressed the guarantee as a constant plus three
greps. A grep is satisfied by a comment. These are not.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from slide_wright_api.app import create_app
from slide_wright_api.deployment import (
    LOOPBACK_BIND,
    LOOPBACK_HOSTS,
    MIN_TOKEN_LENGTH,
    DeploymentError,
    Mode,
    generate_token,
    resolve,
)
from slide_wright_api.workspace import Workspace

TOKEN = generate_token()
HOSTED = {
    "SLIDE_WRIGHT_MODE": "self-hosted",
    "SLIDE_WRIGHT_TOKEN": TOKEN,
    "SLIDE_WRIGHT_HOSTNAME": "deck.internal.example",
}


class TestNothingConfigured:
    """The case that must not have changed, because it is every current user."""

    def test_it_is_local(self) -> None:
        assert resolve({}).mode is Mode.LOCAL

    def test_it_binds_loopback(self) -> None:
        assert resolve({}).bind == LOOPBACK_BIND

    def test_it_has_no_token_to_check(self) -> None:
        here = resolve({})
        assert here.token is None
        assert not here.requires_auth

    def test_it_answers_only_to_loopback_names(self) -> None:
        assert resolve({}).answers_to == LOOPBACK_HOSTS


class TestLocalIsSealed:
    """The whole point of ADR-0011.

    Each of these is refused rather than ignored. An operator who sets a bind
    address and is silently given loopback learns the setting does nothing; one
    who is stopped learns that the *mode* is the thing to change, which is true.
    """

    @pytest.mark.parametrize(
        "setting",
        ["SLIDE_WRIGHT_BIND", "SLIDE_WRIGHT_HOSTNAME", "SLIDE_WRIGHT_TOKEN"],
    )
    def test_a_setting_that_does_not_belong_to_local_stops_the_process(
        self, setting: str
    ) -> None:
        with pytest.raises(DeploymentError) as refused:
            resolve({setting: "0.0.0.0" if setting.endswith("BIND") else "x" * 40})
        assert setting in str(refused.value)
        assert "self-hosted" in str(refused.value), "the error must name the way out"

    def test_even_a_harmless_bind_is_refused(self) -> None:
        """`127.0.0.1` is what local does anyway, and it is still refused.

        Accepting it would mean the setting is read in local mode, and a
        setting that is read is a setting that can be got wrong.
        """
        with pytest.raises(DeploymentError):
            resolve({"SLIDE_WRIGHT_BIND": "127.0.0.1"})

    def test_an_invented_mode_is_not_quietly_treated_as_local(self) -> None:
        with pytest.raises(DeploymentError) as refused:
            resolve({"SLIDE_WRIGHT_MODE": "public"})
        assert "local, self-hosted" in str(refused.value)


class TestSelfHostedFailsClosed:
    def test_it_will_not_start_without_a_token(self) -> None:
        with pytest.raises(DeploymentError) as refused:
            resolve({"SLIDE_WRIGHT_MODE": "self-hosted"})
        assert "SLIDE_WRIGHT_TOKEN" in str(refused.value)

    def test_the_refusal_hands_over_a_usable_token(self) -> None:
        """An operator told only "set a token" sets `changeme`."""
        with pytest.raises(DeploymentError) as refused:
            resolve({"SLIDE_WRIGHT_MODE": "self-hosted"})
        offered = str(refused.value).rsplit(" ", 1)[-1].strip()
        assert len(offered) >= MIN_TOKEN_LENGTH

    def test_a_weak_token_is_refused(self) -> None:
        with pytest.raises(DeploymentError):
            resolve({"SLIDE_WRIGHT_MODE": "self-hosted", "SLIDE_WRIGHT_TOKEN": "hunter2"})

    def test_it_will_not_start_without_being_told_its_own_name(self) -> None:
        """There is no safe default: the Host header is the rebinding defence."""
        with pytest.raises(DeploymentError) as refused:
            resolve({"SLIDE_WRIGHT_MODE": "self-hosted", "SLIDE_WRIGHT_TOKEN": TOKEN})
        assert "SLIDE_WRIGHT_HOSTNAME" in str(refused.value)

    def test_loopback_is_not_silently_added_to_a_hosted_deployment(self) -> None:
        here = resolve(HOSTED)
        assert here.answers_to == frozenset({"deck.internal.example"})
        assert "localhost" not in here.answers_to

    def test_a_port_on_a_configured_hostname_is_not_part_of_the_name(self) -> None:
        """An operator writes what they type into a browser, which has a port."""
        here = resolve({**HOSTED, "SLIDE_WRIGHT_HOSTNAME": "deck.example:8443"})
        assert here.answers_to == frozenset({"deck.example"})


class TestWhatTheHostedServerEnforces:
    @pytest.fixture
    def hosted(self):
        app = create_app(
            workspace=Workspace(), serve_client=False, deployment=resolve(HOSTED)
        )
        with TestClient(app, base_url="http://deck.internal.example") as client:
            yield client

    @pytest.fixture
    def local(self):
        app = create_app(workspace=Workspace(), serve_client=False, deployment=resolve({}))
        with TestClient(app, base_url="http://127.0.0.1:8787") as client:
            yield client

    def test_ping_says_a_token_is_needed_without_needing_one(self, hosted) -> None:
        response = hosted.get("/api/ping")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "auth": "required"}

    def test_ping_says_so_locally_too(self, local) -> None:
        assert local.get("/api/ping").json()["auth"] == "none"

    def test_health_is_behind_the_token(self, hosted) -> None:
        """It reports the engine version and how many decks are open.

        That is a description of somebody's activity, so it is not something an
        unauthenticated caller learns.
        """
        assert hosted.get("/api/health").status_code == 401

    def test_a_wrong_token_is_refused(self, hosted) -> None:
        response = hosted.get(
            "/api/health", headers={"Authorization": f"Bearer {generate_token()}"}
        )
        assert response.status_code == 401

    def test_a_token_in_the_wrong_scheme_is_refused(self, hosted) -> None:
        response = hosted.get("/api/health", headers={"Authorization": f"Basic {TOKEN}"})
        assert response.status_code == 401

    def test_the_right_token_gets_through(self, hosted) -> None:
        response = hosted.get("/api/health", headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_the_refusal_says_how_to_authenticate(self, hosted) -> None:
        response = hosted.get("/api/health")
        assert response.headers.get("www-authenticate") == "Bearer"

    def test_a_valid_token_does_not_excuse_the_wrong_host(self, hosted) -> None:
        """The rebinding guard is not an authentication fallback.

        A page that resolved `evil.example` to this address still sends
        `evil.example`, and holding a token -- stolen, or belonging to the user
        whose browser is being used -- must not get it past that.
        """
        response = hosted.get(
            "/api/health",
            headers={"Authorization": f"Bearer {TOKEN}", "Host": "evil.example"},
        )
        assert response.status_code == 421

    def test_local_still_refuses_a_foreign_host(self, local) -> None:
        assert local.get("/api/health", headers={"Host": "evil.example"}).status_code == 421

    def test_local_needs_no_token(self, local) -> None:
        assert local.get("/api/health").status_code == 200


class TestTheDevOriginIsLocalOnly:
    """Allowing `localhost:5173` on a reachable server hands it to any page.

    The production client is served same-origin and needs no entry at all, so
    there is nothing to trade away here.
    """

    def _origins(self, deployment) -> list[str]:
        app = create_app(workspace=Workspace(), serve_client=False, deployment=deployment)
        for middleware in app.user_middleware:
            allowed = middleware.kwargs.get("allow_origins")
            if allowed is not None:
                return list(allowed)
        pytest.fail("no CORS middleware is installed at all")

    def test_local_permits_the_dev_server(self) -> None:
        assert "http://localhost:5173" in self._origins(resolve({}))

    def test_a_hosted_deployment_permits_no_other_origin(self) -> None:
        assert self._origins(resolve(HOSTED)) == []
