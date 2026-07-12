const test = require("node:test");
const assert = require("node:assert");

const { startServer, api, store } = require("./helpers");

test("stats aggregate clicks by UTC day with earliest-peak tie-break", async () => {
  const { base, close } = startServer();
  try {
    await api(base, "POST", "/api/links", { url: "https://example.com", slug: "tracked" });
    store.recordClick("tracked", "2026-07-01T09:00:00.000Z");
    store.recordClick("tracked", "2026-07-01T21:30:00.000Z");
    store.recordClick("tracked", "2026-07-03T05:00:00.000Z");

    const stats = await api(base, "GET", "/api/links/tracked/stats");
    assert.strictEqual(stats.status, 200);
    assert.deepStrictEqual(stats.json, {
      total: 3,
      byDay: { "2026-07-01": 2, "2026-07-03": 1 },
      peakDay: "2026-07-01",
      peakCount: 2,
    });
  } finally {
    await close();
  }
});

test("stats for an unclicked link are empty", async () => {
  const { base, close } = startServer();
  try {
    await api(base, "POST", "/api/links", { url: "https://example.com", slug: "fresh" });
    const stats = await api(base, "GET", "/api/links/fresh/stats");
    assert.deepStrictEqual(stats.json, { total: 0, byDay: {}, peakDay: null, peakCount: 0 });
  } finally {
    await close();
  }
});

test("stats for an unknown slug 404", async () => {
  const { base, close } = startServer();
  try {
    assert.strictEqual((await api(base, "GET", "/api/links/nope/stats")).status, 404);
  } finally {
    await close();
  }
});
