"""Where this process is allowed to listen, and who is allowed to reach it.

ADR-0011. Until now the answer was a module constant -- `HOST = "127.0.0.1"` --
and three CI greps asserting nobody had edited it. That is a real safeguard and
it has a specific weakness: it protects the promise by making one deployment
impossible to express, so the day a customer needs the product on their own
infrastructure, the only available move is to delete the safeguard.

A constant cannot distinguish "this must never be public" from "this is the
local mode and the local mode must never be public". This module makes that
distinction, and in doing so the local guarantee gets *stronger*, not weaker:

    Before:  no code in this repository may bind a public interface.
    Now:     the local mode cannot be configured on to one, at all, ever,
             and choosing any other mode is explicit, refuses to start
             without authentication, and says so on stdout.

The second survives the feature existing. The first only survived while the
feature did not.

**Nothing changes for a user who configures nothing.** No environment variable
set means `local`, which is byte-for-byte the behaviour ADR-0008 and ADR-0010
describe: loopback, Host-header allowlist, no authentication, no accounts.

**`local` refuses configuration rather than ignoring it.** Setting a bind
address while in local mode is an error that stops the process, not a setting
that is quietly overridden. A safeguard that silently discards what an operator
asked for teaches them it is not there.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from enum import Enum

#: Names a loopback deployment answers to. Anything else is a request that was
#: addressed somewhere other than this machine -- see `app.only_answer_to_host`
#: for why the Host header, and not the socket, is what survives DNS rebinding.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

LOOPBACK_BIND = "127.0.0.1"
DEFAULT_PORT = 8787

#: The shortest token worth accepting. A deployment reachable by anything other
#: than the machine it runs on is reachable by a scanner, and a short shared
#: secret on such a deployment is an unlocked door with a sign on it.
MIN_TOKEN_LENGTH = 32


class DeploymentError(Exception):
    """A configuration that must not start. The message is meant for an operator."""


class Mode(str, Enum):
    #: One user, their own machine, the document never leaves it. The default,
    #: and what happens when nothing is configured.
    LOCAL = "local"

    #: A server the *customer* runs, inside their own network. Still not a
    #: vendor-hosted product: the trust boundary does not move, because the
    #: infrastructure is theirs. See docs/architecture/08-deployment-models.md.
    SELF_HOSTED = "self-hosted"


@dataclass(frozen=True)
class Deployment:
    """The resolved answer, computed once at startup and never re-read.

    Frozen because a deployment that can change while running is a deployment
    whose guarantees are true only of the moment they were checked.
    """

    mode: Mode
    bind: str
    port: int
    #: Names this server will answer to in a Host header. Empty means loopback.
    hostnames: frozenset[str]
    #: Shared bearer token. `None` in local mode, and never `None` otherwise.
    token: str | None

    @property
    def is_local(self) -> bool:
        return self.mode is Mode.LOCAL

    @property
    def requires_auth(self) -> bool:
        return self.token is not None

    @property
    def answers_to(self) -> frozenset[str]:
        return LOOPBACK_HOSTS if self.is_local else self.hostnames

    def describe(self) -> list[str]:
        """What to print at startup, so the operator can see what they got.

        A deployment mode that is only visible in a config file is one people
        get wrong. This is the line that makes a mistake obvious in the first
        second rather than in an incident.
        """
        if self.is_local:
            return [
                f"http://{self.bind}:{self.port}",
                "  the document does not leave this machine (ADR-0008)",
            ]
        return [
            f"listening on {self.bind}:{self.port}",
            f"  mode: self-hosted -- answers to {', '.join(sorted(self.hostnames))}",
            "  authentication: required (bearer token)",
            "  the document does not leave YOUR infrastructure; this process "
            "does not upload it anywhere",
        ]


def resolve(env: dict[str, str] | None = None, *, port: int | None = None) -> Deployment:
    """Read the environment and decide, or refuse.

    Every refusal is a `DeploymentError` carrying a sentence an operator can
    act on. There is deliberately no path that warns and continues: a
    misconfigured deployment of this product exposes confidential documents,
    and a warning in a log is not a control.
    """
    source = os.environ if env is None else env
    raw = (source.get("SLIDE_WRIGHT_MODE") or Mode.LOCAL.value).strip().lower()

    try:
        mode = Mode(raw)
    except ValueError:
        allowed = ", ".join(m.value for m in Mode)
        raise DeploymentError(
            f"SLIDE_WRIGHT_MODE={raw!r} is not a mode. Choose one of: {allowed}."
        ) from None

    chosen_port = port or _port(source)
    if mode is Mode.LOCAL:
        return _local(source, chosen_port)
    return _self_hosted(source, chosen_port)


def _local(source, port: int) -> Deployment:
    """Loopback, and not negotiable.

    The three settings below are refused rather than ignored. An operator who
    sets a bind address and is silently given loopback learns that the setting
    does nothing; an operator who is stopped learns that the *mode* is the
    thing to change, which is true and is the point.
    """
    for name in ("SLIDE_WRIGHT_BIND", "SLIDE_WRIGHT_HOSTNAME", "SLIDE_WRIGHT_TOKEN"):
        if source.get(name):
            raise DeploymentError(
                f"{name} is set, but the mode is 'local', which always binds "
                f"{LOOPBACK_BIND} and never authenticates. This is not ignored, "
                "because a setting that silently does nothing is worse than one "
                "that is refused. To run a server other people reach, set "
                "SLIDE_WRIGHT_MODE=self-hosted and read "
                "docs/architecture/08-deployment-models.md first."
            )

    return Deployment(
        mode=Mode.LOCAL,
        bind=LOOPBACK_BIND,
        port=port,
        hostnames=LOOPBACK_HOSTS,
        token=None,
    )


def _self_hosted(source, port: int) -> Deployment:
    """A server on the customer's own infrastructure. Fails closed, twice.

    Without a token it does not start. Without the names it should answer to it
    does not start. Both are refusals rather than defaults, because the safe
    default for "who may reach this" cannot be guessed, and guessing wrong here
    means a confidential deck is reachable by whoever asks.
    """
    token = (source.get("SLIDE_WRIGHT_TOKEN") or "").strip()
    if not token:
        raise DeploymentError(
            "SLIDE_WRIGHT_MODE=self-hosted requires SLIDE_WRIGHT_TOKEN, and it "
            "is not set. This process will not start without authentication: "
            "it serves confidential documents, and a server reachable by "
            "anything other than its own machine is reachable by a scanner.\n"
            f"  generate one:  {generate_token()}"
        )
    if len(token) < MIN_TOKEN_LENGTH:
        raise DeploymentError(
            f"SLIDE_WRIGHT_TOKEN is {len(token)} characters; at least "
            f"{MIN_TOKEN_LENGTH} are required.\n"
            f"  generate one:  {generate_token()}"
        )

    names = _names(source.get("SLIDE_WRIGHT_HOSTNAME", ""))
    if not names:
        raise DeploymentError(
            "SLIDE_WRIGHT_MODE=self-hosted requires SLIDE_WRIGHT_HOSTNAME -- the "
            "name or names this server is reached by, comma separated, for "
            "example 'slidewright.internal.example'. It is what a DNS rebinding "
            "attack cannot forge, so there is no safe default to fall back to."
        )

    return Deployment(
        mode=Mode.SELF_HOSTED,
        bind=(source.get("SLIDE_WRIGHT_BIND") or "0.0.0.0").strip(),
        port=port,
        hostnames=names,
        token=token,
    )


def _names(raw: str) -> frozenset[str]:
    """Split and normalise a host list, dropping any port each carries.

    An operator writes what they type into a browser, which often includes a
    port. A Host header carries one too. Comparing them has to happen on the
    name alone or `example.internal` and `example.internal:8443` are two
    different deployments to this code and one deployment to everybody else.
    """
    return frozenset(
        stripped for stripped in (_name_only(part) for part in raw.split(",")) if stripped
    )


def _name_only(value: str) -> str:
    """`example.com:8443` -> `example.com`, and `[::1]:8787` -> `[::1]`.

    A bracketed IPv6 literal is full of colons, so the port is only the tail
    after the *last* one, and only when the part before it does not end in the
    closing bracket that makes it an address rather than a host-and-port.
    """
    host = value.strip().lower()
    head, colon, tail = host.rpartition(":")
    if colon and head and tail.isdigit() and not host.endswith("]"):
        return head
    return host


def _port(source) -> int:
    raw = (source.get("SLIDE_WRIGHT_PORT") or "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        raise DeploymentError(f"SLIDE_WRIGHT_PORT={raw!r} is not a number.") from None
    if not 1 <= port <= 65535:
        raise DeploymentError(f"SLIDE_WRIGHT_PORT={port} is not a usable port.")
    return port


def generate_token() -> str:
    """A token worth using, so the error message can hand one over.

    An operator told only "set a token" sets `changeme`. One handed a real one
    pastes it.
    """
    return secrets.token_urlsafe(32)
