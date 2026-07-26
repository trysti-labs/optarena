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
FROM node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0

RUN apt-get update && apt-get install -y --no-install-recommends \
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

WORKDIR /workspace
