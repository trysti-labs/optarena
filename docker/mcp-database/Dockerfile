# optarena-tester-mcp-database: sandbox for the REAL `crystaldba/postgres-mcp`
# MCP server (sandboxed-real tool-use execution - see
# optarena/_cases/_sandboxed_mcp_service.py), talking to a REAL, throwaway
# Postgres instance started inside this same container - not our in-process
# approximation (optarena/_cases/_mock_service.py's DatabaseService).
#
# python:3.12, not the debian:bookworm-slim + apt python3 (3.11) every other
# image uses: confirmed empirically that postgres-mcp declares
# `Requires-Python >=3.12` - Debian bookworm's own python3 package is 3.11.
# Refresh deliberately: docker buildx imagetools inspect python:3.12-bookworm
FROM python:3.12-bookworm@sha256:3cd9086bdb30f7c9bc08a3fa621d9842e0d3f6f9291aeb4677e0547817c10b12

# P1-03: apt-get upgrade picks up any OS-level security patch published for
# this exact Debian release since the base image was last rebuilt.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
        postgresql \
    && rm -rf /var/lib/apt/lists/*

# Pinned (not floating latest), same reasoning as every other image's pinned
# packages. mcp==1.9.4 pinned explicitly, not left to postgres-mcp's own
# loose `mcp[cli]>=1.5.0` constraint: the SAME real compatibility break
# confirmed for mcp-server-git (mcp==2.0.0's Server API removed
# `.list_tools()`) applies here too - postgres-mcp's source predates it.
RUN pip install --no-cache-dir mcp==1.9.4 postgres-mcp==0.3.0

COPY start-postgres-mcp.sh /opt/start-postgres-mcp.sh
RUN chmod +x /opt/start-postgres-mcp.sh

# A-36: same unprivileged-account provision as every other image - not
# actually usable here (Postgres itself needs to run as its own `postgres`
# system user, already provisioned by the postgresql apt package), kept for
# consistency/documentation of the convention rather than a real opt-in path
# for this particular image.
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
