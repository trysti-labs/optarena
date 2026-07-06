use actix_web::{web, App, HttpResponse, HttpServer};

async fn health() -> HttpResponse {
    HttpResponse::Ok().json(serde_json::json!({"status": "ok"}))
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    let srv = HttpServer::new(|| App::new().route("/health", web::get().to(health)));
    let _ = srv;
    Ok(())
}
