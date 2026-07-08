/**
 * src/oracle.js
 * ─────────────
 * File-system oracle (ported from the Python harness's workspace.py) plus the
 * test-case loader.  The workspace diff is the primary correctness signal: the
 * test passes when Cline creates files matching each case's expected_files spec.
 */
import fs from 'node:fs';
import path from 'node:path';
import { execSync, execFileSync } from 'node:child_process';
import { PROMPTS_DIR } from './paths.js';

const IGNORE_DIRS = new Set(['.git', '.vscode', '.cline', '.aider', 'node_modules', '__pycache__']);

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
      // Skip only the known tool/VCS directories (mirrors the Python oracle's
      // IGNORE_DIRS) - NOT every dot-directory: cases legitimately expect
      // files under e.g. `.github/workflows/`, and skipping all dot-dirs made
      // those files invisible to the diff, auto-failing the CI cases.
      if (e.isDirectory() && IGNORE_DIRS.has(e.name)) continue;
      // aider writes .aider.* FILES (history, tags cache) - tool bookkeeping,
      // not model output; keep them out of the diff (same as the Python side).
      if (e.isFile() && e.name.startsWith('.aider')) continue;
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
    for (const needle of spec.not_content_patterns || []) {
      if (lower.includes(String(needle).toLowerCase())) {
        failures.push(`"${match}" contains forbidden content "${needle}"`);
      }
    }
    for (const pattern of spec.regex_patterns || []) {
      try {
        if (!new RegExp(pattern, 'i').test(content)) {
          failures.push(`"${match}" does not match regex "${pattern}"`);
        }
      } catch (e) {
        failures.push(`invalid regex "${pattern}": ${e.message}`);
      }
    }
    if (Number.isInteger(spec.min_lines) && spec.min_lines > 0) {
      const nLines = content.split('\n').filter((l, i, a) => i < a.length - 1 || l !== '').length;
      if (nLines < spec.min_lines) {
        failures.push(`"${match}" has ${nLines} line(s), expected >= ${spec.min_lines}`);
      }
    }
  }
  return failures;
}

const DOCKER_IMAGE_DEFAULT = 'optarena-tester:latest';
let dockerChecked = false;
let dockerOk = false;
let dockerWarned = false;

function dockerAvailable() {
  if (dockerChecked) return dockerOk;
  dockerChecked = true;
  try {
    execFileSync('docker', ['info'], { stdio: 'pipe', timeout: 10000 });
    dockerOk = true;
  } catch {
    dockerOk = false;
  }
  return dockerOk;
}

function dockerImageAvailable(image) {
  try {
    execFileSync('docker', ['image', 'inspect', image], { stdio: 'pipe', timeout: 10000 });
    return true;
  } catch {
    return false;
  }
}

function newOracleInfo(cmd) {
  return { check_command: cmd || null, ran: false, sandbox: null, image: null,
           exit_code: null, duration_s: null, output: '' };
}

// One shared container PER DISTINCT IMAGE for the WHOLE harness run - mirrors
// cases._active_sandboxes on the Python side. Keyed by image tag (not a single
// slot) because a run mixing e.g. a Python case and a Go case needs both
// toolchains live at once, and each case must exec into the container that
// actually has its toolchain (a case's `docker_image` field).
const activeSandboxes = new Map();

/**
 * Start a shared sandbox container for one image, mounting `root` (the
 * harness's fixed workspace directory) once. Returns the sandbox handle, or
 * null when Docker isn't available/enabled or the image isn't built - callers
 * then fall back per-case (ephemeral docker run, or the host).
 */
export function startDockerSandbox(root, image) {
  image = image || process.env.OPTARENA_DOCKER_IMAGE || DOCKER_IMAGE_DEFAULT;
  if (process.env.OPTARENA_NO_DOCKER === '1' || !dockerAvailable()) return null;
  if (activeSandboxes.has(image)) return activeSandboxes.get(image);
  if (!dockerImageAvailable(image)) {
    console.error(`[optarena] Docker image '${image}' not found - check_command will run on the host. Run \`optarena docker build\`.`);
    return null;
  }
  const name = `optarena-sandbox-${Math.random().toString(16).slice(2, 14)}`;
  const resolvedRoot = path.resolve(root);
  try {
    execFileSync('docker', [
      'run', '-d', '--rm', '--name', name,
      '--network', 'none', '--memory', '2g', '--cpus', '2',
      '-v', `${resolvedRoot}:/workspace`, '-w', '/workspace',
      image, 'sleep', 'infinity',
    ], { stdio: 'pipe', timeout: 20000 });
  } catch (e) {
    console.error(`[optarena] could not start docker sandbox: ${e.message}`);
    return null;
  }
  const sandbox = { name, root: resolvedRoot, image };
  activeSandboxes.set(image, sandbox);
  console.log(`[optarena] docker sandbox: ${name} (image ${image}) - one container per image for this whole run`);
  return sandbox;
}

