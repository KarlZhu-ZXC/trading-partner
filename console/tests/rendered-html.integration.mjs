import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { after, before } from "node:test";
import test from "node:test";

let serverProcess;
let serverOrigin;

async function availablePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const { port } = address;
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return port;
}

before(async () => {
  const port = await availablePort();
  serverOrigin = `http://127.0.0.1:${port}`;
  serverProcess = spawn(
    process.execPath,
    ["node_modules/next/dist/bin/next", "start", "--hostname", "127.0.0.1", "--port", String(port)],
    {
      cwd: new URL("..", import.meta.url),
      env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );

  let diagnostics = "";
  serverProcess.stdout.on("data", (chunk) => { diagnostics += chunk; });
  serverProcess.stderr.on("data", (chunk) => { diagnostics += chunk; });
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (serverProcess.exitCode !== null) {
      throw new Error(`Next server exited before readiness:\n${diagnostics}`);
    }
    try {
      const response = await fetch(serverOrigin);
      if (response.ok) return;
    } catch {
      // The server has not bound its loopback socket yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Next server did not become ready:\n${diagnostics}`);
});

after(async () => {
  if (!serverProcess || serverProcess.exitCode !== null) return;
  serverProcess.kill("SIGTERM");
  await new Promise((resolve) => serverProcess.once("exit", resolve));
});

async function render(route = "/") {
  return fetch(`${serverOrigin}${route}`, { headers: { accept: "text/html" } });
}

test("server-renders the local control room", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /Trading Partner/);
  assert.match(html, /Notes/);
  assert.match(html, /<html data-theme="light" lang="en"/);
  assert.match(html, /Loopback only/);
  assert.match(html, /data-theme="light"/);
  assert.match(html, /Theme/);
  assert.match(html, /Light/);
  assert.match(html, /Dark/);
  assert.match(html, /Notes/);
  assert.match(html, /Capabilities/);
  assert.doesNotMatch(html, /aria-label="Copilot Rail"/);
  assert.doesNotMatch(html, /Your site is taking shape|react-loading-skeleton/);
});

test("renders all primary local-console routes", async () => {
  for (const [route, heading] of [
    ["/monitors", "Monitors"],
    ["/decision-workbench", "Journal"],
    ["/research", "Research"],
    ["/agenda", "Catalyst Agenda"],
    ["/scorecards", "Scorecards"],
    ["/capabilities", "Capabilities"],
    ["/portfolio", "Portfolio"],
    ["/retro", "Journal"],
    ["/operations", "Operations"],
  ]) {
    const response = await render(route);
    assert.equal(response.status, 200);
    assert.match(await response.text(), new RegExp(heading));
  }
});

test("legacy Chat route redirects to the shared Copilot Rail", async () => {
  const response = await render("/chat");
  assert.equal(response.status, 200);
  assert.match(response.url, /\/?\?agent=open$/);
  const html = await response.text();
  assert.match(html, /Notes/);
});
