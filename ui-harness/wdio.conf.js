/**
 * wdio.conf.js
 * ────────────
 * WebdriverIO + wdio-vscode-service config for driving a real VS Code AI agent
 * extension (Cline / Roo-Cline / Continue, selected via EXT) against SelfOpt.
 *
 * Unlike the old CDP/pyautogui harness, wdio-vscode-service downloads + launches
 * a clean VS Code, exposes the VS Code API via browser.executeWorkbench(), and
 * switches the WebDriver context INTO the extension's webview iframe.
 *
 * onPrepare seeds the test profile so the extension boots configured for SelfOpt
 * with auto-approval (no onboarding, no manual approvals).
 */
import fs from 'node:fs';
import {
  STORAGE_PATH, WORKSPACE, STATE_DB, EXT, DESCRIPTOR, API_MODE, BACKEND_URL, MODEL_ID,
  findExtensionPath,
} from './src/paths.js';
import { seedGlobalStateDb } from './src/seed.js';

/**
 * CRITICAL: when launched from inside VS Code's integrated terminal / extension
 * host, the environment leaks `ELECTRON_RUN_AS_NODE=1` and `VSCODE_*` vars. Any
 * `Code.exe` we spawn would inherit them, run as plain Node.js (rejecting
 * Chromium flags with "bad option: --no-sandbox"), or attach to the parent VS
 * Code instance. Scrub them here so every child launches a clean, standalone VS
 * Code. Runs in both the launcher and each worker (both import this config).
 */
function scrubElectronEnv() {
  for (const key of Object.keys(process.env)) {
    if (key === 'ELECTRON_RUN_AS_NODE' || key.startsWith('VSCODE_')) {
      delete process.env[key];
    }
  }
}
scrubElectronEnv();

const EXT_PATH = findExtensionPath();

// Per-extension launch env (e.g. Continue's isolated CONTINUE_GLOBAL_DIR).
const launchEnv = DESCRIPTOR.launchEnv ? DESCRIPTOR.launchEnv(STORAGE_PATH) : {};
for (const [k, v] of Object.entries(launchEnv)) process.env[k] = v;

export const config = {
  runner: 'local',
  specs: ['./test/agent.e2e.js'],
  maxInstances: 1,

  capabilities: [{
    browserName: 'vscode',
    // Pin to the newest VS Code that wdio-vscode-service v8 supports (its bundled
    // locators stop at 1.123.0). 'stable' (1.126+) rejects ChromeDriver launch
    // flags. Cline/Roo/Continue all support <=1.123.
    browserVersion: process.env.VSCODE_VERSION || '1.123.0',
    'wdio:vscodeOptions': {
      extensionPath: EXT_PATH,
      storagePath: STORAGE_PATH,
      workspacePath: WORKSPACE,
      userSettings: {
        'workbench.startupEditor': 'none',
        'window.commandCenter': false,
        'security.workspace.trust.enabled': false,
        'telemetry.telemetryLevel': 'off',
        'update.mode': 'none',
        'extensions.autoCheckUpdates': false,
      },
      verboseLogging: false,
      // Cold-profile first launch (fresh .vscode-storage) + a loaded machine can
      // take >25s before the extension-host proxy dials back; keep this generous.
      vscodeProxyOptions: { enable: true, connectionTimeout: 120000 },
    },
  }],

  services: ['vscode'],
  framework: 'mocha',
  reporters: ['spec'],
  logLevel: 'warn',
  bail: 0,
  mochaOpts: {
    ui: 'bdd',
    timeout: 900000, // 15 min ceiling; per-case logic enforces its own budget
  },

  onPrepare() {
    console.log(`\n[onPrepare] ${DESCRIPTOR.label} UI tests — EXT=${EXT}  API=${API_MODE}  backend=${BACKEND_URL}`);
    console.log(`[onPrepare] extension: ${EXT_PATH}`);

    for (const dir of [STORAGE_PATH, WORKSPACE]) {
      fs.rmSync(dir, { recursive: true, force: true });
      fs.mkdirSync(dir, { recursive: true });
    }

    if (DESCRIPTOR.configKind === 'globalState') {
      const items = DESCRIPTOR.seedGlobalState(API_MODE, BACKEND_URL, MODEL_ID);
      const keys = seedGlobalStateDb(STATE_DB, items);
      console.log(`[onPrepare] seeded ${keys.length} globalState keys -> ${STATE_DB}`);
    } else if (DESCRIPTOR.configKind === 'continueYaml') {
      DESCRIPTOR.seedFiles(STORAGE_PATH, API_MODE, BACKEND_URL, MODEL_ID);
      console.log(`[onPrepare] wrote Continue config (CONTINUE_GLOBAL_DIR=${launchEnv.CONTINUE_GLOBAL_DIR})`);
    }
    console.log(`[onPrepare] workspace: ${WORKSPACE}\n`);
  },
};
