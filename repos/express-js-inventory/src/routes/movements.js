const express = require("express");
const itemStore = require("../store/itemStore");
const movementStore = require("../store/movementStore");
const inventoryService = require("../services/inventoryService");

const movementsRouter = express.Router();

movementsRouter.post("/:itemId/movements", (req, res) => {
  const item = itemStore.getItem(Number(req.params.itemId));
  if (!item) {
    res.status(404).json({ error: "item not found" });
    return;
  }
  const body = req.body ?? {};
  const result = inventoryService.applyMovement(item, body);
  if (result.error) {
    res.status(400).json({ error: result.error });
    return;
  }
  res.status(201).json({ movement: result.movement, item: result.item });
});

movementsRouter.get("/:itemId/movements", (req, res) => {
  const item = itemStore.getItem(Number(req.params.itemId));
  if (!item) {
    res.status(404).json({ error: "item not found" });
    return;
  }
  res.status(200).json(movementStore.listMovementsForItem(item.id));
});

module.exports = { movementsRouter };
