# optarena-tester-mcp-build-tools: sandbox for the REAL `nx-mcp` MCP server
# (sandboxed-real tool-use execution - see
# optarena/_cases/_sandboxed_mcp_service.py), plus the real `nx` CLI it
# introspects a workspace through - not our in-process approximation
# (optarena/_cases/_mock_service.py's BuildToolsService).
#
# Same base-pinning discipline as docker/node/Dockerfile - identical digest.
FROM node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0

RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# F-13: npm ci against a committed package-lock.json pins the whole resolved
# dependency graph, same convention as docker/mcp-filesystem. `nx` itself is
# pinned alongside nx-mcp: nx-mcp introspects a workspace via the `nx` CLI,
# so both need to be present and version-matched.
# Regenerate: cd docker/mcp-build-tools && npm install --package-lock-only
WORKDIR /opt/node-packages
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

ENV PATH=/opt/node-packages/node_modules/.bin:$PATH

# A-36: same unprivileged-account provision as every other image.
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
