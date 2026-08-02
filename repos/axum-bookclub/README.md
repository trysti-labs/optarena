# axum-bookclub

A small book-club tracker (Axum). Three entities: clubs, members (belong to a
club), and books (belong to a club, with a to_read/reading/finished status).

Run tests with `cargo test --offline`. Integration tests in `tests/` build
the router via `axum_bookclub::app()` and exercise it in-process with
`tower::ServiceExt::oneshot` - no real network port.
