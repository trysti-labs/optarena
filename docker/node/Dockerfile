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
RUN npm install -g \
        express \
        react \
        react-dom \
        next \
        vue \
        @babel/core \
        @babel/cli \
        @babel/preset-env \
        @babel/preset-react \
        jest \
        typescript@5.6.3 \
        ts-node \
        reflect-metadata \
        rxjs \
        @nestjs/core \
        @nestjs/common \
        @nestjs/platform-express \
    && npm cache clean --force

ENV NODE_PATH=/usr/local/lib/node_modules

WORKDIR /workspace
