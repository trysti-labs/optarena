/**
 * test/agent.e2e.js
 * ─────────────────
 * Drives a real VS Code AI-agent webview (Cline / Roo-Cline / Continue, selected
 * via EXT) against the configured backend and verifies the workspace diff.
 *
 * Flow per case:
 *   1. clear workspace + start a fresh task
 *   2. write any setup_files, snapshot the workspace
 *   3. open the extension's webview iframe, type the prompt, submit (Enter)
 *   4. poll the filesystem, clicking any approval buttons, until expected_files
 *      appear or the case budget elapses
 *   5. assert expected_files matched
 *
 * All extension-specific knowledge (view command, approve labels, mode hints)
 * comes from the DESCRIPTOR in src/extensions.js.
 */
import fs from 'node:fs';
import path from 'node:path';
import { WORKSPACE, BACKEND_URL, API_MODE, MODEL_ID, DESCRIPTOR, RESULTS_FILE } from '../src/paths.js';
import {
  snapshot, changedFiles, checkExpected, writeSetupFiles, loadCases,
} from '../src/oracle.js';

const CASE_TIMEOUT = Number(process.env.CASE_TIMEOUT || 150) * 1000;
const POLL_MS = 3000;
// Max time to let an intermediate (non-final) prompt settle before the next one.
const INTER_PROMPT_WAIT = Number(process.env.INTER_PROMPT_WAIT || 150) * 1000;
// A turn is considered done once there are no approvals/file changes for this long.
const IDLE_MS = Number(process.env.IDLE_MS || 15) * 1000;

const APPROVE_RE = DESCRIPTOR.approveRe;
const REJECT_RE = DESCRIPTOR.rejectRe;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Run a VS Code command in the workbench via the service's API proxy. */
async function runCommand(id, ...args) {
  return browser.executeWorkbench(
    (vscode, cmd, cmdArgs) => vscode.commands.executeCommand(cmd, ...cmdArgs),
    id, args,
  );
}

/**
 * Return the CURRENT extension webview, re-acquired fresh each call. We never
 * cache a webview handle: moving the view (sidebar → editor area) or the
 * extension re-rendering replaces the iframe, invalidating a cached reference.
 * Re-focuses + re-opens the view if no webview is currently present.
 */
async function freshWebview() {
  const workbench = await browser.getWorkbench();
  let webviews = await workbench.getAllWebviews();
  if (webviews.length === 0) {
    await runCommand(DESCRIPTOR.viewCommand);
    await browser.waitUntil(async () => {
      webviews = await workbench.getAllWebviews();
      return webviews.length > 0;
    }, { timeout: 30000, timeoutMsg: `${DESCRIPTOR.label} webview iframe never appeared` });
  }
  // With multiple webviews (or one that just reloaded), pick the one whose body
  // actually has content — avoids landing in a stale/detached frame after an
  // SPA reload (e.g. Roo's welcome → wizard → chat transitions).
  if (webviews.length > 1) {
    for (const wv of webviews) {
      try {
        await wv.open();
        const len = await browser.execute(() => ((document.body && document.body.innerHTML) || '').length).catch(() => 0);
        await wv.close();
        if (len > 80) return wv;
      } catch { try { await wv.close(); } catch { /* ignore */ } }
    }
  }
  return webviews[0];
}

/** Locate the chat input inside the (already-open) webview context. */
async function findChatInput() {
  const selectors = [
    'textarea[data-testid="chat-input"]',
    'textarea[placeholder*="task" i]',
    'textarea[placeholder*="type" i]',
    'textarea[placeholder*="ask" i]',
    'textarea',
    'div[contenteditable="true"]',
    '[contenteditable="true"]',
  ];
  for (const sel of selectors) {
    const els = await $$(sel);
    for (const el of els) {
      if (await el.isDisplayed().catch(() => false)) return el;
    }
  }
  return null;
}

