# optarena-tester-rust: sandbox for Rust/Axum/Actix-web cases.
#
# A scratch crate depending on axum/actix-web/tokio/serde is built once here
# (network available at build time) to warm the shared ~/.cargo registry
# (both the crates.io index and the downloaded crate sources) so a case's
# own Cargo.toml with compatible version ranges can resolve and build with
# `cargo build --offline` at check_command time (--network none).
#
# CARGO_TARGET_DIR points every build (the warmup's AND each case's) at one
# shared target directory, so the *compiled* dependency artifacts from the
# warmup (axum/actix/tokio take minutes to compile) are reused at
# check_command time - a case build only compiles the case's own crate, in
# seconds, instead of recompiling the whole dependency graph inside its
# check_command_timeout. Case test scripts locate their binary via
# $CARGO_TARGET_DIR/debug/<bin>. Concurrent case builds are safe: cargo
# file-locks the target dir and serializes.
# Base pinned by multi-arch manifest-list digest (H-03: a floating tag can be
# silently replaced upstream, changing oracle behavior between identical
# OptArena commits). The tag stays for readability; the digest is what's
# actually pulled. Refresh deliberately: docker buildx imagetools inspect <tag>
FROM rust:1-bookworm@sha256:77fac8b98f9f46062bb680b6d25d5bcaabfc400143952ebc572e924bcbedc3fa

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
    && rm -rf /var/lib/apt/lists/*

ENV CARGO_TARGET_DIR=/opt/cargo-target

WORKDIR /warmup
COPY Cargo.toml Cargo.lock ./
RUN mkdir -p src/bin
COPY main.rs src/main.rs
COPY actix_warmup.rs src/bin/actix_warmup.rs
# F-13: --locked makes the pinned Cargo.lock (regenerate with `docker run
# --rm -v <this dir>:/warmup -w /warmup rust:1-bookworm cargo
# generate-lockfile`, using matching src/ layout) load-bearing - without it,
# a lockfile on disk is just documentation, since plain `cargo build` still
# re-resolves and silently drifts to newer compatible crate versions.
RUN cargo build --locked && rm -rf /warmup

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

# cargo writes its build lock and incremental output into the pre-warmed
# target dir (an anonymous volume at run time, initialized from these
# bits) - an unprivileged uid needs write access to it.
RUN chmod -R a+rwX /opt/cargo-target \
 && chmod -R a+rX "${CARGO_HOME:-/usr/local/cargo}"

WORKDIR /workspace
