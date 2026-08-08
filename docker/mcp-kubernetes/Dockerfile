# optarena-tester-mcp-kubernetes: sandbox for the REAL `Flux159/mcp-server-
# kubernetes` MCP server (sandboxed-real tool-use execution - see
# optarena/_cases/_sandboxed_mcp_service.py), talking to a REAL, throwaway
# `kind` cluster - not our in-process approximation
# (optarena/_cases/_mock_service.py's KubernetesService).
#
# Same docker-outside-of-docker trust boundary as docker/mcp-docker: kind's
# cluster "nodes" are created as SIBLING containers on the HOST's real
# Docker daemon via the mounted socket, not a nested/privileged inner
# daemon - see SANDBOXED_SERVICES["kubernetes"]'s extra_run_args.
FROM node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0

RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        docker.io \
    && rm -rf /var/lib/apt/lists/*

# Pinned (not floating latest), same reasoning as every other pinned binary.
RUN curl -fsSL -o /usr/local/bin/kind \
        https://github.com/kubernetes-sigs/kind/releases/download/v0.32.0/kind-linux-amd64 \
    && chmod +x /usr/local/bin/kind
RUN curl -fsSL -o /usr/local/bin/kubectl \
        https://dl.k8s.io/release/v1.36.3/bin/linux/amd64/kubectl \
    && chmod +x /usr/local/bin/kubectl

# F-13: npm ci against a committed package-lock.json, same convention as
# docker/mcp-filesystem. Regenerate: cd docker/mcp-kubernetes &&
# npm install --package-lock-only
WORKDIR /opt/node-packages
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
ENV PATH=/opt/node-packages/node_modules/.bin:/usr/local/bin:$PATH

COPY start-k8s-mcp.sh /opt/start-k8s-mcp.sh
RUN chmod +x /opt/start-k8s-mcp.sh

WORKDIR /workspace
