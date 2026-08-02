const express = require("express");
const { warehousesRouter } = require("./routes/warehouses");
const { itemsRouter } = require("./routes/items");
const { movementsRouter } = require("./routes/movements");

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/api/warehouses", warehousesRouter);
  app.use("/api/items", movementsRouter);
  app.use("/api/items", itemsRouter);
  return app;
}

module.exports = { createApp };
