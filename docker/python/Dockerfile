# optarena-tester-python: sandbox for Python/FastAPI/Pydantic/Flask/Django cases.
#
# All dependencies are installed at BUILD time (network available here) since
# check_command runs with --network none - nothing can be pip installed at
# test time. Add a package here, don't add it to a case's check_command.
# Base pinned by multi-arch manifest-list digest (H-03: a floating tag can be
# silently replaced upstream, changing oracle behavior between identical
# OptArena commits). The tag stays for readability; the digest is what's
# actually pulled. Refresh deliberately: docker buildx imagetools inspect <tag>
FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python-is-python3 \
        python3-pip \
        python3-venv \
        git \
    && rm -rf /var/lib/apt/lists/*

# Pinned (not floating latest) for the same reason the base image is
# digest-pinned above: an unpinned package can be silently replaced by a
# new release upstream, changing oracle behavior between identical
# OptArena commits. Versions below are what was already installed and
# verified working - bump deliberately, not by accident.
RUN pip install --no-cache-dir --break-system-packages \
        fastapi==0.139.2 \
        pydantic==2.13.4 \
        uvicorn==0.51.0 \
        httpx==0.28.1 \
        requests==2.34.2 \
        pytest==9.1.1 \
        flask==3.1.3 \
        django==5.2.16 \
        sqlalchemy==2.0.51 \
        alembic==1.18.5 \
        pandas==3.0.3 \
        typer==0.27.0 \
        pyyaml==6.0.3

# dependency_upgrade cases: the main interpreter above carries the CURRENT
# (target) version of a library; this venv carries an older, pre-breaking-
# change version side by side, so a case can show what "before" looked like
# without touching the main env's version. Both are installed at BUILD time
# (network available here) since check_command runs with --network none.
RUN python3 -m venv /opt/venv-pydantic1 \
    && /opt/venv-pydantic1/bin/pip install --no-cache-dir pydantic==1.10.14

# A-36: an unprivileged account so `OPTARENA_SANDBOX_USER=1000:1000` is
# usable at all. The image still DEFAULTS to root (no USER directive): the run
# root is a HOST bind mount whose ownership a container cannot change, so
# flipping the default needs per-track validation on real Linux (see the
# sandbox-nonroot CI job). What was missing before is what this adds - the
# account itself, a writable HOME, and toolchain caches an unprivileged uid
# can actually read: with none of that, the documented opt-in failed
# immediately on every image, which is why it was never a real mitigation.
#
# Adopts a pre-existing uid 1000 where the base image already has one
# (node:20 ships `node`, maven:3.9-eclipse-temurin-17 ships `ubuntu`)
# instead of failing on a duplicate. Parsed with parameter expansion
# rather than `cut` so the RUN carries no pipe (hadolint DL4006).
RUN set -eu; \
    if ! getent group 1000 >/dev/null; then groupadd -g 1000 optarena; fi; \
    if ! getent passwd 1000 >/dev/null; then \
        useradd -u 1000 -g 1000 -m -d /home/optarena -s /bin/sh optarena; \
    fi; \
    entry="$(getent passwd 1000)"; \
    without_shell="${entry%:*}"; \
    home="${without_shell##*:}"; \
    mkdir -p "$home"; \
    chmod 0777 "$home"

WORKDIR /workspace
