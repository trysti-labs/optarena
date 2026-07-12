// Portless in-process test helpers: listen on port 0, talk via global fetch.
// Tests run against the COMPILED output - `tsc -p .` must have run first
// (the repo's `npm test` script and every OptArena oracle on this repo do).
const { createApp } = require("../dist/app");
const store = require("../dist/store/linkStore");

function startServer() {
  store.resetStore();
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
    redirect: "manual",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let json = null;
  try {
    json = await resp.json();
  } catch {
    // 204s and redirects have no JSON body
  }
  return { status: resp.status, headers: resp.headers, json };
}

module.exports = { startServer, api, store };
