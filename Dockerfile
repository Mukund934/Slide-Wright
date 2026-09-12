# Slide-Wright as a container, for the self-hosted mode of ADR-0011.
#
# This is the customer-hosted model of docs/architecture/08-deployment-models.md
# §4B: a server the *customer* runs, inside their own network. It is not a
# vendor-hosted product and does not become one by being in a container. The
# trust boundary does not move, because the infrastructure on the other side of
# it is theirs.
#
# Build:
#   docker build -t slide-wright .
#
# Run — every one of these is required, and the process refuses to start
# without them rather than guessing:
#   docker run --rm -p 8787:8787 \
#     -e SLIDE_WRIGHT_MODE=self-hosted \
#     -e SLIDE_WRIGHT_HOSTNAME=slidewright.internal.example \
#     -e SLIDE_WRIGHT_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" \
#     -v /srv/decks:/decks \
#     slide-wright
#
# The volume is the whole story of how this product works. Decks are opened by
# path, never uploaded, so the container needs to see the filesystem the decks
# are on. Mount it read-write: the engine keeps versions in a `.slidewright`
# folder beside each deck, which is what makes revert reach a real earlier copy.

# ── the client ───────────────────────────────────────────────────────────────
# Built here rather than copied from a developer's machine, so the image cannot
# ship whatever happened to be in someone's dist/ at the time.
FROM node:22-slim AS client

WORKDIR /build
COPY src/product/web/package.json src/product/web/package-lock.json ./
RUN npm ci
COPY src/product/web/ ./
RUN npm run build


# ── the wheels ───────────────────────────────────────────────────────────────
FROM python:3.13-slim AS wheels

WORKDIR /build
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel

COPY src/engine/ ./src/engine/
COPY src/product/api/ ./src/product/api/
# Where setup.py looks for it. The API wheel carries the client, and building
# in the wrong order produces an image serving an API with no interface -- the
# defect this repository already shipped once.
COPY --from=client /build/dist/ ./src/product/web/dist/

RUN python -m pip wheel --no-deps --no-build-isolation -w /wheels ./src/engine \
 && python -m pip wheel --no-deps --no-build-isolation -w /wheels ./src/product/api

COPY scripts/assert_wheel_has_client.py ./
RUN python assert_wheel_has_client.py /wheels


# ── the image ────────────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# Not root. This process reads and writes the customer's document directory,
# and the blast radius of any file-handling bug should stop at that directory.
RUN useradd --create-home --uid 10001 slidewright

COPY --from=wheels /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl \
 && rm -rf /wheels

USER slidewright
WORKDIR /decks

# Deliberately no default for MODE, HOSTNAME or TOKEN. A container that starts
# without being configured is a container somebody deploys without reading
# anything, and the failure mode here is a confidential document reachable by
# whoever asks. ADR-0011 makes that refusal the product's behaviour; this
# Dockerfile just declines to paper over it.
ENV SLIDE_WRIGHT_BIND=0.0.0.0 \
    SLIDE_WRIGHT_PORT=8787 \
    PYTHONUNBUFFERED=1

EXPOSE 8787

# /api/ping is the only unauthenticated route, which is exactly what a health
# check needs: it proves the process is serving without handing an orchestrator
# a credential, and without reporting anyone's activity.
#
# It must send the *configured* hostname, not 127.0.0.1. A self-hosted
# deployment answers only to the names it was given and loopback is not
# silently added to that set (ADR-0011), so a probe addressed to 127.0.0.1
# would be refused with 421 forever -- a health check that can never pass,
# which is worse than none because it takes the container down with it.
COPY --chown=slidewright:slidewright scripts/container_healthcheck.py /usr/local/bin/healthcheck.py
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "/usr/local/bin/healthcheck.py"]

ENTRYPOINT ["python", "-m", "slide_wright_api", "--no-browser"]
