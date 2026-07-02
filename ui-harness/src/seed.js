/**
 * src/seed.js
 * ───────────
 * Generic writer for VS Code extension globalState. The per-extension content
 * (which keys/values) lives in src/extensions.js; this module just persists a
 * { key: value } map into the test profile's state.vscdb ItemTable.
 *
 * VS Code stores extension globalState in `<userDataDir>/User/globalStorage/
 * state.vscdb`. Keys/values depend on the extension (see extensions.js).
 */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const Database = require('better-sqlite3');

/**
 * Create/seed the state.vscdb at `stateDbPath` with the given { key: value }
 * items (values JSON-encoded). Creates parent dirs and the ItemTable with VS
 * Code's exact schema if missing. Returns the list of keys written.
 */
export function seedGlobalStateDb(stateDbPath, items) {
  fs.mkdirSync(path.dirname(stateDbPath), { recursive: true });

  const db = new Database(stateDbPath);
  try {
    db.exec(
      'CREATE TABLE IF NOT EXISTS ItemTable ' +
      '(key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)',
    );
    const insert = db.prepare(
      'INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)',
    );
    const tx = db.transaction(() => {
      for (const [key, value] of Object.entries(items)) {
        insert.run(key, JSON.stringify(value));
      }
    });
    tx();
    return Object.keys(items);
  } finally {
    db.close();
  }
}
