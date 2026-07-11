def _make_user(client):
    return client.post("/users", json={"username": "owner", "email": "owner@example.com"}).json()


def test_create_and_get_project(client):
    user = _make_user(client)
    resp = client.post(
        "/projects", json={"name": "Apollo", "description": "moon", "owner_id": user["id"]}
    )
    assert resp.status_code == 201
    project = resp.json()
    assert project["name"] == "Apollo"

    resp = client.get(f"/projects/{project['id']}")
    assert resp.status_code == 200


def test_project_tasks_empty_initially(client):
    user = _make_user(client)
    project = client.post("/projects", json={"name": "Gemini", "owner_id": user["id"]}).json()
    resp = client.get(f"/projects/{project['id']}/tasks")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_missing_project_404(client):
    resp = client.get("/projects/999")
    assert resp.status_code == 404


def test_project_progress_counts_statuses(client):
    user = _make_user(client)
    project = client.post("/projects", json={"name": "Skylab", "owner_id": user["id"]}).json()
    t1 = client.post("/tasks", json={"title": "a", "project_id": project["id"]}).json()
    client.post("/tasks", json={"title": "b", "project_id": project["id"]})
    client.patch(f"/tasks/{t1['id']}", json={"status": "done"})

    resp = client.get(f"/projects/{project['id']}/progress")
    assert resp.status_code == 200
    assert resp.json() == {"total": 2, "todo": 1, "in_progress": 0, "done": 1, "percent_done": 50.0}
