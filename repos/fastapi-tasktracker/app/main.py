from __future__ import annotations

from fastapi import FastAPI

from app.routers import projects, tasks, users

app = FastAPI(title="Task Tracker")
app.include_router(users.router)
app.include_router(projects.router)
app.include_router(tasks.router)


@app.get("/health")
def health():
    return {"status": "ok"}
