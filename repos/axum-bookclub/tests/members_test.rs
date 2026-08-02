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
async fn create_member_requires_existing_club() {
    let router = app();

    let (status, _) = send(
        &router,
        Method::POST,
        "/api/clubs/999/members",
        Some(json!({"name": "Ada", "email": "a@example.com"})),
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);

    let club_id = make_club(&router).await;
    let (status, body) = send(
        &router,
        Method::POST,
        &format!("/api/clubs/{club_id}/members"),
        Some(json!({"name": "Ada", "email": "a@example.com"})),
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(body["club_id"].as_u64().unwrap(), club_id);
}

#[tokio::test]
async fn list_members_scoped_to_club() {
    let router = app();
    let club1 = make_club(&router).await;
    let club2 = make_club(&router).await;

    send(
        &router,
        Method::POST,
        &format!("/api/clubs/{club1}/members"),
        Some(json!({"name": "Ada", "email": "a@example.com"})),
    )
    .await;
    send(
        &router,
        Method::POST,
        &format!("/api/clubs/{club2}/members"),
        Some(json!({"name": "Bob", "email": "b@example.com"})),
    )
    .await;

    let (status, body) = send(&router, Method::GET, &format!("/api/clubs/{club1}/members"), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body.as_array().unwrap().len(), 1);
}
