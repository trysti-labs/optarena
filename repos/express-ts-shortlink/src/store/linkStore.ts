import { Link } from "../models/types";

const links = new Map<string, Link>();
const clicks = new Map<string, string[]>(); // slug -> ISO click timestamps

export function createLink(link: Link): void {
  links.set(link.slug, link);
  clicks.set(link.slug, []);
}

export function getLink(slug: string): Link | undefined {
  return links.get(slug);
}

export function listLinks(): Link[] {
  return [...links.values()].sort((a, b) => (a.slug < b.slug ? -1 : 1));
}

export function deleteLink(slug: string): boolean {
  clicks.delete(slug);
  return links.delete(slug);
}

export function recordClick(slug: string, at: string): void {
  const existing = clicks.get(slug);
  if (existing) {
    existing.push(at);
  }
}

export function clicksFor(slug: string): string[] {
  return [...(clicks.get(slug) ?? [])];
}

/** Test hook: wipe all state (each test file starts from an empty store). */
export function resetStore(): void {
  links.clear();
  clicks.clear();
}
