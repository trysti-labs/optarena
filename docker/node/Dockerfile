# optarena-tester-node: sandbox for Node/Express/React cases.
#
# Packages are installed globally at BUILD time (network available here) and
# NODE_PATH points at the global install dir so plain `require("express")`
# resolves at check_command time without a per-case node_modules and without
# network (check_command runs with --network none).
FROM node:20-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python-is-python3 \
    && rm -rf /var/lib/apt/lists/*

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
        typescript \
        ts-node \
        reflect-metadata \
        rxjs \
        @nestjs/core \
        @nestjs/common \
        @nestjs/platform-express \
    && npm cache clean --force

ENV NODE_PATH=/usr/local/lib/node_modules

WORKDIR /workspace
