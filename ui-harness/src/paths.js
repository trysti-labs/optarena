/**
 * src/paths.js
 * ────────────
 * Deterministic paths + run configuration shared by wdio.conf.js (launcher
 * process) and the spec (worker process). Both import this module so they agree
 * on the same workspace / storage locations without round-tripping through env
 * vars (which don't cross the launcher→worker boundary).
 */
import os from 'node:os';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { getExtension } from './extensions.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** Harness root: ui-harness/ */
export const PROJECT_DIR = path.resolve(__dirname, '..');

/** Run configuration (overridable via env). */
export const EXT = (process.env.EXT || 'cline').toLowerCase();
export const DESCRIPTOR = getExtension(EXT);
export const API_MODE = (process.env.API_KIND || process.env.CLINE_API || 'ollama').toLowerCase();
export const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:11434';
export const MODEL_ID = process.env.MODEL_ID || 'llama3.2';
// H-08: forwarded from the Python driver (scenario.backend.api_key). Default
// 'optarena' keeps Ollama (which ignores the key) working unchanged; a keyed
// OpenAI-compatible backend now gets its real credential instead of a stub.
export const API_KEY = process.env.API_KEY || 'optarena';

/** Optional: OptArena writes one JSON line per case here (see test/agent.e2e.js). */
export const RESULTS_FILE = process.env.RESULTS_FILE || '';

/**
 * Per-extension dirs (suffixed so different EXT runs don't collide and don't
 * re-trigger setup). onPrepare wipes + recreates them each run.
 *
 * These live OUTSIDE the repo checkout (system temp dir, stable per-extension
 * name so re-runs reuse the downloaded VS Code/profile), not under
 * PROJECT_DIR: the agent driven in this workspace gets auto-approved file
 * reads, and a workspace inside the checkout put the whole repo - including
 * `.env` secrets and every case's reference_solution - one `../` away from
 * the code under test. Override with OPTARENA_UI_DIR to pin a location.
 */
const UI_BASE = process.env.OPTARENA_UI_DIR
  || path.join(os.tmpdir(), 'optarena-ui-harness');
export const STORAGE_PATH = path.join(UI_BASE, `.vscode-storage-${EXT}`);
export const WORKSPACE = path.join(UI_BASE, `.workspace-${EXT}`);

/** Where wdio-vscode-service places the VS Code user-data-dir (see service.js). */
export const USER_DATA_DIR = path.join(STORAGE_PATH, 'settings');
export const GLOBAL_STORAGE_DIR = path.join(USER_DATA_DIR, 'User', 'globalStorage');
export const STATE_DB = path.join(GLOBAL_STORAGE_DIR, 'state.vscdb');

/** Test cases: arena's canonical dir (or CASES_DIR override). */
export const PROMPTS_DIR = process.env.CASES_DIR
  || path.resolve(PROJECT_DIR, '..', 'optarena', 'cases');

/**
 * Locate the installed extension directory (highest version) for the active
 * descriptor. Overridable via EXT_PATH.
 */
export function findExtensionPath() {
  if (process.env.EXT_PATH) return process.env.EXT_PATH;
  const prefix = DESCRIPTOR.extDirPrefix.toLowerCase();
  const extRoot = path.join(os.homedir(), '.vscode', 'extensions');
  let candidates = [];
  try {
    candidates = fs.readdirSync(extRoot)
      .filter((d) => d.toLowerCase().startsWith(prefix))
      .map((d) => path.join(extRoot, d))
      .filter((p) => fs.existsSync(path.join(p, 'package.json')));
  } catch {
    /* ignore */
  }
  if (candidates.length === 0) {
    throw new Error(
      `${DESCRIPTOR.label} extension (${DESCRIPTOR.extDirPrefix}) not found under ${extRoot}. ` +
      `Install it in VS Code, or set EXT_PATH to its directory.`,
    );
  }
  candidates.sort();
  return candidates[candidates.length - 1];
}
