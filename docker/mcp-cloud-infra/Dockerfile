# optarena-tester-mcp-cloud-infra: sandbox for the REAL official
# `awslabs.aws-iac-mcp-server` MCP server (sandboxed-real tool-use
# execution - see optarena/_cases/_sandboxed_mcp_service.py), not our
# in-process approximation (optarena/_cases/_mock_service.py's
# CloudInfraService).
#
# No LocalStack/AWS credentials at all, unlike the original plan's
# assumption: confirmed empirically (see the mock's own tool list -
# cdk_best_practices, search_cdk_documentation, validate_cloudformation_
# template, ...) that this server's real tool surface is IaC documentation
# lookup and static template validation/compliance checking, not live AWS
# resource management - it needs no cloud backend to run at all. Runs with
# --network none, same as every non-network-dependent sandboxed service.
FROM python:3.12-bookworm@sha256:3cd9086bdb30f7c9bc08a3fa621d9842e0d3f6f9291aeb4677e0547817c10b12

RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# Pinned (not floating latest), same reasoning as every other pinned image.
RUN pip install --no-cache-dir "awslabs.aws-iac-mcp-server==1.0.22"

WORKDIR /workspace
