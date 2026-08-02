package api

import "testing"

func makeAgent(t *testing.T, base string) float64 {
	t.Helper()
	r := doRequest(t, base, "POST", "/api/agents", map[string]any{"name": "Ada", "email": "ada@example.com"})
	return r.body["id"].(float64)
}

func TestCreateTicketValidatesAgent(t *testing.T) {
	srv := startServer(t)
	defer srv.Close()

	bad := doRequest(t, srv.URL, "POST", "/api/tickets", map[string]any{"subject": "Help", "agentId": 999})
	if bad.status != 400 {
		t.Fatalf("expected 400, got %d", bad.status)
	}

	agentID := makeAgent(t, srv.URL)
	created := doRequest(t, srv.URL, "POST", "/api/tickets", map[string]any{"subject": "Help", "agentId": agentID})
	if created.status != 201 {
		t.Fatalf("expected 201, got %d", created.status)
	}
	if created.body["status"] != "open" {
		t.Fatalf("expected default status open, got %v", created.body["status"])
	}
}

func TestListTicketsFiltersByStatusAndAgent(t *testing.T) {
	srv := startServer(t)
	defer srv.Close()

	a1 := makeAgent(t, srv.URL)
	a2 := makeAgent(t, srv.URL)

	t1 := doRequest(t, srv.URL, "POST", "/api/tickets", map[string]any{"subject": "A", "agentId": a1})
	doRequest(t, srv.URL, "POST", "/api/tickets", map[string]any{"subject": "B", "agentId": a1})
	doRequest(t, srv.URL, "POST", "/api/tickets", map[string]any{"subject": "C", "agentId": a2})

	doRequest(t, srv.URL, "PATCH", "/api/tickets/"+jsonNum(t1.body["id"]), map[string]any{"status": "closed"})

	byAgent := doRequest(t, srv.URL, "GET", "/api/tickets?agentId="+jsonNum(a1), nil)
	if len(byAgent.list) != 2 {
		t.Fatalf("expected 2 tickets for a1, got %d", len(byAgent.list))
	}

	byStatus := doRequest(t, srv.URL, "GET", "/api/tickets?status=closed", nil)
	if len(byStatus.list) != 1 {
		t.Fatalf("expected 1 closed ticket, got %d", len(byStatus.list))
	}
}

func TestUpdateTicketValidatesStatusAndPriority(t *testing.T) {
	srv := startServer(t)
	defer srv.Close()

	created := doRequest(t, srv.URL, "POST", "/api/tickets", map[string]any{"subject": "A"})
	id := jsonNum(created.body["id"])

	badStatus := doRequest(t, srv.URL, "PATCH", "/api/tickets/"+id, map[string]any{"status": "bogus"})
	if badStatus.status != 400 {
		t.Fatalf("expected 400 for bad status, got %d", badStatus.status)
	}

	ok := doRequest(t, srv.URL, "PATCH", "/api/tickets/"+id, map[string]any{"priority": "high"})
	if ok.status != 200 || ok.body["priority"] != "high" {
		t.Fatalf("unexpected update result: status=%d body=%v", ok.status, ok.body)
	}
}
