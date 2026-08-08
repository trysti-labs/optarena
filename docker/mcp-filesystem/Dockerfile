# optarena-tester-mcp-filesystem: sandbox for the REAL
# @modelcontextprotocol/server-filesystem MCP server (sandboxed-real tool-use
# execution - see optarena/_cases/_sandboxed_mcp_service.py), not our
# in-process approximation of it (optarena/_cases/_mock_service.py's
# FilesystemService). The point of this image is to run the actual reference
# implementation, not a toolchain for compiling generated code - unlike every
# other docker/<track>/Dockerfile in this repo.
#
# Same base-pinning discipline as docker/node/Dockerfile (H-03: a floating
# tag can be silently replaced upstream, changing sandbox behavior between
# identical OptArena commits) - identical digest, since nothing else in this
# image needs a different Node major version. Refresh deliberately:
# docker buildx imagetools inspect node:20-bookworm-slim
FROM node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0

# P1-03: apt-get upgrade picks up any OS-level security patch published for
# this exact Debian release since the base image was last rebuilt.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# F-13: `npm ci` against a committed package-lock.json pins the WHOLE
# resolved dependency graph (134 packages as of the version below), not just
# this one direct dependency - npm's global-install mode doesn't read a
# lockfile at all, so a transitive dependency could otherwise drift to a new
# release between identical builds. Installs locally into /opt/node-packages
# with its .bin symlink on PATH, so `mcp-server-filesystem` resolves exactly
# like a real global install would, without one.
# Regenerate: cd docker/mcp-filesystem && npm install --package-lock-only
WORKDIR /opt/node-packages
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

ENV PATH=/opt/node-packages/node_modules/.bin:$PATH

# A-36: an unprivileged account so `OPTARENA_SANDBOX_USER=1000:1000` is
# usable at all - same provision, same rationale, as docker/node/Dockerfile.
# The image still DEFAULTS to root (no USER directive): the run root is a
# HOST bind mount whose ownership a container cannot change.
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
