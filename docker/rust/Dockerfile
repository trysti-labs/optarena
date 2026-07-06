# optarena-tester-rust: sandbox for Rust/Axum/Actix-web cases.
#
# A scratch crate depending on axum/actix-web/tokio/serde is built once here
# (network available at build time) to warm the shared ~/.cargo registry
# (both the crates.io index and the downloaded crate sources) so a case's
# own Cargo.toml with compatible version ranges can resolve and build with
# `cargo build --offline` at check_command time (--network none).
FROM rust:1-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /warmup
COPY Cargo.toml .
RUN mkdir -p src/bin
COPY main.rs src/main.rs
COPY actix_warmup.rs src/bin/actix_warmup.rs
RUN cargo build && rm -rf /warmup/target /warmup/src /warmup/Cargo.toml /warmup/Cargo.lock

WORKDIR /workspace
