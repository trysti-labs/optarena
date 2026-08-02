package api

import "testing"

func TestCommentsScopedToTicket(t *testing.T) {
	srv := startServer(t)
	defer srv.Close()

	t1 := doRequest(t, srv.URL, "POST", "/api/tickets", map[string]any{"subject": "A"})
	t2 := doRequest(t, srv.URL, "POST", "/api/tickets", map[string]any{"subject": "B"})
	id1 := jsonNum(t1.body["id"])
	id2 := jsonNum(t2.body["id"])

	missing := doRequest(t, srv.URL, "POST", "/api/tickets/"+id1+"/comments", map[string]any{"author": "", "body": "hi"})
	if missing.status != 400 {
		t.Fatalf("expected 400 for missing author, got %d", missing.status)
	}

	doRequest(t, srv.URL, "POST", "/api/tickets/"+id1+"/comments", map[string]any{"author": "Ada", "body": "first"})
	doRequest(t, srv.URL, "POST", "/api/tickets/"+id1+"/comments", map[string]any{"author": "Ada", "body": "second"})
	doRequest(t, srv.URL, "POST", "/api/tickets/"+id2+"/comments", map[string]any{"author": "Bob", "body": "other ticket"})

	list1 := doRequest(t, srv.URL, "GET", "/api/tickets/"+id1+"/comments", nil)
	if len(list1.list) != 2 {
		t.Fatalf("expected 2 comments on ticket 1, got %d", len(list1.list))
	}

	notFound := doRequest(t, srv.URL, "GET", "/api/tickets/999/comments", nil)
	if notFound.status != 404 {
		t.Fatalf("expected 404, got %d", notFound.status)
	}
}
