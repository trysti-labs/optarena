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

WORKDIR /workspace
