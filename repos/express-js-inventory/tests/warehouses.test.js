const { test } = require("node:test");
const assert = require("node:assert/strict");
const { startServer, api } = require("./helpers");

test("create and list warehouses", async () => {
  const { base, close } = startServer();
  try {
    const created = await api(base, "POST", "/api/warehouses", {
      name: "Main DC",
      location: "Austin, TX",
    });
    assert.equal(created.status, 201);
    assert.equal(created.json.name, "Main DC");

    const missingName = await api(base, "POST", "/api/warehouses", { location: "x" });
    assert.equal(missingName.status, 400);

    const list = await api(base, "GET", "/api/warehouses");
    assert.equal(list.status, 200);
    assert.equal(list.json.length, 1);

    const notFound = await api(base, "GET", "/api/warehouses/999");
    assert.equal(notFound.status, 404);
  } finally {
    await close();
  }
});