/** Acquire a fresh webview frame, run fn inside it, always close. */
async function inWebview(fn) {
  const webview = await freshWebview();
  await webview.open();
  try { return await fn(); } finally { await webview.close(); }
}

/** Count matching elements in the current webview frame (robust to SPA churn). */
async function domCount(sel) {
  return browser.execute((s) => document.querySelectorAll(s).length, sel).catch(() => 0);
}

/** Click the first visible element (in the current webview ctx) matching `re`. */
async function clickByText(re, extraSel = '') {
  const sel = `button, [role="button"], [role="option"], li, vscode-button, a${extraSel ? ', ' + extraSel : ''}`;
  for (const el of await $$(sel)) {
    let t = '';
    try { t = ((await el.getText()) || (await el.getAttribute('aria-label')) || '').trim(); } catch { continue; }
    if (!re.test(t)) continue;
    try {
      await browser.execute((e) => e.scrollIntoView({ block: 'center' }), el);
      await el.click();
      return true;
    } catch { /* try next */ }
  }
  return false;
}

/**
 * Take ONE step toward a ready chat input, in the current webview frame. Returns
 * 'ready' when the chat input is present, otherwise a short tag describing the
 * action taken (or 'wait'). Because SPA route changes (Get Started → wizard →
 * chat) transiently invalidate the WebDriver frame, the caller re-acquires a
 * fresh frame between each call (see prepareChat()).
 */
