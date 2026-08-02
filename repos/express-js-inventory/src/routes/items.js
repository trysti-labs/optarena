const express = require("express");
const warehouseStore = require("../store/warehouseStore");
const itemStore = require("../store/itemStore");

const itemsRouter = express.Router();

itemsRouter.post("/", (req, res) => {
  const body = req.body ?? {};
  const sku = typeof body.sku === "string" ? body.sku.trim() : "";
  const name = typeof body.name === "string" ? body.name.trim() : "";
  const warehouseId = Number(body.warehouseId);
  const quantity = body.quantity === undefined ? 0 : Number(body.quantity);
  const reorderThreshold = body.reorderThreshold === undefined ? 0 : Number(body.reorderThreshold);

  if (sku.length === 0) {
    res.status(400).json({ error: "sku is required" });
    return;
  }
  if (name.length === 0) {
    res.status(400).json({ error: "name is required" });
    return;
  }
  if (!Number.isInteger(warehouseId) || !warehouseStore.getWarehouse(warehouseId)) {
    res.status(400).json({ error: "warehouseId must reference an existing warehouse" });
    return;
  }
  if (!Number.isInteger(quantity) || quantity < 0) {
    res.status(400).json({ error: "quantity must be a non-negative integer" });
    return;
  }
  if (!Number.isInteger(reorderThreshold) || reorderThreshold < 0) {
    res.status(400).json({ error: "reorderThreshold must be a non-negative integer" });
    return;
  }
  if (itemStore.findBySku(sku)) {
    res.status(409).json({ error: "sku already in use" });
    return;
  }

  const item = itemStore.createItem({ sku, name, warehouseId, quantity, reorderThreshold });
  res.status(201).json(item);
});

itemsRouter.get("/", (req, res) => {
  let items = itemStore.listItems();
  if (req.query.warehouseId !== undefined) {
    const warehouseId = Number(req.query.warehouseId);
    items = items.filter((item) => item.warehouseId === warehouseId);
  }
  if (req.query.low === "true") {
    items = items.filter((item) => item.quantity <= item.reorderThreshold);
  }
  res.status(200).json(items);
});

itemsRouter.get("/:id", (req, res) => {
  const item = itemStore.getItem(Number(req.params.id));
  if (!item) {
    res.status(404).json({ error: "item not found" });
    return;
  }
  res.status(200).json(item);
});

itemsRouter.patch("/:id", (req, res) => {
  const item = itemStore.getItem(Number(req.params.id));
  if (!item) {
    res.status(404).json({ error: "item not found" });
    return;
  }
  const body = req.body ?? {};
  const patch = {};
  if (body.name !== undefined) {
    if (typeof body.name !== "string" || body.name.trim().length === 0) {
      res.status(400).json({ error: "name must be a non-empty string" });
      return;
    }
    patch.name = body.name.trim();
  }
  if (body.reorderThreshold !== undefined) {
    const reorderThreshold = Number(body.reorderThreshold);
    if (!Number.isInteger(reorderThreshold) || reorderThreshold < 0) {
      res.status(400).json({ error: "reorderThreshold must be a non-negative integer" });
      return;
    }
    patch.reorderThreshold = reorderThreshold;
  }
  const updated = itemStore.updateItem(item.id, patch);
  res.status(200).json(updated);
});

module.exports = { itemsRouter };
