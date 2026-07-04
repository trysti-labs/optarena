# optarena-tester: shared sandbox for check_command test execution.
#
# Bundles the toolchains optarena's built-in cases exercise (C, Python, Node)
# so case authors and CI never need gcc/python/node installed on the host -
# only Docker. Build once with `optarena docker build`, then every
# check_command runs inside a container of this image with the case
# workspace bind-mounted read-write at /workspace and no network access.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        python3 \
        python-is-python3 \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
