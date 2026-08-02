let warehouses = new Map();
let nextId = 1;

function createWarehouse({ name, location }) {
  const warehouse = { id: nextId++, name, location };
  warehouses.set(warehouse.id, warehouse);
  return warehouse;
}

function getWarehouse(id) {
  return warehouses.get(id);
}

function listWarehouses() {
  return [...warehouses.values()].sort((a, b) => a.id - b.id);
}

/** Test hook: wipe all state (each test file starts from an empty store). */
function resetStore() {
  warehouses = new Map();
  nextId = 1;
}

module.exports = { createWarehouse, getWarehouse, listWarehouses, resetStore };
