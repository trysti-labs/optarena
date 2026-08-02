pub mod handlers;
pub mod state;

use std::sync::Arc;

use axum::routing::{get, patch, post};
use axum::Router;

use handlers::{books, clubs, members};
use state::AppState;

pub fn app() -> Router {
    let state = Arc::new(AppState::new());

    Router::new()
        .route("/api/clubs", post(clubs::create_club).get(clubs::list_clubs))
        .route("/api/clubs/:club_id", get(clubs::get_club))
        .route(
            "/api/clubs/:club_id/members",
            post(members::create_member).get(members::list_members),
        )
        .route(
            "/api/clubs/:club_id/books",
            post(books::create_book).get(books::list_books),
        )
        .route("/api/books/:book_id", patch(books::update_book))
        .with_state(state)
}
