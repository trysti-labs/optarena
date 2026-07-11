def test_create_and_get_user(client):
    resp = client.post(
        "/users", json={"username": "ada", "email": "ada@example.com", "full_name": "Ada Lovelace"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "ada"
    assert body["is_active"] is True

    resp = client.get(f"/users/{body['id']}")
    assert resp.status_code == 200
    assert resp.json()["email"] == "ada@example.com"


def test_list_users(client):
    client.post("/users", json={"username": "a", "email": "a@example.com"})
    client.post("/users", json={"username": "b", "email": "b@example.com"})
    resp = client.get("/users")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_get_missing_user_404(client):
    resp = client.get("/users/999")
    assert resp.status_code == 404
