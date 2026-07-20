# optarena-tester: shared sandbox for check_command test execution.
#
# Bundles the toolchains optarena's built-in cases exercise (C/C++, Python,
# Node, SQL via sqlite3, Shell, plus static-validation-only tooling for
# Docker Compose and Terraform cases - pyyaml for YAML structure checks and
# the terraform CLI) so case authors and CI never need these toolchains
# installed on the host - only Docker. Build once with `optarena docker
# build`, then every check_command runs inside a container of this image
# with the case workspace bind-mounted read-write at /workspace and no
# network access.
# Base pinned by multi-arch manifest-list digest (H-03: a floating tag can be
# silently replaced upstream, changing oracle behavior between identical
# OptArena commits). The tag stays for readability; the digest is what's
# actually pulled. Refresh deliberately: docker buildx imagetools inspect <tag>
FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

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

# Offline provider mirror: `terraform fmt -check` needs no provider, but
# `terraform init`/`validate` against a case's aws_*/null_* resources do -
# terraform resolves those via its implied-provider mechanism even with no
# required_providers block. Pre-download hashicorp/aws + hashicorp/null here
# (network available at build time) into a filesystem mirror, and point every
# terraform invocation at it via TF_CLI_CONFIG_FILE, so init/validate resolve
# fully offline under --network none at check_command time.
RUN mkdir -p /tmp/tfmirror-src /opt/terraform-plugins \
    && printf 'terraform {\n  required_providers {\n    aws  = { source = "hashicorp/aws",  version = "~> 5.0" }\n    null = { source = "hashicorp/null", version = "~> 3.2" }\n  }\n}\n' \
        > /tmp/tfmirror-src/main.tf \
    && (cd /tmp/tfmirror-src && terraform providers mirror /opt/terraform-plugins) \
    && rm -rf /tmp/tfmirror-src

ENV TF_CLI_CONFIG_FILE=/etc/terraform-offline.tfrc
RUN printf 'provider_installation {\n  filesystem_mirror {\n    path    = "/opt/terraform-plugins"\n    include = ["registry.terraform.io/hashicorp/*"]\n  }\n  direct {\n    exclude = ["registry.terraform.io/hashicorp/*"]\n  }\n}\n' \
    > /etc/terraform-offline.tfrc

WORKDIR /workspace
