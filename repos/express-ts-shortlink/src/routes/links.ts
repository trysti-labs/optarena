import express from "express";

import { MAX_URL_LENGTH } from "../config";
import { Link } from "../models/types";
import { isValidSlug, randomSlug } from "../services/slugService";
import * as store from "../store/linkStore";

export const linksRouter = express.Router();

linksRouter.post("/", (req, res) => {
  const body = req.body ?? {};
  const url = typeof body.url === "string" ? body.url : "";
  if (url.length === 0 || url.length > MAX_URL_LENGTH) {
    res.status(400).json({ error: "url is required" });
    return;
  }

  let slug: string;
  if (body.slug !== undefined) {
    if (typeof body.slug !== "string" || !isValidSlug(body.slug)) {
      res.status(400).json({ error: "invalid slug" });
      return;
    }
    slug = body.slug;
  } else {
    do {
      slug = randomSlug();
    } while (store.getLink(slug) !== undefined);
  }

  if (store.getLink(slug) !== undefined) {
    res.status(409).json({ error: "slug already taken" });
    return;
  }

  const link: Link = { slug, url, createdAt: new Date().toISOString() };
  store.createLink(link);
  res.status(201).json(link);
});

linksRouter.get("/", (_req, res) => {
  res.status(200).json(store.listLinks());
});

linksRouter.get("/:slug", (req, res) => {
  const link = store.getLink(req.params.slug);
  if (link === undefined) {
    res.status(404).json({ error: "link not found" });
    return;
  }
  res.status(200).json(link);
});

linksRouter.delete("/:slug", (req, res) => {
  if (!store.deleteLink(req.params.slug)) {
    res.status(404).json({ error: "link not found" });
    return;
  }
  res.status(204).send();
});
