/**
 * src/extensions.js
 * ─────────────────
 * Per-extension descriptors so one wdio harness can drive multiple VS Code AI
 * agents (Cline, Roo-Cline, Continue) against the scenario backend. Selected via the EXT env
 * var (default "cline").
 *
 * Each descriptor provides:
 *   key, label
 *   extDirPrefix   - prefix of the installed extension folder (and its id)
 *   viewCommand    - VS Code command to open the extension's view
 *   approveRe/rejectRe - which webview buttons to click / never click
 *   configKind     - 'globalState' (seed state.vscdb) | 'continueYaml' (config file)
 *   seedGlobalState(apiMode, url, model) -> { itemTableKey: value } (globalState kind)
 *   seedFiles(storagePath, apiMode, url, model) -> void     (file-config kind)
 *   launchEnv(storagePath) -> { ENV: value }                (extra env for VS Code)
 *   prepareInput  - optional command to focus the chat input before typing
 *   agentModeHint - optional UI text to select an agent/act mode (Continue)
 */
import fs from 'node:fs';
import path from 'node:path';

// Buttons that approve a pending action; reject/deny are excluded first.
const AGENT_APPROVE_RE = /^(save|approve|run|proceed|accept|allow|yes|keep|apply|continue|resume|retry)\b/i;
const AGENT_REJECT_RE = /^(reject|deny|cancel|no\b|discard|revert|start new)/i;

/** Cline's permissive auto-approval object (nested), verified from its bundle. */
function clineAutoApproval() {
  return {
    version: 1, enabled: true, favorites: [], maxRequests: 1000,
    actions: {
      readFiles: true, readFilesExternally: true, editFiles: true,
      editFilesExternally: true, executeSafeCommands: true,
      executeAllCommands: true, useBrowser: false, useMcp: true,
    },
    enableNotifications: false,
  };
}

