use std::sync::Arc;

use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::Json;
use serde::Deserialize;
use serde_json::json;

use crate::state::{AppState, Book};

const VALID_STATUSES: [&str; 3] = ["to_read", "reading", "finished"];

#[derive(Deserialize)]
pub struct CreateBook {
    pub title: Option<String>,
    pub author: Option<String>,
}

pub async fn create_book(
    State(state): State<Arc<AppState>>,
    Path(club_id): Path<u32>,
    Json(body): Json<CreateBook>,
) -> impl IntoResponse {
    let title = body.title.unwrap_or_default().trim().to_string();
    let author = body.author.unwrap_or_default().trim().to_string();
    if title.is_empty() {
        return (StatusCode::BAD_REQUEST, Json(json!({"error": "title is required"}))).into_response();
    }
    if author.is_empty() {
        return (StatusCode::BAD_REQUEST, Json(json!({"error": "author is required"}))).into_response();
    }

    let mut store = state.store.lock().unwrap();
    if !store.clubs.contains_key(&club_id) {
        return (StatusCode::NOT_FOUND, Json(json!({"error": "club not found"}))).into_response();
    }
    let id = store.next_book_id;
    store.next_book_id += 1;
    let book = Book {
        id,
        club_id,
        title,
        author,
        status: "to_read".to_string(),
    };
    store.books.insert(id, book.clone());
    (StatusCode::CREATED, Json(book)).into_response()
}

#[derive(Deserialize)]
pub struct ListBooksQuery {
    pub status: Option<String>,
}

pub async fn list_books(
    State(state): State<Arc<AppState>>,
    Path(club_id): Path<u32>,
    Query(query): Query<ListBooksQuery>,
) -> impl IntoResponse {
    let store = state.store.lock().unwrap();
    if !store.clubs.contains_key(&club_id) {
        return (StatusCode::NOT_FOUND, Json(json!({"error": "club not found"}))).into_response();
    }
    let mut books: Vec<Book> = store
        .books
        .values()
        .filter(|b| b.club_id == club_id)
        .filter(|b| query.status.as_deref().map_or(true, |s| b.status == s))
        .cloned()
        .collect();
    books.sort_by_key(|b| b.id);
    (StatusCode::OK, Json(books)).into_response()
}

#[derive(Deserialize)]
pub struct UpdateBook {
    pub status: Option<String>,
}

pub async fn update_book(
    State(state): State<Arc<AppState>>,
    Path(book_id): Path<u32>,
    Json(body): Json<UpdateBook>,
) -> impl IntoResponse {
    let mut store = state.store.lock().unwrap();
    if !store.books.contains_key(&book_id) {
        return (StatusCode::NOT_FOUND, Json(json!({"error": "book not found"}))).into_response();
    }
    if let Some(status) = body.status {
        if !VALID_STATUSES.contains(&status.as_str()) {
            return (StatusCode::BAD_REQUEST, Json(json!({"error": "invalid status"}))).into_response();
        }
        let book = store.books.get_mut(&book_id).unwrap();
        book.status = status;
    }
    let book = store.books.get(&book_id).unwrap().clone();
    (StatusCode::OK, Json(book)).into_response()
}
