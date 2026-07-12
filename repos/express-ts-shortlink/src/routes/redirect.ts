import express from "express";

import * as store from "../store/linkStore";

export const redirectRouter = express.Router();

redirectRouter.get("/:slug", (req, res) => {
  const link = store.getLink(req.params.slug);
  if (link === undefined) {
    res.status(404).json({ error: "link not found" });
    return;
  }
  store.recordClick(link.slug, new Date().toISOString());
  res.redirect(302, link.url);
});
