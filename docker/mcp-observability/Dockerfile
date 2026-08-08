# optarena-tester-mcp-observability: sandbox for the REAL official
# `grafana/mcp-grafana` MCP server (sandboxed-real tool-use execution - see
# optarena/_cases/_sandboxed_mcp_service.py), talking to a REAL, throwaway
# Grafana OSS instance started inside this same container - not our
# in-process approximation (optarena/_cases/_mock_service.py's
# ObservabilityService).
#
# golang:1.26, not 1.24 (docker/mcp-code-intel's base): confirmed
# empirically that mcp-grafana's go.mod requires go 1.26.3. Refresh
# deliberately: docker buildx imagetools inspect golang:1.26-bookworm
FROM golang:1.26-bookworm@sha256:6c5605ab3a9a9fb3c4eafe5b3d63cdbf3881caf113262b67862547b54a9db599

# P1-03: apt-get upgrade picks up any OS-level security patch published for
# this exact Debian release since the base image was last rebuilt. curl is
# needed by the startup script's health check.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        adduser \
        libfontconfig1 \
        musl \
    && rm -rf /var/lib/apt/lists/*

# Grafana OSS isn't in Debian's apt repos - installed from the official
# .deb, pinned to a specific version (not "latest") for the same reasoning
# as every other pinned dependency in this repo: reproducible sandbox
# behavior between identical OptArena commits.
RUN curl -fsSL -o /tmp/grafana.deb https://dl.grafana.com/oss/release/grafana_13.1.3_amd64.deb \
    && dpkg -i /tmp/grafana.deb \
    && rm /tmp/grafana.deb

# Pinned tagged release, same reasoning as every other go-installed binary.
RUN go install github.com/grafana/mcp-grafana/cmd/mcp-grafana@v1.0.0 \
    && mv /go/bin/mcp-grafana /usr/local/bin/

COPY start-grafana-mcp.sh /opt/start-grafana-mcp.sh
RUN chmod +x /opt/start-grafana-mcp.sh

WORKDIR /workspace
