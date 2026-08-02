use std::collections::HashMap;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Club {
    pub id: u32,
    pub name: String,
    pub description: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Member {
    pub id: u32,
    pub club_id: u32,
    pub name: String,
    pub email: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Book {
    pub id: u32,
    pub club_id: u32,
    pub title: String,
    pub author: String,
    pub status: String,
}

#[derive(Default)]
pub struct Store {
    pub clubs: HashMap<u32, Club>,
    pub members: HashMap<u32, Member>,
    pub books: HashMap<u32, Book>,
    pub next_club_id: u32,
    pub next_member_id: u32,
    pub next_book_id: u32,
}

impl Store {
    pub fn new() -> Self {
        Store {
            clubs: HashMap::new(),
            members: HashMap::new(),
            books: HashMap::new(),
            next_club_id: 1,
            next_member_id: 1,
            next_book_id: 1,
        }
    }
}

pub struct AppState {
    pub store: Mutex<Store>,
}

impl AppState {
    pub fn new() -> Self {
        AppState {
            store: Mutex::new(Store::new()),
        }
    }
}