/**
 * Start one shared sandbox per distinct image the given cases need (their
 * `docker_image` field, defaulting to the base image). Returns the started
 * handles - pass the array to stopDockerSandboxes() when the run ends.
 */
export function startDockerSandboxes(root, cases) {
  const images = new Set(
    (cases || [])
      .filter((c) => c.check_command)
      .map((c) => c.docker_image || process.env.OPTARENA_DOCKER_IMAGE || DOCKER_IMAGE_DEFAULT),
  );
  return [...images].map((image) => startDockerSandbox(root, image)).filter(Boolean);
}

/** Stop a shared sandbox container (safe to call with null/already-stopped). */
export function stopDockerSandbox(sandbox) {
  if (!sandbox) return;
  try { execFileSync('docker', ['stop', '-t', '2', sandbox.name], { stdio: 'pipe' }); } catch { /* already gone */ }
  if (activeSandboxes.get(sandbox.image) === sandbox) activeSandboxes.delete(sandbox.image);
}

/** Stop every sandbox in the given array (from startDockerSandboxes). */
export function stopDockerSandboxes(sandboxes) {
  for (const s of sandboxes || []) stopDockerSandbox(s);
}

/**
 * Kill stray processes left in a shared container after a check_command
 * timeout (`timeout` TERMs the sh/test process, but a TERM'd Python test
 * never runs its `finally:`, so a server it spawned survives and holds its
 * port for every later case in this container). PID 1 (`sleep infinity`)
 * is excluded by `kill -1` semantics, so the container itself stays up.
 */
function reapSandbox(sandbox) {
  try {
    execFileSync('docker', ['exec', sandbox.name, 'sh', '-c', 'kill -9 -1 2>/dev/null; true'],
      { stdio: 'pipe', timeout: 10000 });
  } catch { /* best-effort */ }
}

function runInSandbox(cmd, root, timeoutMs, sandbox, info) {
  info.sandbox = 'docker';
  info.image = sandbox.image;
  info.container = sandbox.name;
  const rel = path.relative(sandbox.root, path.resolve(root)).split(path.sep).join('/');
  const workdir = rel ? `/workspace/${rel}` : '/workspace';
  const timeoutS = Math.max(1, Math.ceil(timeoutMs / 1000));
  const t0 = Date.now();
  try {
    const out = execFileSync('docker', [
      'exec', '-w', workdir, sandbox.name,
      'timeout', `${timeoutS}s`, 'sh', '-c', cmd,
    ], { timeout: timeoutMs + 10000, stdio: 'pipe' });
    info.duration_s = (Date.now() - t0) / 1000;
    info.ran = true;
    info.exit_code = 0;
    info.output = String(out || '').slice(-400).trim();
    return { failures: [], oracle: info };
  } catch (e) {
    info.duration_s = (Date.now() - t0) / 1000;
    info.ran = true;
    info.exit_code = e.status ?? null;
    info.output = `${e.stdout || ''}${e.stderr || ''}`.slice(-400).trim();
    if (e.status === 124 || e.status == null) {   // in-container timeout, or outer kill
      reapSandbox(sandbox);
    }
    if (e.status === 124) {
      return { failures: [`check_command timed out after ${timeoutS}s (docker exec): ${cmd}`], oracle: info };
    }
    const code = e.status != null ? `exit ${e.status}` : (e.signal || 'error');
    return { failures: [`check_command failed in docker (${code}): ${cmd}${info.output ? ' :: ' + info.output : ''}`], oracle: info };
  }
}

/**
 * Run the case's optional check_command (JS mirror of cases.run_check_command).
 * Runs inside the shared `optarena-tester` Docker container when Docker is
 * available (isolation + no host toolchain needed), falling back to running
 * directly on the host when it is not. Returns `{failures, oracle}` - oracle
 * always reports what actually happened (sandbox, exit code, timing, output),
 * not just the verdict.
 */
