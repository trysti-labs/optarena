// Portless in-process test helpers: listen on port 0, talk via global fetch.
const { createApp } = require("../src/app");
const warehouseStore = require("../src/store/warehouseStore");
const itemStore = require("../src/store/itemStore");
const movementStore = require("../src/store/movementStore");

function startServer() {
  warehouseStore.resetStore();
  itemStore.resetStore();
  movementStore.resetStore();
  const app = createApp();
  const server = app.listen(0);
  const base = `http://127.0.0.1:${server.address().port}`;
  return {
    base,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

async function api(base, method, path, body) {
  const resp = await fetch(base + path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let json = null;
  try {
    json = await resp.json();
  } catch {
    // no JSON body
  }
  return { status: resp.status, json };
}

module.exports = { startServer, api };
