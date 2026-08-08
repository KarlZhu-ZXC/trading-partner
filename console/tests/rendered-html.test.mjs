import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFile, readdir } from "node:fs/promises";
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
  assert.match(html, /Investment Research Console/);
  assert.match(html, /<html data-theme="light" lang="en"/);
  assert.match(html, /Loopback only/);
  assert.match(html, /data-theme="light"/);
  assert.match(html, /Theme/);
  assert.match(html, /Light/);
  assert.match(html, /Dark/);
  assert.match(html, /Overview/);
  assert.match(html, /Capabilities/);
  assert.doesNotMatch(html, /Your site is taking shape|react-loading-skeleton/);
});

test("restores the persisted sidebar width before paint and keeps it in sync", async () => {
  const layoutSource = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");
  const shellSource = await readFile(new URL("../app/components/console-shell.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(layoutSource, /trading-partner-sidebar-collapsed/);
  assert.match(layoutSource, /document\.documentElement\.classList\.toggle\("sidebar-collapsed"/);
  assert.match(shellSource, /document\.documentElement\.classList\.toggle\("sidebar-collapsed", storedCollapsed\)/);
  assert.match(shellSource, /document\.documentElement\.classList\.toggle\("sidebar-collapsed", next\)/);
  assert.match(styles, /html\.sidebar-collapsed \.app-shell \{ --sidebar-width:76px; \}/);
});

test("places Research before Monitors in primary navigation", async () => {
  const shellSource = await readFile(new URL("../app/components/console-shell.tsx", import.meta.url), "utf8");
  assert.ok(shellSource.indexOf('href: "/research"') < shellSource.indexOf('href: "/monitors"'));
});

test("renders all primary local-console routes", async () => {
  for (const [route, heading] of [
    ["/monitors", "Monitor Runs &amp; Events"],
    ["/research", "Research workspace"],
    ["/capabilities", "MCP Capabilities"],
    ["/portfolio", "Portfolio"],
    ["/operations", "Operations Center"],
  ]) {
    const response = await render(route);
    assert.equal(response.status, 200);
    assert.match(await response.text(), new RegExp(heading));
  }
});

test("research console is a responsive Research Subject/Thesis master-detail workspace", async () => {
  const response = await render("/research");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Research workspace/);
  assert.match(html, /Research/);

  const source = await readFile(new URL("../app/research/page.tsx", import.meta.url), "utf8");
  const shellSource = await readFile(new URL("../app/components/console-shell.tsx", import.meta.url), "utf8");
  assert.match(source, /\/api\/research/);
  assert.match(source, /including archived/i);
  assert.match(source, /const SUBJECT_STATUSES = \["draft", "active", "archived"\]/);
  assert.match(source, /const \[status, setStatus\] = useState\("ACTIVE"\)/);
  assert.match(source, /const THESIS_STATUSES = \["draft", "active", "strengthened", "weakened", "invalidated", "archived"\]/);
  assert.match(source, /RESEARCH SUBJECTS/);
  assert.match(source, /Research Subject/);
  assert.match(source, /SubjectAggregate/);
  assert.match(source, /SubjectDraft/);
  assert.match(source, /SubjectEditor/);
  assert.doesNotMatch(source, /CaseAggregate|CaseDraft|CaseEditor|ResearchCaseDetail/);
  assert.match(source, /latest_revisions/);
  assert.match(source, /Partial read failed/);
  assert.match(source, /investment_case_manage/);
  assert.match(source, /operation: "update"/);
  assert.match(source, /operation: "archive"/);
  assert.match(source, /research_judgment_propose/);
  assert.match(source, /research_judgment_confirm/);
  assert.match(source, /pending_candidates/);
  assert.match(source, /INSTRUMENT SELECTION/);
  assert.match(source, /candidateInstrumentId/);
  assert.match(source, /selection_reason/);
  assert.match(source, /"shortlisted"/);
  assert.match(source, /"selected"/);
  assert.match(source, /"rejected"/);
  assert.match(source, /subject-activate-propose/);
  assert.match(source, /Start tracking/);
  assert.match(source, /thesisStatusExplicit/);
  assert.match(source, /Edit Thesis · New Revision/);
  assert.match(source, /Parent PRIMARY Thesis/);
  assert.match(source, /Rival Theses/);
  assert.match(source, /ThesisRelationshipList/);
  assert.match(source, /parent_thesis_id/);
  assert.match(source, /rival_thesis_ids/);
  assert.match(source, /decideCandidate\(item, "withdraw"\)/);
  assert.match(source, /#subject-/);
  assert.match(source, /legacySubjectId/);
  assert.match(source, /\^#case-/);
  assert.match(source, /\/api\/monitors\?run_limit=1&event_limit=1/);
  assert.match(source, /master-detail/);
  assert.doesNotMatch(source, /Read-only display of all Research Subjects/);
  assert.match(shellSource, /href: "\/research"/);
});

test("overview Monitor titles deep-link to async-loaded definition cards", async () => {
  const overviewSource = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const attentionSource = await readFile(new URL("../app/lib/attention.ts", import.meta.url), "utf8");
  const monitorsSource = await readFile(new URL("../app/monitors/page.tsx", import.meta.url), "utf8");
  const editorSource = await readFile(new URL("../app/monitors/monitor-editor.tsx", import.meta.url), "utf8");

  assert.match(overviewSource, /href=\{`\/monitors#\$\{monitorAnchorId\(monitor\.monitor_id\)\}`\}/);
  assert.match(overviewSource, /buildConsoleNotices/);
  assert.match(overviewSource, /Waiting for Next Evaluation/);
  assert.match(overviewSource, /No manual action required/);
  assert.match(attentionSource, /\/research#subject-/);
  assert.match(attentionSource, /OIL_WEEKEND_REFERENCE_UNAVAILABLE/);
  assert.match(attentionSource, /does not mean data is currently unavailable/);
  assert.match(attentionSource, /target Monitor is paused or archived/i);
  assert.match(overviewSource, /runs\.slice\(0, 8\)/);
  assert.match(monitorsSource, /id=\{monitorAnchorId\(monitor\.monitor_id\)\}/);
  assert.match(monitorsSource, /window\.scrollTo/);
  assert.doesNotMatch(monitorsSource, /scrollIntoView/);
  assert.match(monitorsSource, /monitor_created_at/);
  assert.match(monitorsSource, /monitor_updated_at/);
  assert.match(monitorsSource, /Last Edited/);
  assert.match(monitorsSource, /rule\.description/);
  assert.match(monitorsSource, /monitorPriceObservation/);
  assert.match(monitorsSource, /monitor-price-observation/);
  assert.match(monitorsSource, /Latest Price/);
  assert.match(monitorsSource, /showIndividualObservation/);
  assert.match(monitorsSource, /monitorMatchesInstrument/);
  assert.match(monitorsSource, /Filter Monitors by target symbol/);
  assert.match(monitorsSource, /Filter by Monitor status/);
  assert.match(monitorsSource, /Restore & Activate/);
  assert.match(monitorsSource, /changeMonitorStatus/);
  assert.match(monitorsSource, /statusFilter === "ALL"/);
  assert.match(monitorsSource, /MONITOR_STATUSES\)\[number\]>\("ACTIVE"\)/);
  assert.match(monitorsSource, /Archiving…/);
  assert.match(monitorsSource, /monitor-list-panel/);
  assert.match(monitorsSource, /Web Search Sources/);
  assert.match(monitorsSource, /web_source_urls/);
  assert.match(editorSource, /missing a human-readable meaning/);
  assert.match(editorSource, /initialMonitor\?\.subject_id/);
  assert.match(editorSource, /case_id: subjectId\.trim\(\)/);
});

test("portfolio is a four-tab durable hub with explicit account writes", async () => {
  const portfolioSource = await readFile(new URL("../app/portfolio/page.tsx", import.meta.url), "utf8");

  assert.match(portfolioSource, /\/api\/portfolio\?transaction_limit=500&coverage_limit=100/);
  assert.match(portfolioSource, /Holdings/);
  assert.match(portfolioSource, /Activity/);
  assert.match(portfolioSource, /Performance/);
  assert.match(portfolioSource, /Risk/);
  assert.match(portfolioSource, /external_state_sync/);
  assert.doesNotMatch(portfolioSource, /watchlist_manage/);
  assert.doesNotMatch(portfolioSource, /Sync Watchlist/);
  assert.match(portfolioSource, /risk_policy_update/);
  assert.match(portfolioSource, /portfolio_risk_get/);
  assert.match(portfolioSource, /window\.location\.hash/);
  assert.match(portfolioSource, /SortableHeader/);
  assert.match(portfolioSource, /execution_effect/);
  assert.match(portfolioSource, /resultEnvelope = envelope\(invocationResult\(response\)\)/);
  assert.doesNotMatch(portfolioSource, /\/api\/accounts/);
});

test("operations provides a guarded foreground Schwab OAuth flow", async () => {
  const operationsSource = await readFile(new URL("../app/operations/page.tsx", import.meta.url), "utf8");

  assert.match(operationsSource, /\/api\/schwab\/oauth/);
  assert.match(operationsSource, /schwab_oauth_renew/);
  assert.match(operationsSource, /schwab_oauth_renew_confirmed/);
  assert.match(operationsSource, /waiting for the Schwab callback/i);
  assert.match(operationsSource, /Use only the newly opened tab/);
});

test("keeps the default console UI copy English-only", async () => {
  const appRoot = new URL("../app/", import.meta.url);
  const files = await readdir(appRoot, { recursive: true });
  for (const file of files.filter((name) => /\.(?:ts|tsx)$/.test(name))) {
    const source = await readFile(new URL(file, appRoot), "utf8");
    assert.doesNotMatch(source, /\p{Script=Han}/u, `${file} contains Chinese default UI copy`);
  }
});
