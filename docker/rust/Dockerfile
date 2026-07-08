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
FROM rust:1-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
    && rm -rf /var/lib/apt/lists/*

ENV CARGO_TARGET_DIR=/opt/cargo-target

WORKDIR /warmup
COPY Cargo.toml .
RUN mkdir -p src/bin
COPY main.rs src/main.rs
COPY actix_warmup.rs src/bin/actix_warmup.rs
RUN cargo build && rm -rf /warmup

WORKDIR /workspace
