const test = require("node:test");
const assert = require("node:assert");

const { startServer, api } = require("./helpers");

test("redirect 302s to the target and records the click", async () => {
  const { base, close } = startServer();
  try {
    await api(base, "POST", "/api/links", { url: "https://example.com/a", slug: "go-a" });

    const redirect = await api(base, "GET", "/r/go-a");
    assert.strictEqual(redirect.status, 302);
    assert.strictEqual(redirect.headers.get("location"), "https://example.com/a");

    await api(base, "GET", "/r/go-a");
    const stats = await api(base, "GET", "/api/links/go-a/stats");
    assert.strictEqual(stats.status, 200);
    assert.strictEqual(stats.json.total, 2);
  } finally {
    await close();
  }
});

test("unknown slug 404s and records nothing", async () => {
  const { base, close } = startServer();
  try {
    assert.strictEqual((await api(base, "GET", "/r/nope")).status, 404);
  } finally {
    await close();
  }
});
