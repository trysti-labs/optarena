mod common;

use axum::http::{Method, StatusCode};
use axum_bookclub::app;
use common::send;
use serde_json::json;

async fn make_club(router: &axum::Router) -> u64 {
    let (_, body) = send(
        router,
        Method::POST,
        "/api/clubs",
        Some(json!({"name": "Club", "description": "x"})),
    )
    .await;
    body["id"].as_u64().unwrap()
}

#[tokio::test]
async fn create_book_defaults_to_read_and_filters_by_status() {
    let router = app();
    let club_id = make_club(&router).await;

    let (status, book) = send(
        &router,
        Method::POST,
        &format!("/api/clubs/{club_id}/books"),
        Some(json!({"title": "Dune", "author": "Herbert"})),
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(book["status"], "to_read");

    let book_id = book["id"].as_u64().unwrap();
    let (status, updated) = send(
        &router,
        Method::PATCH,
        &format!("/api/books/{book_id}"),
        Some(json!({"status": "reading"})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(updated["status"], "reading");

    let (status, _) = send(
        &router,
        Method::PATCH,
        &format!("/api/books/{book_id}"),
        Some(json!({"status": "bogus"})),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);

    let (status, filtered) = send(
        &router,
        Method::GET,
        &format!("/api/clubs/{club_id}/books?status=reading"),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(filtered.as_array().unwrap().len(), 1);
}