export const EXTENSIONS = {
  cline: {
    key: 'cline',
    label: 'Cline',
    extDirPrefix: 'saoudrizwan.claude-dev',
    viewCommand: 'workbench.view.extension.claude-dev-ActivityBar',
    newTaskCommand: 'cline.plusButtonClicked',
    approveRe: AGENT_APPROVE_RE,
    rejectRe: AGENT_REJECT_RE,
    configKind: 'globalState',
    seedGlobalState(apiMode, url, model = 'llama3.2') {
      const id = 'saoudrizwan.claude-dev';
      const p = `${id}/`;
      const items = {
        [id]: { welcomeViewCompleted: true },
        'workbench.view.extension.claude-dev-ActivityBar.state.hidden': [
          { id: 'claude-dev.SidebarProvider', isHidden: false },
        ],
        [`${p}autoApprovalSettings`]: clineAutoApproval(),
        [`${p}telemetrySetting`]: 'disabled',
      };
      if (apiMode === 'openai') {
        Object.assign(items, {
          [`${p}apiProvider`]: 'openai',
          [`${p}openAiBaseUrl`]: `${url}/v1`,
          [`${p}openAiApiKey`]: 'optarena',
          [`${p}openAiModelId`]: model,
        });
      } else {
        Object.assign(items, {
          [`${p}apiProvider`]: 'ollama',
          [`${p}ollamaBaseUrl`]: url,
          [`${p}ollamaModelId`]: model,
        });
      }
      return items;
    },
  },

  roo: {
    key: 'roo',
    label: 'Roo-Cline',
    extDirPrefix: 'rooveterinaryinc.roo-cline',
    viewCommand: 'workbench.view.extension.roo-cline-ActivityBar',
    newTaskCommand: 'roo-cline.plusButtonClicked',
    wizard: 'roo', // first-run "Choose your provider" wizard, driven in the spec
    approveRe: AGENT_APPROVE_RE,
    rejectRe: AGENT_REJECT_RE,
    configKind: 'globalState',
    seedGlobalState(apiMode, url, model = 'llama3.2') {
      // Roo stores provider config in `providerProfiles` and uses FLAT auto-approve
      // keys. Its globalState row layout is confirmed by a discovery run; we seed
      // both the provider profile and the flat permissive flags.
      const id = 'rooveterinaryinc.roo-cline';
      const cfg = apiMode === 'openai'
        ? { apiProvider: 'openai', openAiBaseUrl: `${url}/v1`, openAiApiKey: 'optarena', openAiModelId: model, id: 'default' }
        : { apiProvider: 'ollama', ollamaBaseUrl: url, ollamaModelId: model, id: 'default' };
      const providerProfiles = {
        currentApiConfigName: 'default',
        apiConfigs: { default: cfg },
      };
      const flags = {
        providerProfiles,
        autoApprovalEnabled: true,
        alwaysAllowWrite: true,
        alwaysAllowReadOnly: true,
        alwaysAllowExecute: true,
        alwaysAllowBrowser: true,
        alwaysAllowMcp: true,
        telemetrySetting: 'disabled',
      };
      // Seed BOTH the standard Memento blob (single row under the ext id) AND
      // Cline-style prefixed rows, so whichever scheme Roo reads is covered.
      const items = { [id]: flags };
      for (const [k, v] of Object.entries(flags)) items[`${id}/${k}`] = v;
      return items;
    },
  },

  kilo: {
    key: 'kilo',
    label: 'Kilo Code',
    // Kilo Code is a Roo-Cline fork: same globalState config shape, its own ids.
    extDirPrefix: 'kilocode.kilo-code',
    viewCommand: 'workbench.view.extension.kilo-code-ActivityBar',
    newTaskCommand: 'kilo-code.plusButtonClicked',
    wizard: 'roo',
    approveRe: AGENT_APPROVE_RE,
    rejectRe: AGENT_REJECT_RE,
    configKind: 'globalState',
    seedGlobalState(apiMode, url, model = 'llama3.2') {
      const id = 'kilocode.kilo-code';
      const cfg = apiMode === 'openai'
        ? { apiProvider: 'openai', openAiBaseUrl: `${url}/v1`, openAiApiKey: 'optarena', openAiModelId: model, id: 'default' }
        : { apiProvider: 'ollama', ollamaBaseUrl: url, ollamaModelId: model, id: 'default' };
      const providerProfiles = {
        currentApiConfigName: 'default',
        apiConfigs: { default: cfg },
      };
      const flags = {
        providerProfiles,
        autoApprovalEnabled: true,
        alwaysAllowWrite: true,
        alwaysAllowReadOnly: true,
        alwaysAllowExecute: true,
        alwaysAllowBrowser: true,
        alwaysAllowMcp: true,
        telemetrySetting: 'disabled',
      };
      const items = { [id]: flags };
      for (const [k, v] of Object.entries(flags)) items[`${id}/${k}`] = v;
      return items;
    },
  },

  continue: {
    key: 'continue',
    label: 'Continue',
    extDirPrefix: 'continue.continue',
    viewCommand: 'workbench.view.extension.continue',
    newTaskCommand: 'continue.newSession',
    prepareInput: 'continue.focusContinueInput',
    agentModeHint: 'Agent',
    approveRe: AGENT_APPROVE_RE,
    rejectRe: AGENT_REJECT_RE,
    configKind: 'continueYaml',
    // Isolate Continue's global dir into the test storage so we don't touch the
    // user's real ~/.continue config.
    launchEnv(storagePath) {
      return { CONTINUE_GLOBAL_DIR: path.join(storagePath, 'continue') };
    },
    seedFiles(storagePath, apiMode, url, model = 'llama3.2') {
      const dir = path.join(storagePath, 'continue');
      fs.mkdirSync(dir, { recursive: true });
      const modelLines = apiMode === 'openai'
        ? [
            '  - name: OptArena Backend',
            '    provider: openai',
            `    model: ${model}`,
            `    apiBase: ${url}/v1`,
            '    apiKey: optarena',
            '    roles: [chat, edit, apply]',
          ]
        : [
            '  - name: OptArena Backend',
            '    provider: ollama',
            `    model: ${model}`,
            `    apiBase: ${url}`,
            '    roles: [chat, edit, apply]',
          ];
      const yaml = [
        'name: OptArena Test',
        'version: 0.0.1',
        'schema: v1',
        'models:',
        ...modelLines,
        '',
      ].join('\n');
      fs.writeFileSync(path.join(dir, 'config.yaml'), yaml, 'utf-8');
      // Disable telemetry + onboarding noise.
      fs.writeFileSync(path.join(dir, '.continuerc.json'),
        JSON.stringify({ allowAnonymousTelemetry: false }), 'utf-8');
    },
  },
};

export function getExtension(key) {
  const ext = EXTENSIONS[(key || 'cline').toLowerCase()];
  if (!ext) {
    throw new Error(`Unknown EXT="${key}". Valid: ${Object.keys(EXTENSIONS).join(', ')}`);
  }
  return ext;
}
