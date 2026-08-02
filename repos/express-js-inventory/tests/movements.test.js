const { test } = require("node:test");
const assert = require("node:assert/strict");
const { startServer, api } = require("./helpers");

async function makeItem(base, quantity = 5, reorderThreshold = 0) {
  const w = await api(base, "POST", "/api/warehouses", { name: "W", location: "x" });
  const item = await api(base, "POST", "/api/items", {
    sku: "SKU",
    name: "Widget",
    warehouseId: w.json.id,
    quantity,
    reorderThreshold,
  });
  return item.json.id;
}

test("IN movement increases quantity, OUT decreases it", async () => {
  const { base, close } = startServer();
  try {
    const itemId = await makeItem(base, 5);

    const inMove = await api(base, "POST", `/api/items/${itemId}/movements`, {
      type: "IN",
      quantity: 3,
    });
    assert.equal(inMove.status, 201);
    assert.equal(inMove.json.item.quantity, 8);

    const outMove = await api(base, "POST", `/api/items/${itemId}/movements`, {
      type: "OUT",
      quantity: 2,
    });
    assert.equal(outMove.status, 201);
    assert.equal(outMove.json.item.quantity, 6);
  } finally {
    await close();
  }
});

test("OUT movement cannot take quantity below zero", async () => {
  const { base, close } = startServer();
  try {
    const itemId = await makeItem(base, 2);
    const rejected = await api(base, "POST", `/api/items/${itemId}/movements`, {
      type: "OUT",
      quantity: 5,
    });
    assert.equal(rejected.status, 400);

    const item = await api(base, "GET", `/api/items/${itemId}`);
    assert.equal(item.json.quantity, 2);
  } finally {
    await close();
  }
});

test("movements list is newest first and scoped to the item", async () => {
  const { base, close } = startServer();
  try {
    const itemA = await makeItem(base, 5);
    const itemB = await makeItem(base, 5);

    await api(base, "POST", `/api/items/${itemA}/movements`, { type: "IN", quantity: 1 });
    await api(base, "POST", `/api/items/${itemA}/movements`, { type: "IN", quantity: 2 });
    await api(base, "POST", `/api/items/${itemB}/movements`, { type: "IN", quantity: 9 });

    const list = await api(base, "GET", `/api/items/${itemA}/movements`);
    assert.equal(list.status, 200);
    assert.equal(list.json.length, 2);
    assert.equal(list.json[0].quantity, 2);
    assert.equal(list.json[1].quantity, 1);
  } finally {
    await close();
  }
});