async function advanceOnce() {
  if (await findChatInput()) return 'ready';
  const text = (await browser.execute(() => (document.body && document.body.innerText) || '').catch(() => '')).toLowerCase();

  // Roo provider wizard. Check BEFORE the welcome screen: the wizard's own copy
  // contains the words "to get started", which must not be mistaken for the
  // welcome "Get Started" button.
  if (await domCount('[aria-label="provider-select"]')) {
    if (!/\bollama\b/.test(text)) {
      // Provider isn't Ollama yet — open the dropdown and pick Ollama.
      (await $$('[aria-label="provider-select"]'))[0]?.click().catch(() => {});
      await sleep(1000);
      const picked = await clickByText(/^ollama$/i, '[role="option"], [role="menuitem"], li');
      if (!picked) {
        // Searchable combobox: type to filter, then confirm.
        try { await browser.keys([...'ollama']); await sleep(600); await browser.keys('Enter'); } catch { /* ignore */ }
      }
      await sleep(2500); // provider switch re-renders the config fields
      return 'provider→ollama';
    }
    // Set the Ollama base URL if a URL field has the wrong value.
    for (const inp of await $$('input[type="text"], input:not([type])')) {
      try {
        const ph = (await inp.getAttribute('placeholder')) || '';
        const val = (await inp.getValue()) || '';
        if ((/url|base/i.test(ph) || /^https?:\/\//.test(val)) && val !== BACKEND_URL) {
          await inp.click();
          await browser.keys([...Array(48)].map(() => 'Backspace'));
          await browser.keys([...BACKEND_URL]);
          return 'base-url';
        }
      } catch { /* ignore */ }
    }
    const modelRe = new RegExp(MODEL_ID.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i');
    if (!modelRe.test(text)) {
      (await $$('[aria-label="model-picker-button"]'))[0]?.click().catch(() => {});
      await sleep(800);
      (await clickByText(modelRe, '[role="option"], div')) || (await clickByText(/.+/, '[role="option"]'));
      return 'model';
    }
    await clickByText(/^finish/i);
    await sleep(3000); // let the wizard commit + navigate to chat
    return 'finish';
  }

  // One-time welcome (Cline/Roo): the "Get Started" button.
  if (await clickByText(/^get started$/i)) {
    await sleep(4000); // welcome → wizard is a full webview reload; let it settle
    return 'get-started';
  }

  // Continue (and others) needing an Agent/Act mode selected.
  if (DESCRIPTOR.agentModeHint && await clickByText(new RegExp(DESCRIPTOR.agentModeHint, 'i'), '[role="combobox"], select')) {
    return 'agent-mode';
  }
  return 'wait';
}

/** Advance through onboarding/wizard until the chat input is ready. */
async function prepareChat() {
  const deadline = Date.now() + 120000;
  let last = '';
  while (Date.now() < deadline) {
    const status = await inWebview(advanceOnce);
    if (status !== last && status !== 'wait') {
      console.log(`  [prepare] ${status}`);
      last = status;
    }
    if (status === 'ready') return true;
    await sleep(2500);
  }
  return false;
}

/** Type a prompt into the extension and submit it. */
async function injectPrompt(text, isFirst) {
  // Some extensions expose a command to focus their chat input reliably.
  if (DESCRIPTOR.prepareInput) await runCommand(DESCRIPTOR.prepareInput).catch(() => {});

  // First prompt: clear onboarding / provider wizard / agent-mode until the chat
  // input is ready (each step in a fresh frame — see prepareChat()).
  if (isFirst) {
    const ready = await prepareChat();
    if (!ready) {
      const diag = await inWebview(() => browser.execute(() => ({
        textareas: document.querySelectorAll('textarea').length,
        text: ((document.body && document.body.innerText) || '').slice(0, 300),
      })).catch(() => ({})));
      throw new Error(`chat input never became ready in ${DESCRIPTOR.label}. diag=${JSON.stringify(diag)}`);
    }
  }

  await inWebview(async () => {
    const input = await findChatInput();
    if (!input) throw new Error(`chat input not found in ${DESCRIPTOR.label} webview`);
    // Scroll into view + focus via JS. As the conversation grows the input moves
    // below the fold, and webdriverio's Actions-API auto-scroll uses a CDP command
    // this Electron lacks ("Browser.getWindowForTarget"), causing "element click
    // intercepted". JS scroll/focus avoids that path entirely.
    await browser.execute((el) => { el.scrollIntoView({ block: 'center' }); el.focus(); }, input);
    await sleep(150);
    await browser.keys([...text]); // real key events so the (React) UI registers it
    await browser.keys('Enter');   // send (Shift+Enter = newline in these UIs)
  });
}

/**
 * Switch into the webview and click any visible approval button. Returns
 * { clicked, labels }. Never clicks reject/deny buttons.
 */
async function clickApprovals() {
  let clicked = 0;
  const labels = [];
  const webview = await freshWebview();
  await webview.open();
  try {
    const buttons = await $$('button, a[role="button"], [role="button"], vscode-button');
    for (const b of buttons) {
      let txt = '';
      try {
        txt = ((await b.getText()) || (await b.getAttribute('aria-label')) || '').trim();
      } catch { continue; }
      if (!txt) continue;
      labels.push(txt);
      if (REJECT_RE.test(txt) || !APPROVE_RE.test(txt)) continue;
      // JS-scroll into view, then click; fall back to a JS click if the native
      // click is intercepted (same Actions-API limitation as injectPrompt).
      try {
        await browser.execute((el) => el.scrollIntoView({ block: 'center' }), b);
        await b.click();
        clicked++;
      } catch {
        try { await browser.execute((el) => el.click(), b); clicked++; } catch { /* stale — ignore */ }
      }
    }
  } catch { /* webview not ready this tick */ } finally {
    await webview.close();
  }
  return { clicked, labels };
}

/** Close editors and empty the workspace (keep .vscode) between cases. */
async function clearWorkspace() {
  await runCommand('workbench.action.closeAllEditors').catch(() => {});
  for (const entry of fs.readdirSync(WORKSPACE)) {
    if (entry === '.vscode') continue;
    fs.rmSync(path.join(WORKSPACE, entry), { recursive: true, force: true });
  }
}

async function backendReachable() {
  const url = API_MODE === 'openai' ? `${BACKEND_URL}/v1/models` : `${BACKEND_URL}/api/tags`;
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(4000) });
    return res.ok;
  } catch {
    return false;
  }
}

