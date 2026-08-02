package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"

	"gin-helpdesk/internal/store"
)

// jsonNum renders a value decoded from JSON (always float64 for numbers via
// map[string]any) back into a path/query-safe integer string.
func jsonNum(v any) string {
	return strconv.Itoa(int(v.(float64)))
}

// startServer resets the in-memory store and starts an httptest server
// backed by a fresh router. Callers must `defer srv.Close()`.
func startServer(t *testing.T) *httptest.Server {
	t.Helper()
	store.Reset()
	return httptest.NewServer(NewRouter())
}

type apiResult struct {
	status int
	body   map[string]any
	list   []map[string]any
}

func doRequest(t *testing.T, base, method, path string, payload any) apiResult {
	t.Helper()
	var buf bytes.Buffer
	if payload != nil {
		if err := json.NewEncoder(&buf).Encode(payload); err != nil {
			t.Fatalf("encode payload: %v", err)
		}
	}
	req, err := http.NewRequest(method, base+path, &buf)
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("do request: %v", err)
	}
	defer resp.Body.Close()

	result := apiResult{status: resp.StatusCode}
	dec := json.NewDecoder(resp.Body)
	var raw json.RawMessage
	if err := dec.Decode(&raw); err == nil {
		var asObj map[string]any
		if err := json.Unmarshal(raw, &asObj); err == nil {
			result.body = asObj
		} else {
			var asList []map[string]any
			if err := json.Unmarshal(raw, &asList); err == nil {
				result.list = asList
			}
		}
	}
	return result
}
