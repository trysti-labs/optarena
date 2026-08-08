# optarena-tester-mcp-package-registry: sandbox for the REAL
# `package-registry-mcp` MCP server (sandboxed-real tool-use execution -
# see optarena/_cases/_sandboxed_mcp_service.py), not our in-process
# approximation (optarena/_cases/_mock_service.py's PackageRegistryService).
#
# UNLIKE every other sandboxed-real image, this one runs with real network
# access (see SANDBOXED_SERVICES["package_registry"]'s "network": "bridge")
# - confirmed empirically that the real server has no offline-registry
# config option at all (hardcoded to registry.npmjs.org/crates.io/nuget.org/
# pypi.org/proxy.golang.org/GitHub advisories), so Verdaccio (the original
# plan for an offline local registry) cannot be wired in without forking the
# server. The trade-off is judged acceptable because confirmed empirically
# via its own tool list: every exposed tool is a read-only public-metadata
# lookup (search/get-details/list-versions/advisories) - there is no
# publish/delete/mutate tool at all, so real network access here can't
# perform a destructive action, unlike a hypothetical write-capable server.
#
# Same base-pinning discipline as docker/mcp-filesystem.
FROM node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0

RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# F-13: npm ci against a committed package-lock.json, same convention as
# docker/mcp-filesystem. Regenerate: cd docker/mcp-package-registry &&
# npm install --package-lock-only
WORKDIR /opt/node-packages
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

ENV PATH=/opt/node-packages/node_modules/.bin:$PATH

WORKDIR /workspace
