# optarena-tester-mcp-docker: sandbox for the REAL `ckreiling/mcp-server-docker`
# MCP server (sandboxed-real tool-use execution - see
# optarena/_cases/_sandboxed_mcp_service.py), talking to the REAL HOST
# Docker daemon via a mounted socket (docker-outside-of-docker) - not our
# in-process approximation (optarena/_cases/_mock_service.py's
# DockerService), and NOT a nested/privileged inner daemon.
#
# Community-maintained, not an official Docker Inc. server - flagged
# accordingly (no official lightweight Docker-Engine-API MCP wrapper exists
# as of this research).
FROM python:3.12-bookworm@sha256:3cd9086bdb30f7c9bc08a3fa621d9842e0d3f6f9291aeb4677e0547817c10b12

RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# Pinned (not floating latest), same reasoning as every other pinned image.
RUN pip install --no-cache-dir "mcp-server-docker==0.3.0"

WORKDIR /workspace
