import express from "express";

import { summarizeClicks } from "../services/statsService";
import * as store from "../store/linkStore";

export const statsRouter = express.Router();

statsRouter.get("/:slug/stats", (req, res) => {
  const link = store.getLink(req.params.slug);
  if (link === undefined) {
    res.status(404).json({ error: "link not found" });
    return;
  }
  res.status(200).json(summarizeClicks(store.clicksFor(link.slug)));
});
