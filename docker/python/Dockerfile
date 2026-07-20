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

RUN pip install --no-cache-dir --break-system-packages \
        fastapi \
        pydantic \
        uvicorn \
        httpx \
        requests \
        pytest \
        flask \
        django \
        sqlalchemy \
        alembic \
        pandas \
        typer \
        pyyaml

# dependency_upgrade cases: the main interpreter above carries the CURRENT
# (target) version of a library; this venv carries an older, pre-breaking-
# change version side by side, so a case can show what "before" looked like
# without touching the main env's version. Both are installed at BUILD time
# (network available here) since check_command runs with --network none.
RUN python3 -m venv /opt/venv-pydantic1 \
    && /opt/venv-pydantic1/bin/pip install --no-cache-dir pydantic==1.10.14

WORKDIR /workspace
