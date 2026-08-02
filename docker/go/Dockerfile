# optarena-tester-go: sandbox for Go/Gin/Fiber cases.
#
# A scratch module requiring Gin and Fiber is built once here (network
# available at build time) to warm the shared module + build cache under
# $GOPATH. At check_command time (--network none) GOFLAGS=-mod=mod lets `go
# build`/`go test` regenerate go.sum entries from the already-cached module
# zips without touching the network; GOPROXY=off/GOSUMDB=off make that the
# only path so a genuinely missing dependency fails fast instead of hanging.
# Base pinned by multi-arch manifest-list digest (H-03: a floating tag can be
# silently replaced upstream, changing oracle behavior between identical
# OptArena commits). The tag stays for readability; the digest is what's
# actually pulled. Refresh deliberately: docker buildx imagetools inspect <tag>
FROM golang:1.22-bookworm@sha256:3d699e4d15d0f8f13c9195c0632a16702b8cbdece2955af1c23b37ae5d55a253

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /warmup
COPY warmup.go .
RUN go mod init warmup \
    && go get github.com/gin-gonic/gin@v1.10.0 \
    && go get github.com/gofiber/fiber/v2@v2.52.5 \
    && go build ./... \
    && rm -rf /warmup

ENV GOFLAGS=-mod=mod
ENV GOPROXY=off
ENV GOSUMDB=off

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

# The shared module cache is populated at build time and only READ at
# check_command time, but Go refuses a cache it cannot stat.
RUN chmod -R a+rX "$(go env GOMODCACHE)" 2>/dev/null || true

WORKDIR /workspace
