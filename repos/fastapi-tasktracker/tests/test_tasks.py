def _setup(client):
    user = client.post("/users", json={"username": "dev", "email": "dev@example.com"}).json()
    project = client.post("/projects", json={"name": "Mercury", "owner_id": user["id"]}).json()
    return user, project


def test_create_and_update_task(client):
    user, project = _setup(client)
    resp = client.post(
        "/tasks", json={"title": "Write tests", "project_id": project["id"], "assignee_id": user["id"]}
    )
    assert resp.status_code == 201
    task = resp.json()
    assert task["status"] == "todo"

    resp = client.patch(f"/tasks/{task['id']}", json={"status": "done"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"


def test_list_tasks_filters_by_status(client):
    user, project = _setup(client)
    client.post("/tasks", json={"title": "A", "project_id": project["id"]})
    t2 = client.post("/tasks", json={"title": "B", "project_id": project["id"]}).json()
    client.patch(f"/tasks/{t2['id']}", json={"status": "done"})

    resp = client.get("/tasks", params={"project_id": project["id"], "status": "done"})
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert ids == [t2["id"]]


def test_delete_task(client):
    user, project = _setup(client)
    task = client.post("/tasks", json={"title": "temp", "project_id": project["id"]}).json()
    resp = client.delete(f"/tasks/{task['id']}")
    assert resp.status_code == 204
    resp = client.get(f"/tasks/{task['id']}")
    assert resp.status_code == 404
