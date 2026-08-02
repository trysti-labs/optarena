package api

import "testing"

func TestCreateAndListAgents(t *testing.T) {
	srv := startServer(t)
	defer srv.Close()

	created := doRequest(t, srv.URL, "POST", "/api/agents", map[string]any{"name": "Ada", "email": "ada@example.com"})
	if created.status != 201 {
		t.Fatalf("expected 201, got %d", created.status)
	}
	if created.body["name"] != "Ada" {
		t.Fatalf("unexpected name: %v", created.body["name"])
	}

	missingEmail := doRequest(t, srv.URL, "POST", "/api/agents", map[string]any{"name": "Bob"})
	if missingEmail.status != 400 {
		t.Fatalf("expected 400, got %d", missingEmail.status)
	}

	list := doRequest(t, srv.URL, "GET", "/api/agents", nil)
	if len(list.list) != 1 {
		t.Fatalf("expected 1 agent, got %d", len(list.list))
	}

	notFound := doRequest(t, srv.URL, "GET", "/api/agents/999", nil)
	if notFound.status != 404 {
		t.Fatalf("expected 404, got %d", notFound.status)
	}
}
