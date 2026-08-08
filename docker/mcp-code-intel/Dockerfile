# optarena-tester-mcp-code-intel: sandbox for the REAL `isaacphi/mcp-language-server`
# MCP server (sandboxed-real tool-use execution - see
# optarena/_cases/_sandboxed_mcp_service.py), proxying to a real `pyright`
# LSP server for a real Python workspace - not our in-process approximation
# (optarena/_cases/_mock_service.py's CodeIntelService).
#
# Needs TWO real toolchains: Go (to build mcp-language-server itself - no
# prebuilt release binaries exist) and Node (pyright, the LSP backend it
# proxies to for this image's target language). Base pinned by digest, same
# H-03 reasoning as every other image. 1.24, not 1.23: confirmed empirically
# that mcp-language-server@v0.1.1's go.mod requires go >= 1.24.0. Refresh
# deliberately: docker buildx imagetools inspect golang:1.24-bookworm
FROM golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

# P1-03: apt-get upgrade picks up any OS-level security patch published for
# this exact Debian release since the base image was last rebuilt.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

# Pinned (not floating latest), same reasoning as every other image's pinned
# packages - go install against @latest can silently resolve to a new,
# behavior-changing release between identical OptArena commits. v0.1.1 is
# the project's latest tagged release (github.com/isaacphi/mcp-language-server/tags).
# $GOPATH/bin, not /root/go/bin - the official golang image sets GOPATH=/go
# (confirmed via `go env GOPATH`), not the GOPATH-less default used outside it.
RUN go install github.com/isaacphi/mcp-language-server@v0.1.1 \
    && mv /go/bin/mcp-language-server /usr/local/bin/

RUN npm install -g pyright@1.1.411

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

WORKDIR /workspace