describe(`${DESCRIPTOR.label} UI × backend (${API_MODE})`, function () {
  before(async function () {
    this.timeout(60000);
    if (!(await backendReachable())) {
      throw new Error(`Backend not reachable at ${BACKEND_URL} (API=${API_MODE}). Start it first.`);
    }
    await freshWebview(); // open the extension view once up front
    console.log(`  [setup] ${DESCRIPTOR.label} webview is open`);
  });

  // Case selection: ONLY_CASE=<name> or CASES=<name,name,…> (arena uses CASES).
  const only = process.env.ONLY_CASE;
  const casesFilter = (process.env.CASES || '').split(',').map((s) => s.trim()).filter(Boolean);
  const cases = loadCases().filter((c) =>
    (only ? c.name === only : true) && (casesFilter.length ? casesFilter.includes(c.name) : true));

  /** Append one JSON line per case for machine consumers (arena driver). */
  function emitResult(rec) {
    if (!RESULTS_FILE) return;
    try { fs.appendFileSync(RESULTS_FILE, JSON.stringify(rec) + '\n'); } catch { /* best-effort */ }
  }

  for (const testCase of cases) {
    it(testCase.name, async function () {
      const prompts = testCase.prompts || [];
      this.timeout((prompts.length - 1) * INTER_PROMPT_WAIT + CASE_TIMEOUT + 180000);
      console.log(`\n  === ${testCase.name}: ${testCase.description || ''} ===`);

      await clearWorkspace();
      if (DESCRIPTOR.newTaskCommand) await runCommand(DESCRIPTOR.newTaskCommand).catch(() => {});
      await sleep(1000);

      writeSetupFiles(WORKSPACE, testCase.setup_files);
      const before = snapshot(WORKSPACE);
      const expected = testCase.expected_files || [];

      let created = [];
      let failures = ['(no poll yet)'];
      const caseStart = Date.now();
      const emit = (error) => emitResult({
        name: testCase.name,
        passed: !error && failures.length === 0,
        duration_s: (Date.now() - caseStart) / 1000,
        files: created,
        failures,
        error: error ? String(error.message || error) : null,
      });

      try {
      for (let i = 0; i < prompts.length; i++) {
        const isFinal = i === prompts.length - 1;
        console.log(`  [inject ${i + 1}/${prompts.length}] ${prompts[i].slice(0, 70)}...`);
        await injectPrompt(prompts[i], i === 0);

        const budget = isFinal ? CASE_TIMEOUT : INTER_PROMPT_WAIT;
        const deadline = Date.now() + budget;
        let lastActivity = Date.now();
        let prevSig = '';
        let ticks = 0;
        while (Date.now() < deadline) {
          const { clicked: n, labels } = await clickApprovals();
          if (n > 0) {
            console.log(`  [approve] clicked ${n} button(s)`);
            await runCommand('workbench.action.files.saveAll').catch(() => {});
          }
          if (n === 0 && labels.length && ticks % 5 === 0) {
            console.log(`  [buttons] ${[...new Set(labels)].join(' | ').slice(0, 200)}`);
          }
          ticks++;

          created = changedFiles(before, WORKSPACE);
          const sig = created.slice().sort().join('|');
          if (n > 0 || sig !== prevSig) { lastActivity = Date.now(); prevSig = sig; }

          if (isFinal) {
            failures = checkExpected(created, expected, WORKSPACE);
            if (failures.length === 0) break;
          } else if (Date.now() - lastActivity > IDLE_MS) {
            break;
          }
          await sleep(POLL_MS);
        }
      }

      } catch (err) {
        emit(err);
        throw err;
      }

      console.log(`  [result] created/changed: ${created.join(', ') || 'none'}`);
      emit(null);
      if (failures.length > 0) {
        throw new Error(`Case "${testCase.name}" failed:\n   - ${failures.join('\n   - ')}`);
      }
    });
  }
});
