/**
 * src/oracle.js
 * ─────────────
 * File-system oracle (ported from the Python harness's workspace.py) plus the
 * test-case loader.  The workspace diff is the primary correctness signal: the
 * test passes when Cline creates files matching each case's expected_files spec.
 */
import fs from 'node:fs';
import path from 'node:path';
import { PROMPTS_DIR } from './paths.js';

const IGNORE_DIRS = new Set(['.git', '.vscode', '.cline', 'node_modules']);

/**
 * Recursively map each workspace file (relative POSIX path) to a content
 * signature (`size:mtimeMs`).  Using a signature rather than a bare set lets us
 * detect files that were *modified* in place (e.g. "modify" cases that edit a
 * pre-existing setup file), not just newly created ones.
 */
export function snapshot(root) {
  const out = new Map();
  function walk(dir) {
    let entries = [];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      if (e.name.startsWith('.') && e.isDirectory()) continue;
      if (IGNORE_DIRS.has(e.name)) continue;
      const full = path.join(dir, e.name);
      if (e.isDirectory()) {
        walk(full);
      } else if (e.isFile()) {
        const rel = path.relative(root, full).split(path.sep).join('/');
        let sig = '0:0';
        try {
          const st = fs.statSync(full);
          sig = `${st.size}:${st.mtimeMs}`;
        } catch { /* ignore */ }
        out.set(rel, sig);
      }
    }
  }
  walk(root);
  return out;
}

/** Files that are new OR whose content changed since `before`. */
export function changedFiles(before, root) {
  const current = snapshot(root);
  const out = [];
  for (const [rel, sig] of current) {
    if (!before.has(rel) || before.get(rel) !== sig) out.push(rel);
  }
  return out;
}

function globToRegExp(pattern) {
  // Support exact names and simple *, ? globs; match against the basename.
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  const re = escaped.replace(/\*/g, '.*').replace(/\?/g, '.');
  return new RegExp(`^${re}$`, 'i');
}

/**
 * Validate created files against expected_files. Returns a list of failure
 * strings; empty means the case passed.
 */
export function checkExpected(created, expectedSpec, root) {
  const failures = [];
  for (const spec of expectedSpec || []) {
    const pattern = spec.path_pattern;
    const re = globToRegExp(pattern);
    const match = created.find((rel) => re.test(path.basename(rel)) || re.test(rel));
    if (!match) {
      failures.push(`expected file matching "${pattern}" not created (got: ${created.join(', ') || 'none'})`);
      continue;
    }
    let content = '';
    try {
      content = fs.readFileSync(path.join(root, match), 'utf-8');
    } catch (e) {
      failures.push(`could not read "${match}": ${e.message}`);
      continue;
    }
    const lower = content.toLowerCase();
    for (const needle of spec.content_patterns || []) {
      if (!lower.includes(String(needle).toLowerCase())) {
        failures.push(`"${match}" missing expected content "${needle}"`);
      }
    }
  }
  return failures;
}

/** Write a case's setup_files into the workspace before the case runs. */
export function writeSetupFiles(root, setupFiles) {
  for (const [rel, content] of Object.entries(setupFiles || {})) {
    const dest = path.join(root, rel);
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.writeFileSync(dest, content, 'utf-8');
  }
}

/** Load all prompt JSON cases from the shared prompts dir. */
export function loadCases() {
  const files = fs.readdirSync(PROMPTS_DIR).filter((f) => f.endsWith('.json')).sort();
  return files.map((f) => JSON.parse(fs.readFileSync(path.join(PROMPTS_DIR, f), 'utf-8')));
}
