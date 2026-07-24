/**
 * test/conformance-runner.mjs
 * ───────────────────────────
 * H-09 conformance harness: executes oracle primitives (snapshot /
 * changedFiles / checkExpected / writeSetupFiles) against workspaces
 * prepared by the Python test suite, and prints the results as JSON so
 * tests/test_conformance.py can assert the JS oracle behaves byte-for-byte
 * like the Python one on the same inputs.
 *
 * Protocol: `node conformance-runner.mjs <job-file.json>` where the job file
 * is {"jobs": [{id, op, dir, ...}]}; output is {"results": {id: ...}} on
 * stdout. Ops:
 *   snapshot        -> {rel: sha1, ...}
 *   changed_files   -> [rel, ...] (against a supplied `before` map)
 *   check_expected  -> [failure-string, ...]
 *   write_setup     -> {error: message|null}   (containment behavior)
 *   apply_disruptions -> {fired: [str, ...], error: message|null}
 *                        (job.case, job.after_index; `fired` reflects the
 *                        workspace's state AFTER firing - the caller's own
 *                        snapshot before/after is what actually gets compared)
 */
import fs from 'node:fs';
import process from 'node:process';
import {
  snapshot, changedFiles, checkExpected, writeSetupFiles, applyDisruptions,
} from '../src/oracle.js';

const jobFile = process.argv[2];
if (!jobFile) {
  console.error('usage: node conformance-runner.mjs <job-file.json>');
  process.exit(2);
}
const { jobs } = JSON.parse(fs.readFileSync(jobFile, 'utf-8'));
const results = {};

for (const job of jobs) {
  switch (job.op) {
    case 'snapshot': {
      results[job.id] = Object.fromEntries([...snapshot(job.dir).entries()].sort());
      break;
    }
    case 'changed_files': {
      const before = new Map(Object.entries(job.before || {}));
      results[job.id] = changedFiles(before, job.dir).sort();
      break;
    }
    case 'check_expected': {
      results[job.id] = checkExpected(job.created || [], job.spec || [], job.dir);
      break;
    }
    case 'write_setup': {
      try {
        writeSetupFiles(job.dir, job.files || {});
        results[job.id] = { error: null };
      } catch (e) {
        results[job.id] = { error: String(e.message || e) };
      }
      break;
    }
    case 'apply_disruptions': {
      try {
        const fired = applyDisruptions(job.case, job.dir, job.after_index, new Set());
        results[job.id] = { fired, error: null };
      } catch (e) {
        results[job.id] = { fired: [], error: String(e.message || e) };
      }
      break;
    }
    default:
      results[job.id] = { error: `unknown op ${job.op}` };
  }
}

process.stdout.write(JSON.stringify({ results }));
