mod common;

use axum::http::{Method, StatusCode};
use axum_bookclub::app;
use common::send;
use serde_json::json;

#[tokio::test]
async fn create_and_list_clubs() {
    let router = app();

    let (status, body) = send(
        &router,
        Method::POST,
        "/api/clubs",
        Some(json!({"name": "Sci-Fi Readers", "description": "space stuff"})),
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(body["name"], "Sci-Fi Readers");

    let (status, _) = send(&router, Method::POST, "/api/clubs", Some(json!({"description": "x"}))).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);

    let (status, body) = send(&router, Method::GET, "/api/clubs", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body.as_array().unwrap().len(), 1);

    let (status, _) = send(&router, Method::GET, "/api/clubs/999", None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}
