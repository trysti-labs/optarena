const itemStore = require("../store/itemStore");
const movementStore = require("../store/movementStore");

const VALID_TYPES = new Set(["IN", "OUT"]);

/**
 * Applies a stock movement to an item: IN adds to quantity, OUT subtracts.
 * Returns { error } on validation failure, or { movement, item } on success.
 * Rejects an OUT movement that would take quantity below zero.
 */
function applyMovement(item, { type, quantity, note }) {
  if (!VALID_TYPES.has(type)) {
    return { error: "type must be IN or OUT" };
  }
  if (typeof quantity !== "number" || !Number.isInteger(quantity) || quantity <= 0) {
    return { error: "quantity must be a positive integer" };
  }

  const delta = type === "IN" ? quantity : -quantity;
  const newQuantity = item.quantity + delta;
  if (newQuantity < 0) {
    return { error: "movement would take quantity below zero" };
  }

  itemStore.updateItem(item.id, { quantity: newQuantity });
  const movement = movementStore.createMovement({
    itemId: item.id,
    type,
    quantity,
    note: typeof note === "string" ? note : null,
    at: new Date().toISOString(),
  });
  return { movement, item: itemStore.getItem(item.id) };
}

module.exports = { applyMovement };
