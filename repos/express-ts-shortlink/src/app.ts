import express from "express";

import { linksRouter } from "./routes/links";
import { redirectRouter } from "./routes/redirect";
import { statsRouter } from "./routes/stats";

export function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/api/links", statsRouter);
  app.use("/api/links", linksRouter);
  app.use("/r", redirectRouter);
  return app;
}
