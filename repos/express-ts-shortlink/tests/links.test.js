const test = require("node:test");
const assert = require("node:assert");

const { startServer, api } = require("./helpers");

test("create, fetch, list, delete a link", async () => {
  const { base, close } = startServer();
  try {
    const created = await api(base, "POST", "/api/links", {
      url: "https://example.com/docs",
      slug: "docs",
    });
    assert.strictEqual(created.status, 201);
    assert.strictEqual(created.json.slug, "docs");
    assert.strictEqual(created.json.url, "https://example.com/docs");

    const fetched = await api(base, "GET", "/api/links/docs");
    assert.strictEqual(fetched.status, 200);
    assert.strictEqual(fetched.json.url, "https://example.com/docs");

    const listed = await api(base, "GET", "/api/links");
    assert.strictEqual(listed.status, 200);
    assert.deepStrictEqual(listed.json.map((l) => l.slug), ["docs"]);

    const deleted = await api(base, "DELETE", "/api/links/docs");
    assert.strictEqual(deleted.status, 204);
    assert.strictEqual((await api(base, "GET", "/api/links/docs")).status, 404);
  } finally {
    await close();
  }
});

test("random slug assigned when none given", async () => {
  const { base, close } = startServer();
  try {
    const created = await api(base, "POST", "/api/links", { url: "https://example.com" });
    assert.strictEqual(created.status, 201);
    assert.match(created.json.slug, /^[a-z0-9]{6}$/);
  } finally {
    await close();
  }
});

test("validation and conflicts", async () => {
  const { base, close } = startServer();
  try {
    assert.strictEqual((await api(base, "POST", "/api/links", {})).status, 400);
    assert.strictEqual(
      (await api(base, "POST", "/api/links", { url: "https://x.dev", slug: "NO CAPS" })).status,
      400,
    );
    await api(base, "POST", "/api/links", { url: "https://x.dev", slug: "taken" });
    const dup = await api(base, "POST", "/api/links", { url: "https://y.dev", slug: "taken" });
    assert.strictEqual(dup.status, 409);
  } finally {
    await close();
  }
});