export function runCheckCommand(testCase, root) {
  const cmd = testCase.check_command;
  const info = newOracleInfo(cmd);
  if (!cmd) return { failures: [], oracle: info };
  const timeout = (testCase.check_command_timeout || 60) * 1000;

  // Route to the shared sandbox that has THIS case's toolchain - a Java case
  // must exec into the jvm image, not whatever single sandbox happens to be
  // up. Mirrors run_check_command's `_active_sandboxes[image]` lookup.
  const image = testCase.docker_image || process.env.OPTARENA_DOCKER_IMAGE || DOCKER_IMAGE_DEFAULT;
  const sandbox = activeSandboxes.get(image);
  if (process.env.OPTARENA_NO_DOCKER !== '1' && sandbox) {
    return runInSandbox(cmd, root, timeout, sandbox, info);
  }
  let useDocker = process.env.OPTARENA_NO_DOCKER !== '1' && dockerAvailable();
  if (useDocker && !dockerImageAvailable(image)) {
    useDocker = false;
    if (!dockerWarned) {
      console.error(`[optarena] Docker image '${image}' not found - falling back to host. Run \`optarena docker build\`.`);
      dockerWarned = true;
    }
  }
  if (!useDocker) {
    if (!dockerAvailable() && !dockerWarned) {
      console.error('[optarena] Docker not available - running check_command directly on the host.');
      dockerWarned = true;
    }
    info.sandbox = 'host';
    const t0 = Date.now();
    try {
      const out = execSync(cmd, { cwd: root, timeout, stdio: 'pipe' });
      info.duration_s = (Date.now() - t0) / 1000;
      info.ran = true;
      info.exit_code = 0;
      info.output = String(out || '').slice(-400).trim();
      return { failures: [], oracle: info };
    } catch (e) {
      info.duration_s = (Date.now() - t0) / 1000;
      info.ran = true;
      info.exit_code = e.status ?? null;
      info.output = `${e.stdout || ''}${e.stderr || ''}`.slice(-400).trim();
      const code = e.status != null ? `exit ${e.status}` : (e.signal || 'error');
      return { failures: [`check_command failed (${code}): ${cmd}${info.output ? ' :: ' + info.output : ''}`], oracle: info };
    }
  }
  info.sandbox = 'docker';
  info.image = image;
  const name = `optarena-check-${Math.random().toString(16).slice(2, 14)}`;
  info.container = name;
  const dockerArgs = [
    'run', '--rm', '--name', name,
    '--network', 'none', '--memory', '2g', '--cpus', '2',
    '-v', `${path.resolve(root)}:/workspace`, '-w', '/workspace',
    image, 'sh', '-c', cmd,
  ];
  const t0 = Date.now();
  try {
    const out = execFileSync('docker', dockerArgs, { timeout: timeout + 15000, stdio: 'pipe' });
    info.duration_s = (Date.now() - t0) / 1000;
    info.ran = true;
    info.exit_code = 0;
    info.output = String(out || '').slice(-400).trim();
    return { failures: [], oracle: info };
  } catch (e) {
    try { execFileSync('docker', ['rm', '-f', name], { stdio: 'pipe' }); } catch { /* already gone */ }
    info.duration_s = (Date.now() - t0) / 1000;
    info.ran = true;
    info.exit_code = e.status ?? null;
    info.output = `${e.stdout || ''}${e.stderr || ''}`.slice(-400).trim();
    const code = e.status != null ? `exit ${e.status}` : (e.signal || 'error');
    return { failures: [`check_command failed in docker (${code}): ${cmd}${info.output ? ' :: ' + info.output : ''}`], oracle: info };
  }
}

/**
 * Full oracle: write test_setup_files (real test code the model never saw),
 * check expected-file specs, then check_command when they pass. Returns
 * `{failures, oracle}` - see runCheckCommand for the oracle shape.
 */
export function evaluateCase(testCase, created, root) {
  const testFiles = Object.keys(testCase.test_setup_files || {});
  writeSetupFiles(root, testCase.test_setup_files);
  const failures = checkExpected(created, testCase.expected_files || [], root);
  if (failures.length > 0) {
    const info = newOracleInfo(testCase.check_command);
    info.test_setup_files = testFiles;
    return { failures, oracle: info };
  }
  const result = runCheckCommand(testCase, root);
  result.oracle.test_setup_files = testFiles;
  return result;
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
