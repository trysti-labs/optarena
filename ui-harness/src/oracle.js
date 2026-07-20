/**
 * src/oracle.js
 * ─────────────
 * File-system oracle - the JS mirror of `optarena/cases.py` (snapshot,
 * expected-file checks, Docker-sandboxed check_command, diff stats, failure
 * classification) - plus the test-case loader. Behavioral verification is
 * the primary signal: `test_setup_files` + `check_command` compile/run/assert
 * the generated code for real (graded on a private copy the agent never
 * sees); `expected_files` content patterns are only a cheap shape check.
 * Any semantic change here must be made in cases.py too, and vice versa.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { PROMPTS_DIR } from './paths.js';

const IGNORE_DIRS = new Set(['.git', '.vscode', '.cline', '.aider', 'node_modules', '__pycache__']);

/**
 * Recursively map each workspace file (relative POSIX path) to a content
 * signature. H-09: this MUST be the SHA-1 of the file's bytes, byte-for-byte
 * identical to the Python oracle's `cases.snapshot` - the two oracles are
 * documented as behaviourally equivalent, and this is the one place they had
 * diverged. `size:mtimeMs` (the old signature) missed a same-size rewrite
 * landing within one filesystem-timestamp tick, so a "modify" case that
 * edited a setup file in place could be judged unchanged on coarse-mtime
 * filesystems (Windows). Content hashing detects it deterministically.
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
        try {
          out.set(rel, crypto.createHash('sha1').update(fs.readFileSync(full)).digest('hex'));
        } catch { /* vanished/locked mid-scan - treat as absent */ }
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

// Defense-in-depth flags for every sandbox container that runs a case's
// check_command (JS mirror of cases._HARDENING_ARGS - keep in sync). Both
// the shared sandbox and the ephemeral per-call fallback use these; --user
// (non-root) is opt-in via OPTARENA_SANDBOX_USER (sandboxUserArgs below).
const HARDENING_ARGS = [
  '--cap-drop', 'ALL',
  '--security-opt', 'no-new-privileges',
  '--pids-limit', '256',
  '--read-only',
  // exec: tmpfs mounts can default to noexec, which silently broke `go
  // test` - it compiles a test binary INTO this tmpfs (via GOCACHE below)
  // and then has to execute it. 1g (not 256m): the Go linker ran out of
  // space writing its output at 256m; 1g stays under the 2g memory limit.
  '--tmpfs', '/tmp:rw,exec,size=1g,mode=1777',
  // go test always compiles before running and writes its build cache to
  // $HOME/.cache/go-build by default - the one thing --read-only broke in a
  // full corpus run. Redirect it into the writable tmpfs; harmless no-op on
  // non-Go images. Keep in sync with cases._HARDENING_ARGS.
  '-e', 'GOCACHE=/tmp/go-build',
];

/**
 * Optional non-root sandbox execution (M-10, mirror of cases._sandbox_user_args):
 * OPTARENA_SANDBOX_USER=uid:gid runs sandbox containers as that user with
 * HOME on the writable tmpfs. Opt-in - see the Python side's rationale.
 */
function sandboxUserArgs() {
  const user = process.env.OPTARENA_SANDBOX_USER;
  return user ? ['--user', user, '-e', 'HOME=/tmp'] : [];
}

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

/**
 * Approximate change size (JS mirror of cases.diff_stats): files touched and
 * lines changed. Modify-cases diff against the case's own setup_files text;
 * new files count their full line length.
 */
function diffStats(testCase, created, root) {
  const setupFiles = testCase.setup_files || {};
  const lineCount = (text) => {
    if (!text) return 0;
    const n = (text.match(/\n/g) || []).length;
    return n + (text.endsWith('\n') ? 0 : 1);
  };
  let linesChanged = 0;
  for (const rel of created) {
    let content;
    try {
      content = fs.readFileSync(path.join(root, rel), 'utf-8');
    } catch { continue; }
    const newLines = lineCount(content);
    const original = setupFiles[rel];
    linesChanged += original != null
      ? (Math.abs(newLines - lineCount(original)) || 1)
      : newLines;
  }
  return { files_changed: created.length, lines_changed_approx: linesChanged };
}

