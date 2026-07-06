# optarena-tester: shared sandbox for check_command test execution.
#
# Bundles the toolchains optarena's built-in cases exercise (C/C++, Python,
# Node, SQL via sqlite3, Shell, plus static-validation-only tooling for
# Docker Compose and Terraform cases - pyyaml for YAML structure checks,
# terraform CLI for `fmt -check`, neither of which needs `terraform init`/
# provider downloads, so both stay usable under --network none) so case
# authors and CI never need these toolchains installed on the host - only
# Docker. Build once with `optarena docker build`, then every check_command
# runs inside a container of this image with the case workspace bind-mounted
# read-write at /workspace and no network access.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        python3 \
        python-is-python3 \
        python3-pip \
        nodejs \
        npm \
        sqlite3 \
        curl \
        unzip \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --break-system-packages pyyaml

RUN curl -fsSL -o /tmp/terraform.zip \
        https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_amd64.zip \
    && unzip -q /tmp/terraform.zip -d /usr/local/bin \
    && rm /tmp/terraform.zip \
    && terraform version

# Terraform normally phones home once per run to check for a newer release;
# under --network none that call would just be wasted time (it fails
# silently rather than hanging, but there's no reason to pay for it).
ENV CHECKPOINT_DISABLE=1

WORKDIR /workspace
