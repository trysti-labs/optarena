use std::sync::Arc;

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::Json;
use serde::Deserialize;
use serde_json::json;

use crate::state::{AppState, Member};

#[derive(Deserialize)]
pub struct CreateMember {
    pub name: Option<String>,
    pub email: Option<String>,
}

pub async fn create_member(
    State(state): State<Arc<AppState>>,
    Path(club_id): Path<u32>,
    Json(body): Json<CreateMember>,
) -> impl IntoResponse {
    let name = body.name.unwrap_or_default().trim().to_string();
    let email = body.email.unwrap_or_default().trim().to_string();
    if name.is_empty() {
        return (StatusCode::BAD_REQUEST, Json(json!({"error": "name is required"}))).into_response();
    }
    if email.is_empty() {
        return (StatusCode::BAD_REQUEST, Json(json!({"error": "email is required"}))).into_response();
    }

    let mut store = state.store.lock().unwrap();
    if !store.clubs.contains_key(&club_id) {
        return (StatusCode::NOT_FOUND, Json(json!({"error": "club not found"}))).into_response();
    }
    let id = store.next_member_id;
    store.next_member_id += 1;
    let member = Member { id, club_id, name, email };
    store.members.insert(id, member.clone());
    (StatusCode::CREATED, Json(member)).into_response()
}

pub async fn list_members(
    State(state): State<Arc<AppState>>,
    Path(club_id): Path<u32>,
) -> impl IntoResponse {
    let store = state.store.lock().unwrap();
    if !store.clubs.contains_key(&club_id) {
        return (StatusCode::NOT_FOUND, Json(json!({"error": "club not found"}))).into_response();
    }
    let mut members: Vec<Member> = store
        .members
        .values()
        .filter(|m| m.club_id == club_id)
        .cloned()
        .collect();
    members.sort_by_key(|m| m.id);
    (StatusCode::OK, Json(members)).into_response()
}
