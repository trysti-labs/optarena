# optarena-tester-node: sandbox for Node/Express/React cases.
#
# Packages are installed globally at BUILD time (network available here) and
# NODE_PATH points at the global install dir so plain `require("express")`
# resolves at check_command time without a per-case node_modules and without
# network (check_command runs with --network none).
# Base pinned by multi-arch manifest-list digest (H-03: a floating tag can be
# silently replaced upstream, changing oracle behavior between identical
# OptArena commits). The tag stays for readability; the digest is what's
# actually pulled. Refresh deliberately: docker buildx imagetools inspect <tag>
FROM node:26-bookworm-slim@sha256:9e6f9357d371591e32ab6f2d8a26d63bdd0d17c29eee3f4f3e7e454d9634bf73

# P1-03: apt-get upgrade picks up any OS-level security patch published for
# this exact Debian release since the base image was last rebuilt.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
        python3 \
        python-is-python3 \
    && rm -rf /var/lib/apt/lists/*

# typescript is pinned to 5.x: unpinned it now resolves to TypeScript 7.x (the
# native `tsgo` compiler), whose API surface breaks ts-node 10.9.2 at bootstrap
# ("Cannot read properties of undefined (reading 'fileExists')"), which makes
# every `ts-node main.ts` case (NestJS) unrunnable. ts-node 10.x supports TS 5.x.
# Everything else pinned too, same reasoning as the base-image digest above -
# an unpinned package can be silently replaced upstream, changing oracle
# behavior between identical commits. Versions in package.json are what was
# already installed and verified working - bump deliberately, not by accident.
#
# F-13: `npm ci` against a committed package-lock.json (not `npm install -g`
# against bare version specs) pins the whole resolved dependency GRAPH, not
# just these 17 direct packages - previously a transitive dependency could
# still drift to a new compatible release between identical builds. Installs
# locally into /opt/node-packages (npm's global mode doesn't read a
# lockfile) with NODE_PATH pointed at its node_modules, which resolves
# `require()` identically to the old global install for every case.
# Regenerate: `docker run --rm -v docker/node:/w -w /w node:20-bookworm-slim
# npm install` after editing package.json's versions.
WORKDIR /opt/node-packages
COPY package.json package-lock.json ./
RUN npm ci && npm cache clean --force

# NODE_PATH resolves `require()`; PATH is needed separately for cases whose
# check_command invokes a CLI directly (e.g. bare `tsc ...`) - `npm ci`
# (unlike the old `npm install -g`) only symlinks bins under
# node_modules/.bin, not onto a directory already on PATH.
ENV NODE_PATH=/opt/node-packages/node_modules
ENV PATH=/opt/node-packages/node_modules/.bin:$PATH

# A-36: an unprivileged account so `OPTARENA_SANDBOX_USER=1000:1000` is
# usable at all. The image still DEFAULTS to root (no USER directive): the run
# root is a HOST bind mount whose ownership a container cannot change, so
# flipping the default needs per-track validation on real Linux (see the
# sandbox-nonroot CI job). What was missing before is what this adds - the
# account itself, a writable HOME, and toolchain caches an unprivileged uid
# can actually read: with none of that, the documented opt-in failed
# immediately on every image, which is why it was never a real mitigation.
#
# Adopts a pre-existing uid 1000 where the base image already has one
# (node:20 ships `node`, maven:3.9-eclipse-temurin-17 ships `ubuntu`)
# instead of failing on a duplicate. Parsed with parameter expansion
# rather than `cut` so the RUN carries no pipe (hadolint DL4006).
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
