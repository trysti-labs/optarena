let movements = new Map();
let nextId = 1;

function createMovement({ itemId, type, quantity, note, at }) {
  const movement = { id: nextId++, itemId, type, quantity, note, at };
  movements.set(movement.id, movement);
  return movement;
}

function listMovementsForItem(itemId) {
  return [...movements.values()]
    .filter((m) => m.itemId === itemId)
    .sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : b.id - a.id));
}

/** Test hook: wipe all state (each test file starts from an empty store). */
function resetStore() {
  movements = new Map();
  nextId = 1;
}

module.exports = { createMovement, listMovementsForItem, resetStore };
