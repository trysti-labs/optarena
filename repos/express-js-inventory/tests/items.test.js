const { test } = require("node:test");
const assert = require("node:assert/strict");
const { startServer, api } = require("./helpers");

async function makeWarehouse(base, name = "Main DC") {
  const r = await api(base, "POST", "/api/warehouses", { name, location: "x" });
  return r.json.id;
}

test("create item requires an existing warehouse", async () => {
  const { base, close } = startServer();
  try {
    const bad = await api(base, "POST", "/api/items", {
      sku: "SKU1",
      name: "Widget",
      warehouseId: 999,
    });
    assert.equal(bad.status, 400);

    const warehouseId = await makeWarehouse(base);
    const created = await api(base, "POST", "/api/items", {
      sku: "SKU1",
      name: "Widget",
      warehouseId,
      quantity: 5,
      reorderThreshold: 2,
    });
    assert.equal(created.status, 201);
    assert.equal(created.json.quantity, 5);

    const dup = await api(base, "POST", "/api/items", {
      sku: "SKU1",
      name: "Widget 2",
      warehouseId,
    });
    assert.equal(dup.status, 409);
  } finally {
    await close();
  }
});

test("list items filters by warehouseId and low stock", async () => {
  const { base, close } = startServer();
  try {
    const w1 = await makeWarehouse(base, "W1");
    const w2 = await makeWarehouse(base, "W2");
    await api(base, "POST", "/api/items", {
      sku: "A",
      name: "A",
      warehouseId: w1,
      quantity: 1,
      reorderThreshold: 5,
    });
    await api(base, "POST", "/api/items", {
      sku: "B",
      name: "B",
      warehouseId: w1,
      quantity: 10,
      reorderThreshold: 5,
    });
    await api(base, "POST", "/api/items", {
      sku: "C",
      name: "C",
      warehouseId: w2,
      quantity: 1,
      reorderThreshold: 5,
    });

    const byWarehouse = await api(base, "GET", `/api/items?warehouseId=${w1}`);
    assert.equal(byWarehouse.json.length, 2);

    const low = await api(base, "GET", "/api/items?low=true");
    assert.equal(low.json.length, 2);

    const both = await api(base, "GET", `/api/items?warehouseId=${w1}&low=true`);
    assert.equal(both.json.length, 1);
    assert.equal(both.json[0].sku, "A");
  } finally {
    await close();
  }
});

test("patch item updates name and reorderThreshold only", async () => {
  const { base, close } = startServer();
  try {
    const warehouseId = await makeWarehouse(base);
    const created = await api(base, "POST", "/api/items", {
      sku: "A",
      name: "A",
      warehouseId,
      quantity: 3,
      reorderThreshold: 1,
    });
    const patched = await api(base, "PATCH", `/api/items/${created.json.id}`, {
      name: "Renamed",
      reorderThreshold: 9,
    });
    assert.equal(patched.status, 200);
    assert.equal(patched.json.name, "Renamed");
    assert.equal(patched.json.reorderThreshold, 9);
    assert.equal(patched.json.quantity, 3);
  } finally {
    await close();
  }
});
