let items = new Map();
let nextId = 1;

function createItem({ sku, name, warehouseId, quantity, reorderThreshold }) {
  const item = {
    id: nextId++,
    sku,
    name,
    warehouseId,
    quantity,
    reorderThreshold,
  };
  items.set(item.id, item);
  return item;
}

function getItem(id) {
  return items.get(id);
}

function findBySku(sku) {
  for (const item of items.values()) {
    if (item.sku === sku) return item;
  }
  return undefined;
}

function listItems() {
  return [...items.values()].sort((a, b) => a.id - b.id);
}

function updateItem(id, patch) {
  const item = items.get(id);
  if (!item) return undefined;
  Object.assign(item, patch);
  return item;
}

/** Test hook: wipe all state (each test file starts from an empty store). */
function resetStore() {
  items = new Map();
  nextId = 1;
}

module.exports = {
  createItem,
  getItem,
  findBySku,
  listItems,
  updateItem,
  resetStore,
};