/**
 * Best-effort failure bucket (JS mirror of cases.classify_failure):
 * timeout / syntax_error / compile_error / assertion_failure / runtime_error.
 * Not authoritative and never part of the pass/fail verdict - a richer
 * metric for the CLI/dashboard/compare table, kept in sync with Python so
 * UI-driver and CLI-driver runs report the same classes.
 */
function classifyFailure(info) {
  if (info.timed_out || info.exit_code === 124) return 'timeout';
  if (!info.ran || info.exit_code == null || info.exit_code === 0) return null;
  const out = (info.output || '').toLowerCase();
  const cmd = (info.check_command || '').toLowerCase();
  if (out.includes('timed out')) return 'timeout';
  if (['syntaxerror', 'indentationerror', 'unterminated', 'unexpected token']
    .some((s) => out.includes(s))) return 'syntax_error';
  if (['undefined reference', 'collect2:', 'compilation error',
    'cannot find symbol', 'error cs', 'error[e', 'undefined:', 'could not compile']
    .some((s) => out.includes(s))) return 'compile_error';
  if (['gcc', 'g++', 'cc '].some((t) => cmd.includes(t))
    && ['error:', 'calledprocesserror'].some((s) => out.includes(s))) return 'compile_error';
  if (out.includes('assertionerror') || out.includes('assert ')) return 'assertion_failure';
  return 'runtime_error';
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
 *
 * Also mounts a SECOND, separate host directory at `/verify` - a private
 * grading area (C-01) that `evaluateCase` copies the live workspace into
 * before writing hidden test files, so those files are never written into
 * `root` itself (which the agent's VS Code extension is actively
 * reading/watching for the whole run). It has to be its own bind mount,
 * not just a subdirectory of `root`: a subdirectory would still be visible
 * to the agent through its opened workspace folder, defeating the point.
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
  const verifyRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'optarena-verify-mount-'));
  try {
    execFileSync('docker', [
      'run', '-d', '--rm', '--name', name,
      '--network', 'none', '--memory', '2g', '--cpus', '2',
      ...HARDENING_ARGS, ...sandboxUserArgs(),
      '-v', `${resolvedRoot}:/workspace`,
      '-v', `${verifyRoot}:/verify`,
      '-w', '/workspace',
      image, 'sleep', 'infinity',
    ], { stdio: 'pipe', timeout: 20000 });
  } catch (e) {
    console.error(`[optarena] could not start docker sandbox: ${e.message}`);
    fs.rmSync(verifyRoot, { recursive: true, force: true });
    return null;
  }
  const sandbox = { name, root: resolvedRoot, verifyRoot, image };
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
  if (sandbox.verifyRoot) fs.rmSync(sandbox.verifyRoot, { recursive: true, force: true });
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
  const resolved = path.resolve(root);
  // `root` is either the live workspace (mounted at /workspace) or a private
  // grading copy under sandbox.verifyRoot (mounted separately at /verify,
  // see evaluateCase) - route to whichever mount actually contains it.
  const inVerify = sandbox.verifyRoot
    && (resolved === sandbox.verifyRoot || resolved.startsWith(sandbox.verifyRoot + path.sep));
  const mountBase = inVerify ? sandbox.verifyRoot : sandbox.root;
  const containerBase = inVerify ? '/verify' : '/workspace';
  const rel = path.relative(mountBase, resolved).split(path.sep).join('/');
  const workdir = rel ? `${containerBase}/${rel}` : containerBase;
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
    // A spawn-level failure (docker binary gone mid-run: ENOENT) means the
    // command never executed - `ran` must stay false, not report a phantom
    // execution with a null exit code.
    if (e.code === 'ENOENT' && e.status == null && !e.signal) {
      return { failures: [`check_command could not run (docker exec): ${e.message}`], oracle: info };
    }
    info.ran = true;
    info.exit_code = e.status ?? null;
    info.output = `${e.stdout || ''}${e.stderr || ''}`.slice(-400).trim();
    if (e.status === 124 || e.status == null) {   // in-container timeout, or outer kill
      info.timed_out = true;                       // both are timeout-class events
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
 * Two distinct, both explicit, opt-ins to running an untrusted check_command
 * directly on the host (JS mirror of cases._unsafe_host_exec_allowed):
 * - OPTARENA_NO_DOCKER=1 - Docker deliberately disabled.
 * - OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1 - covers the C-02 gap where Docker was
 *   never explicitly disabled, it's just unavailable/misconfigured; a stderr
 *   warning is not an adequate control for arbitrary code execution, so that
 *   case now fails closed unless this is set.
 */
function unsafeHostExecAllowed() {
  return process.env.OPTARENA_NO_DOCKER === '1' || process.env.OPTARENA_ALLOW_UNSAFE_HOST_EXEC === '1';
}

/**
 * Run a host command synchronously and, on timeout, kill the whole process
 * TREE - not just the direct child (JS mirror of cases.run_capture, H-11).
 * `execSync`/`spawnSync` only signal the immediate child on timeout, so a
 * test that spawned a server would leave it orphaned holding its port.
 * On POSIX the child is spawned `detached` (its own process group leader),
 * so signalling the negative pid reaps the group; on Windows `taskkill /T`
 * walks the tree. Returns the spawnSync result object.
 */
function runHostCapture(cmd, { cwd, timeout }) {
  const opts = {
    cwd, timeout, shell: true, stdio: 'pipe',
    encoding: 'utf-8', killSignal: 'SIGKILL',
  };
  if (process.platform !== 'win32') opts.detached = true;
  const res = spawnSync(cmd, opts);
  const timedOut = res.error && res.error.code === 'ETIMEDOUT';
  if (timedOut && res.pid) {
    if (process.platform === 'win32') {
      try { spawnSync('taskkill', ['/F', '/T', '/PID', String(res.pid)], { stdio: 'ignore' }); } catch { /* best-effort */ }
    } else {
      try { process.kill(-res.pid, 'SIGKILL'); } catch { /* group already gone */ }
    }
  }
  return res;
}

/**
 * Run the case's optional check_command (JS mirror of cases.run_check_command).
 * Runs inside the shared `optarena-tester` Docker container when Docker is
 * available (isolation + no host toolchain needed). When Docker is
 * unavailable or an image is missing, this fails closed (C-02) unless the
 * caller explicitly opted into host execution - see unsafeHostExecAllowed.
 * Returns `{failures, oracle}` - oracle always reports what actually
 * happened (sandbox, exit code, timing, output), not just the verdict.
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
  const dockerDisabled = process.env.OPTARENA_NO_DOCKER === '1';
  const unsafeOk = unsafeHostExecAllowed();
  if (!dockerDisabled && sandbox) {
    return runInSandbox(cmd, root, timeout, sandbox, info);
  }
  let useDocker = !dockerDisabled && dockerAvailable();
  if (useDocker && !dockerImageAvailable(image)) {
    useDocker = false;
    if (!dockerWarned) {
      console.error(`[optarena] Docker image '${image}' not found - ` + (unsafeOk
        ? 'falling back to host (unsafe host exec explicitly allowed).'
        : 'refusing to run check_command on the host. Run `optarena docker build`, '
          + 'or set OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1 to run this untrusted command '
          + 'directly on this machine anyway.'));
      dockerWarned = true;
    }
  }
  if (!useDocker) {
    if (!dockerAvailable() && !dockerWarned) {
      console.error('[optarena] Docker not available - ' + (unsafeOk
        ? 'running check_command directly on the host (unsafe host exec explicitly allowed).'
        : 'refusing to run check_command on the host. Install/start Docker Desktop, '
          + 'or set OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1 to run this untrusted command '
          + 'directly on this machine anyway.'));
      dockerWarned = true;
    }
    if (!unsafeOk) {
      info.sandbox = 'refused';
      return {
        failures: [`check_command refused: no Docker sandbox available and host execution `
          + `was not explicitly allowed (see stderr): ${cmd}`],
        oracle: info,
      };
    }
    info.sandbox = 'host';
    const t0 = Date.now();
    const res = runHostCapture(cmd, { cwd: root, timeout });
    info.duration_s = (Date.now() - t0) / 1000;
    info.ran = true;
    const timedOut = res.error && res.error.code === 'ETIMEDOUT';
    if (res.status === 0) {
      info.exit_code = 0;
      info.output = String(res.stdout || '').slice(-400).trim();
      return { failures: [], oracle: info };
    }
    info.exit_code = res.status ?? null;
    info.output = `${res.stdout || ''}${res.stderr || ''}`.slice(-400).trim();
    if (timedOut) info.timed_out = true;
    const code = timedOut ? `timed out after ${timeout / 1000}s`
      : (res.status != null ? `exit ${res.status}` : (res.signal || 'error'));
    return { failures: [`check_command failed (${code}): ${cmd}${info.output ? ' :: ' + info.output : ''}`], oracle: info };
  }
  info.sandbox = 'docker';
  info.image = image;
  const name = `optarena-check-${Math.random().toString(16).slice(2, 14)}`;
  info.container = name;
  const dockerArgs = [
    'run', '--rm', '--name', name,
    '--network', 'none', '--memory', '2g', '--cpus', '2',
    ...HARDENING_ARGS, ...sandboxUserArgs(),
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
    if (e.code === 'ENOENT' && e.status == null && !e.signal) {
      return { failures: [`check_command could not run (docker): ${e.message}`], oracle: info };
    }
    info.ran = true;
    info.exit_code = e.status ?? null;
    info.output = `${e.stdout || ''}${e.stderr || ''}`.slice(-400).trim();
    if (e.status == null) info.timed_out = true;   // outer-timeout kill, not a real exit
    const code = e.status != null ? `exit ${e.status}` : (e.signal || 'error');
    return { failures: [`check_command failed in docker (${code}): ${cmd}${info.output ? ' :: ' + info.output : ''}`], oracle: info };
  }
}

/**
 * Recursively copy `root` into `dest` (same IGNORE_DIRS/`.aider*` skip rules
 * as `snapshot`), so grading can happen on a private copy instead of the
 * live agent workspace. Symlinks are skipped outright - defense in depth,
 * a copied workspace shouldn't need them and following one could read
 * arbitrary host files.
 */
function copyWorkspaceForVerification(root, dest) {
  fs.mkdirSync(dest, { recursive: true });
  let entries = [];
  try {
    entries = fs.readdirSync(root, { withFileTypes: true });
  } catch {
    return;
  }
  for (const e of entries) {
    if (e.isSymbolicLink()) continue;
    if (e.isDirectory() && IGNORE_DIRS.has(e.name)) continue;
    if (e.isFile() && e.name.startsWith('.aider')) continue;
    const src = path.join(root, e.name);
    const out = path.join(dest, e.name);
    if (e.isDirectory()) {
      copyWorkspaceForVerification(src, out);
    } else if (e.isFile()) {
      fs.copyFileSync(src, out);
    }
  }
}

/**
 * Full oracle: write test_setup_files (real test code the model never saw),
 * check expected-file specs, then check_command when they pass. Returns
 * `{failures, oracle}` - see runCheckCommand for the oracle shape.
 *
 * C-01: this used to write the hidden test files straight into `root` - the
 * LIVE agent workspace. `agent.e2e.js` calls this on every poll tick while
 * the agent's VS Code extension is actively reading/watching that same
 * directory, so the model could observe its own hidden tests mid-run. Grade
 * a private, frozen copy instead: the live workspace is only ever read from,
 * never written to, by anything test-related.
 */
export function evaluateCase(testCase, created, root) {
  const testFiles = Object.keys(testCase.test_setup_files || {});
  // If a shared sandbox is active for this case's image, the grading copy
  // must land under ITS `/verify` mount (a subdirectory of sandbox.verifyRoot)
  // so runCheckCommand's docker-exec path can actually reach it; otherwise
  // (host exec, or an ephemeral per-call `docker run -v <dir>:/workspace`)
  // any private temp directory works, since those paths mount whatever
  // directory they're given directly.
  const image = testCase.docker_image || process.env.OPTARENA_DOCKER_IMAGE || DOCKER_IMAGE_DEFAULT;
  const sandbox = process.env.OPTARENA_NO_DOCKER !== '1' ? activeSandboxes.get(image) : null;
  const verifyParent = sandbox ? sandbox.verifyRoot : os.tmpdir();
  const verifyRoot = fs.mkdtempSync(path.join(verifyParent, 'optarena-verify-'));
  try {
    copyWorkspaceForVerification(root, verifyRoot);
    writeSetupFiles(verifyRoot, testCase.test_setup_files);
    const failures = checkExpected(created, testCase.expected_files || [], verifyRoot);
    if (failures.length > 0) {
      const info = newOracleInfo(testCase.check_command);
      info.test_setup_files = testFiles;
      info.diff = diffStats(testCase, created, root);
      return { failures, oracle: info };
    }
    const result = runCheckCommand(testCase, verifyRoot);
    result.oracle.test_setup_files = testFiles;
    // Same enrichment as the Python evaluate_case: change-size estimate
    // always, failure classification only when the check actually failed -
    // so UI-driver results carry the same metric fields as CLI-driver ones.
    result.oracle.diff = diffStats(testCase, created, root);
    if (result.failures.length > 0) {
      result.oracle.failure_class = classifyFailure(result.oracle);
    }
    return result;
  } finally {
    fs.rmSync(verifyRoot, { recursive: true, force: true });
  }
}

/**
 * Write a case's setup_files into the workspace before the case runs (JS
 * mirror of cases.write_setup_files). `rel` comes straight from case JSON
 * (`setup_files`/`test_setup_files` keys) - H-01: a "../" traversal there
 * would write outside the sandboxed workspace, onto the host. `path.resolve`
 * (not `path.join`) correctly collapses ".." and treats an absolute `rel` as
 * replacing `root` entirely, exactly like Python's `(root / rel).resolve()`,
 * so the containment check below actually sees the real destination.
 */
export function writeSetupFiles(root, setupFiles) {
  const resolvedRoot = path.resolve(root);
  for (const [rel, content] of Object.entries(setupFiles || {})) {
    const dest = path.resolve(resolvedRoot, rel);
    const relToRoot = path.relative(resolvedRoot, dest);
    if (relToRoot === '..' || relToRoot.startsWith('..' + path.sep) || path.isAbsolute(relToRoot)) {
      throw new Error(`setup file path escapes workspace root: ${JSON.stringify(rel)}`);
    }
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    // The lexical check above can be defeated by a symlinked directory INSIDE
    // the workspace pointing outside it (path.resolve, unlike Python's
    // Path.resolve, never touches the filesystem). Now that the parent chain
    // exists, realpath both sides and re-check physical containment - this is
    // what keeps the two oracles' H-01 guarantees actually equivalent.
    const realRoot = fs.realpathSync(resolvedRoot);
    const realParent = fs.realpathSync(path.dirname(dest));
    if (realParent !== realRoot && !realParent.startsWith(realRoot + path.sep)) {
      throw new Error(`setup file path escapes workspace root via symlink: ${JSON.stringify(rel)}`);
    }
    fs.writeFileSync(dest, content, 'utf-8');
  }
}

/** Load all prompt JSON cases from the shared prompts dir. */
export function loadCases() {
  const files = fs.readdirSync(PROMPTS_DIR).filter((f) => f.endsWith('.json')).sort();
  return files.map((f) => JSON.parse(fs.readFileSync(path.join(PROMPTS_DIR, f), 'utf-8')));
}
