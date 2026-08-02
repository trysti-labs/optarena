use std::sync::Arc;

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::Json;
use serde::Deserialize;
use serde_json::json;

use crate::state::{AppState, Club};

#[derive(Deserialize)]
pub struct CreateClub {
    pub name: Option<String>,
    pub description: Option<String>,
}

pub async fn create_club(
    State(state): State<Arc<AppState>>,
    Json(body): Json<CreateClub>,
) -> impl IntoResponse {
    let name = body.name.unwrap_or_default().trim().to_string();
    if name.is_empty() {
        return (StatusCode::BAD_REQUEST, Json(json!({"error": "name is required"}))).into_response();
    }
    let description = body.description.unwrap_or_default();

    let mut store = state.store.lock().unwrap();
    let id = store.next_club_id;
    store.next_club_id += 1;
    let club = Club { id, name, description };
    store.clubs.insert(id, club.clone());
    (StatusCode::CREATED, Json(club)).into_response()
}

pub async fn list_clubs(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let store = state.store.lock().unwrap();
    let mut clubs: Vec<Club> = store.clubs.values().cloned().collect();
    clubs.sort_by_key(|c| c.id);
    Json(clubs)
}

pub async fn get_club(
    State(state): State<Arc<AppState>>,
    Path(club_id): Path<u32>,
) -> impl IntoResponse {
    let store = state.store.lock().unwrap();
    match store.clubs.get(&club_id) {
        Some(club) => (StatusCode::OK, Json(club.clone())).into_response(),
        None => (StatusCode::NOT_FOUND, Json(json!({"error": "club not found"}))).into_response(),
    }
}
