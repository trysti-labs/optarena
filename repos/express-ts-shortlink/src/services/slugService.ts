import { RANDOM_SLUG_LENGTH, SLUG_MAX_LENGTH, SLUG_MIN_LENGTH } from "../config";

const SLUG_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";

/** A valid slug is 3-30 chars of lowercase a-z0-9 groups joined by single hyphens. */
export function isValidSlug(slug: string): boolean {
  return (
    slug.length >= SLUG_MIN_LENGTH &&
    slug.length <= SLUG_MAX_LENGTH &&
    SLUG_PATTERN.test(slug)
  );
}

export function randomSlug(): string {
  let out = "";
  for (let i = 0; i < RANDOM_SLUG_LENGTH; i++) {
    out += ALPHABET[Math.floor(Math.random() * ALPHABET.length)];
  }
  return out;
}
