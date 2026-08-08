# optarena-tester-mcp-git-repo: sandbox for the REAL official `mcp-server-git`
# MCP server (sandboxed-real tool-use execution - see
# optarena/_cases/_sandboxed_mcp_service.py), not our in-process
# approximation of it (optarena/_cases/_mock_service.py's GitRepoService).
#
# Same base-pinning discipline as docker/python/Dockerfile - identical
# digest, since nothing here needs a different Debian release. Refresh
# deliberately: docker buildx imagetools inspect debian:bookworm-slim
FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

# P1-03: apt-get upgrade picks up any OS-level security patch published for
# this exact Debian release since the base image was last rebuilt. `git`
# itself is required - mcp-server-git shells out to a real local checkout
# via GitPython, not a re-implementation.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
        python3 \
        python-is-python3 \
        python3-pip \
        git \
    && rm -rf /var/lib/apt/lists/*

# Pinned (not floating latest), same reasoning as docker/python/Dockerfile's
# own pinned packages - an unpinned package can be silently replaced by a
# new release upstream, changing sandboxed-real behavior between identical
# OptArena commits. `mcp-server-git` is part of the official
# modelcontextprotocol/servers monorepo (same release train/versioning as
# the filesystem server this mirrors docker/mcp-filesystem for).
#
# mcp==1.9.4 is pinned EXPLICITLY, not left to mcp-server-git's own loose
# `mcp>=1.0.0` constraint: confirmed empirically that letting pip resolve
# to the latest mcp==2.0.0 breaks mcp-server-git 2026.7.10 outright
# (`AttributeError: 'Server' object has no attribute 'list_tools'` at
# startup - mcp-server-git's source calls the pre-2.0 Server API, and the
# real package has never been updated for the 2.0 breaking change). 1.9.4
# is the last pre-2.0 release. Re-check this pin when bumping either package.
RUN pip install --no-cache-dir --break-system-packages mcp==1.9.4 mcp-server-git==2026.7.10

# A-36: same unprivileged-account provision as every other image - see
# docker/node/Dockerfile's comment for the full rationale.
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

# mcp-server-git shells out to `git`, which refuses to operate on a
# repository owned by a different uid ("detected dubious ownership") - the
# run root is a host bind mount, so the container's root user doesn't
# "own" it in git's eyes either. A wildcard is safe here: this container
# only ever touches the one case directory bind-mounted into it, on a
# fully disposable per-case sandbox (see build_sandboxed_service).
RUN git config --system --add safe.directory '*'

WORKDIR /workspace
