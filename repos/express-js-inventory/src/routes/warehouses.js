const express = require("express");
const store = require("../store/warehouseStore");

const warehousesRouter = express.Router();

warehousesRouter.post("/", (req, res) => {
  const body = req.body ?? {};
  const name = typeof body.name === "string" ? body.name.trim() : "";
  const location = typeof body.location === "string" ? body.location.trim() : "";
  if (name.length === 0) {
    res.status(400).json({ error: "name is required" });
    return;
  }
  if (location.length === 0) {
    res.status(400).json({ error: "location is required" });
    return;
  }
  const warehouse = store.createWarehouse({ name, location });
  res.status(201).json(warehouse);
});

warehousesRouter.get("/", (_req, res) => {
  res.status(200).json(store.listWarehouses());
});

warehousesRouter.get("/:id", (req, res) => {
  const warehouse = store.getWarehouse(Number(req.params.id));
  if (!warehouse) {
    res.status(404).json({ error: "warehouse not found" });
    return;
  }
  res.status(200).json(warehouse);
});

module.exports = { warehousesRouter };
