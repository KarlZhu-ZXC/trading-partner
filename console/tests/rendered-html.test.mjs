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
  assert.match(html, /Overview/);
  assert.match(html, /<html data-theme="light" lang="en"/);
  assert.match(html, /Loopback only/);
  assert.match(html, /data-theme="light"/);
  assert.match(html, /Theme/);
  assert.match(html, /Light/);
  assert.match(html, /Dark/);
  assert.match(html, /Overview/);
  assert.match(html, /Capabilities/);
  assert.match(html, /Agent Rail/);
  assert.doesNotMatch(html, /Your site is taking shape|react-loading-skeleton/);
});

test("restores the persisted sidebar width before paint and keeps it in sync", async () => {
  const layoutSource = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");
  const shellSource = await readFile(new URL("../app/components/console-shell.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(layoutSource, /trading-partner-sidebar-collapsed/);
  assert.match(layoutSource, /trading-partner-agent-rail-collapsed/);
  assert.match(layoutSource, /matchMedia\("\(max-width: 1100px\)"\)/);
  assert.match(layoutSource, /document\.documentElement\.classList\.toggle\("sidebar-collapsed"/);
  assert.match(layoutSource, /document\.documentElement\.classList\.toggle\("agent-rail-collapsed"/);
  assert.match(shellSource, /document\.documentElement\.classList\.toggle\("sidebar-collapsed", overlayViewport \|\| storedCollapsed\)/);
  assert.match(shellSource, /document\.documentElement\.classList\.toggle\("sidebar-collapsed", next\)/);
  assert.match(styles, /html\.sidebar-collapsed \.app-shell \{ --sidebar-width:76px; \}/);
  assert.match(styles, /html\.agent-rail-collapsed \.app-shell \{ --agent-rail-width:0px; \}/);
});

test("provides independent Obsidian-style navigation and Agent panel toggles", async () => {
  const shellSource = await readFile(new URL("../app/components/console-shell.tsx", import.meta.url), "utf8");
  const railSource = await readFile(new URL("../app/components/agent-rail.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(shellSource, /aria-controls="console-navigation-panel"/);
  assert.match(shellSource, /aria-controls="console-agent-panel"/);
  assert.match(shellSource, /Open navigation panel/);
  assert.match(shellSource, /Open Agent Panel/);
  assert.match(shellSource, /LOCAL HUB/);
  assert.doesNotMatch(shellSource, /LOCAL CONTROL ROOM/);
  assert.match(shellSource, /event\.key === "Escape"/);
  assert.match(shellSource, /event\.key\.toLowerCase\(\) === "l"/);
  assert.match(shellSource, /event\.key\.toLowerCase\(\) === "a"/);
  assert.match(railSource, /id="console-agent-panel"/);
  assert.match(styles, /workspace-pane-backdrop\.visible/);
  assert.match(styles, /--sidebar-width:232px/);
  assert.match(styles, /transform:translateX\(-100%\)/);
  assert.match(styles, /agent-rail\.collapsed[\s\S]*visibility: hidden/);
  assert.match(shellSource, /className="global-header"/);
  assert.match(shellSource, /<ThemeSwitch \/>/);
  assert.doesNotMatch(shellSource, /className="sidebar-toggle"/);
  assert.match(styles, /\.main-content \{ --content-gutter:42px; padding:0 var\(--content-gutter\) 56px; min-width:0; \}/);
  assert.match(styles, /\.global-header \{[^}]*min-height:54px;/);
  assert.match(styles, /\.page-header \{[^}]*align-items:center;[^}]*min-height:42px;[^}]*margin-bottom:18px;/);
  assert.match(styles, /\.environment-chip \{[^}]*min-height:34px;/);
  assert.match(styles, /\.page-level-actions \{[^}]*align-self:center;/);
});

test("places Research before Monitors in primary navigation", async () => {
  const shellSource = await readFile(new URL("../app/components/console-shell.tsx", import.meta.url), "utf8");
  assert.match(shellSource, /CONSOLE_PAGE_LABELS/);
  assert.match(shellSource, /"decision-workbench": "Workbench"/);
  assert.match(shellSource, /const label = CONSOLE_PAGE_LABELS\[item\.key\]/);
  assert.match(shellSource, /<h1>\{CONSOLE_PAGE_LABELS\[active\]\}<\/h1>/);
  assert.ok(shellSource.indexOf('href: "/decision-workbench"') < shellSource.indexOf('href: "/research"'));
  assert.ok(shellSource.indexOf('href: "/research"') < shellSource.indexOf('href: "/monitors"'));
  assert.ok(shellSource.indexOf('href: "/research"') < shellSource.indexOf('href: "/scorecards"'));
  assert.ok(shellSource.indexOf('href: "/scorecards"') < shellSource.indexOf('href: "/monitors"'));
});

test("automatically authenticates Console writes with a restart-safe session token", async () => {
  const apiSource = await readFile(new URL("../app/lib/api.ts", import.meta.url), "utf8");
  assert.match(apiSource, /\/api\/session/);
  assert.match(apiSource, /X-Trading-Partner-Console-Token/);
  assert.match(apiSource, /response\.status === 403/);
});

test("keeps the data API on loopback while LAN mode uses authenticated same-origin access", async () => {
  const apiSource = await readFile(new URL("../app/lib/api.ts", import.meta.url), "utf8");
  const authSource = await readFile(new URL("../app/lib/lan-auth.ts", import.meta.url), "utf8");
  const proxySource = await readFile(new URL("../app/api/console/[...path]/route.ts", import.meta.url), "utf8");
  const startSource = await readFile(new URL("../scripts/start-lan.mjs", import.meta.url), "utf8");

  assert.match(apiSource, /"\/api\/console"/);
  assert.match(proxySource, /http:\/\/127\.0\.0\.1:8765/);
  assert.match(proxySource, /isLoopbackRequest/);
  assert.match(proxySource, /x-trading-partner-console-token/);
  assert.match(proxySource, /path\[0\] === "api"/);
  assert.match(proxySource, /`api\/\$\{path\.join\("\/"\)\}`/);
  assert.doesNotMatch(proxySource, /request\.headers\.get\(["'](?:origin|cookie|x-forwarded)/i);
  assert.match(authSource, /LAN_PASSWORD_MIN_LENGTH = 16/);
  assert.match(authSource, /HMAC/);
  assert.match(startSource, /"--hostname", "0\.0\.0\.0"/);
  assert.doesNotMatch(startSource, /NEXT_PUBLIC_TRADING_PARTNER_CONSOLE_LAN_PASSWORD/);
});

  test("renders all primary local-console routes", async () => {
    for (const [route, heading] of [
      ["/monitors", "Monitors"],
      ["/decision-workbench", "Workbench"],
      ["/research", "Research"],
      ["/agenda", "Catalyst Agenda"],
      ["/scorecards", "Scorecards"],
      ["/capabilities", "Capabilities"],
      ["/portfolio", "Portfolio"],
      ["/retro", "Trade Retro"],
      ["/operations", "Operations"],
      ["/chat", "Agent Chat"],
    ]) {
    const response = await render(route);
    assert.equal(response.status, 200);
    assert.match(await response.text(), new RegExp(heading));
    }
  });

test("decision workbench aggregates durable stages without replacing specialist pages", async () => {
  const source = await readFile(new URL("../app/decision-workbench/page.tsx", import.meta.url), "utf8");
  assert.match(source, /\/api\/decision-workbench/);
  assert.equal((source.match(/useApi<Dict>/g) ?? []).length, 1);
  assert.doesNotMatch(source, /useApi<Dict>\("\/api\/(research|monitors|agenda|retro|scorecards)/);
  assert.match(source, /partial_failures/);
  assert.match(source, /postApi/);
  assert.match(source, /"Acknowledge"/);
  assert.match(source, />Resolve</);
  assert.match(source, /INCOMPLETE/);
  assert.match(source, /No Provider refresh, automatic confirmation, position change, or order execution/);
  assert.match(source, /research#subject-/);
  assert.match(source, /href="\/monitors"/);
  assert.match(source, /href="\/retro"/);
});

test("agenda route uses the durable Catalyst Agenda write contract", async () => {
  const response = await render("/agenda");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Catalyst Agenda/);

  const shellSource = await readFile(new URL("../app/components/console-shell.tsx", import.meta.url), "utf8");
  const source = await readFile(new URL("../app/agenda/page.tsx", import.meta.url), "utf8");
  assert.match(shellSource, /href: "\/agenda"/);
  assert.match(source, /\/api\/agenda/);
  assert.match(source, /\/api\/agenda\/summary-preview/);
  assert.match(source, /\/api\/agenda\/summary-send/);
  assert.match(source, /research_memory_append/);
  assert.match(source, /LINK_OUTCOME/);
  assert.match(source, /event_id/);
  assert.match(source, /report_id/);
  assert.match(source, /evidence_id/);
  assert.match(source, /outcome_occurred_at/);
  assert.match(source, /outcome_note/);
  assert.match(source, /linkedEventIds/);
  assert.match(source, /linkedEvidenceIds/);
  assert.match(source, /Resolved Supporting Evidence/);
  assert.match(source, /statusIsUpcoming\s*\?/);
  assert.match(source, /statusIsOccurred\s*\?/);
  assert.match(source, /Revise Outcome/);
  assert.match(source, /Daily summary queued for delivery/);
  assert.match(source, /date_drift_count/);
  assert.match(source, /operation:\s*["']agenda_item["']/);
  assert.match(source, /AgendaAction|action/);
  assert.match(source, /idempotency_key:/);
  assert.match(source, /confirmed_by:\s*["']user["']/);
  assert.match(source, /authorization_note/);
  assert.match(source, /agenda_item_id/);
  assert.match(source, /expected_version/);
  assert.match(source, /cancellation_reason/);
  assert.match(source, /extractItems/);
});

test("monitor evidence labels support explicit and legacy previous-close feature ids", async () => {
  const source = await readFile(new URL("../app/monitors/page.tsx", import.meta.url), "utf8");
  assert.match(source, /return_from_previous_regular_session_close_pct/);
  assert.match(source, /quote_return_pct/);
  assert.match(source, /return_from_previous_regular_session_close/);
});

test("portfolio displays valuation-only Snapshot Price and title-cases table headers", async () => {
  const source = await readFile(new URL("../app/portfolio/page.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /function snapshotPrice\(/);
  assert.match(source, /Math\.abs\(marketValue\) \/ quantity/);
  assert.match(source, /label="Snapshot Price"/);
  assert.match(source, /label="Market Value \(Not NAV\)"/);
  assert.doesNotMatch(source, /label="Snapshot price"/);
  assert.match(source, /title="Portfolio Exposure"/);
  assert.match(source, /Portfolio Total Value/);
  assert.match(source, /Gross Position Value/);
  assert.match(source, /gross exposure—not account NAV or long\/short net exposure/);
  assert.match(source, /function compactInstrumentId\(/);
  assert.match(source, /parts\.length >= 3 \? `\$\{parts\[1\]\}:\$\{parts\.slice\(2\)\.join\(":"\)\}`/);
  assert.match(styles, /thead th,.agent-message-table-wrap th \{ text-transform:capitalize; \}/);
});

test("scorecards route uses judgment scorecard source-contract calls", async () => {
  const response = await render("/scorecards");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Scorecards/);

  const shellSource = await readFile(new URL("../app/components/console-shell.tsx", import.meta.url), "utf8");
  const source = await readFile(new URL("../app/scorecards/page.tsx", import.meta.url), "utf8");

  assert.match(shellSource, /href: "\/scorecards"/);
  assert.match(shellSource, /Scorecards/);
  assert.match(source, /\/api\/scorecards/);
  assert.match(source, /window\.location\.search/);
  assert.match(source, /research_workflow_run/);
  assert.match(source, /operation:\s*["']judgment_scorecard["']/);
  assert.match(source, /case_id:\s*subjectId/);
  assert.match(source, /thesis_id:\s*thesisId/);
  assert.doesNotMatch(source, /window\.confirm/);
  assert.match(source, /TARGET_DIMENSION_OUTCOMES = \["NOT_EVALUATED", "EVALUATED", "PARTIAL", "PASS", "FAIL"\]/);
});

test("research console is a responsive Research Subject/Thesis master-detail workspace", async () => {
  const response = await render("/research");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Research/);
  assert.match(html, /Research/);

  const source = await readFile(new URL("../app/research/page.tsx", import.meta.url), "utf8");
  const entitySource = await readFile(new URL("../app/components/entity-browser.tsx", import.meta.url), "utf8");
  const continuitySource = await readFile(new URL("../app/research/research-continuity.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  const shellSource = await readFile(new URL("../app/components/console-shell.tsx", import.meta.url), "utf8");
  assert.match(source, /\/api\/research/);
  assert.match(source, /including archived/i);
  assert.match(source, /const SUBJECT_STATUSES = \["draft", "active", "archived"\]/);
  assert.match(source, /const \[status, setStatus\] = useState\("ACTIVE"\)/);
  assert.match(source, /const THESIS_STATUSES = \["draft", "active", "strengthened", "weakened", "invalidated", "archived"\]/);
  assert.match(source, /Research Library/);
  assert.doesNotMatch(source, /Browse and manage durable Research Subjects/);
  assert.match(source, /Text Filter/);
  assert.doesNotMatch(source, /title="Research Library"/);
  assert.doesNotMatch(source, /research-library-count/);
  assert.match(source, /<EntityBrowser/);
  assert.match(source, /entity-filter-notice/);
  assert.match(entitySource, /entity-filter-results/);
  assert.doesNotMatch(source, /research-browser-range/);
  assert.doesNotMatch(source, /research-browser-footer/);
  assert.match(source, /ResearchPageActions/);
  assert.match(source, /page-action-menu/);
  assert.match(source, /pageActions=\{<ResearchPageActions/);
  assert.match(source, /Open Research Page Actions/);
  assert.doesNotMatch(source, /className="toolbar research-toolbar"/);
  assert.match(entitySource, /EntityBrowser/);
  assert.match(source, /Filter by Research Subject Status/);
  assert.match(source, /Show Previous Research Subjects/);
  assert.match(source, /Show Next Research Subjects/);
  assert.match(source, /The current Research Subject is outside this filter/);
  assert.doesNotMatch(source, /kicker="RESEARCH SUBJECTS" title="All Research Subjects"/);
  assert.match(source, /Research Subject/);
  assert.match(source, /SubjectAggregate/);
  assert.match(source, /SubjectDraft/);
  assert.match(source, /SubjectEditor/);
  assert.doesNotMatch(source, /CaseAggregate|CaseDraft|CaseEditor|ResearchCaseDetail/);
  assert.match(source, /latest_revisions/);
  assert.match(source, /Partial Read Failed/);
  assert.match(source, /investment_case_manage/);
  assert.match(source, /operation: "update"/);
  assert.match(source, /operation: "archive"/);
  assert.match(source, /research_judgment_propose/);
  assert.match(source, /research_judgment_confirm/);
  assert.match(source, /pending_candidates/);
  assert.match(source, /continuity-checklist-action/);
  assert.match(source, /title="Health Check"/);
  assert.doesNotMatch(source, /Next evidence and review obligations from durable research state/);
  assert.doesNotMatch(source, /continuitySignals\.length \? `\$\{continuitySignals\.length\} OPEN` : "READY"/);
  assert.match(source, /goToSection\("research-section-review"\)/);
  assert.ok(source.indexOf('className="research-section-nav"') < source.indexOf('id="research-section-overview"'));
  assert.ok(source.indexOf('id="research-section-review"') < source.indexOf('id="research-section-selection"'));
  assert.match(source, /research-section-nav/);
  assert.match(source, /RESEARCH_MODULES/);
  assert.match(source, /Overview/);
  assert.match(source, /Instruments/);
  assert.match(source, /Trade Plan/);
  assert.match(source, /<HorizontalTabs/);
  assert.match(source, /kicker=\{text\(researchSubject\.subject_type/);
  assert.match(source, /title=\{text\(researchSubject\.title/);
  assert.match(source, /role="tabpanel"/);
  assert.match(source, /url\.searchParams\.set\("section", module\)/);
  assert.match(continuitySource, /activeModule !== "trade-plan"/);
  assert.match(continuitySource, /activeModule !== "history"/);
  assert.match(continuitySource, /activeModule !== "evidence"/);
  assert.match(continuitySource, /const \[expanded, setExpanded\] = useState\(false\)/);
  assert.match(continuitySource, /if \(expanded && timeline === null/);
  assert.match(continuitySource, /research-collapsed-hint/);
  assert.match(styles, /\.main-content \{ overflow-x: clip; \}/);
  assert.match(styles, /\.research-master-detail \{ display:grid; grid-template-columns:minmax\(0,1fr\)/);
  assert.match(styles, /\.entity-filters \{ display:grid; grid-template-columns:/);
  assert.match(styles, /\.entity-filters \{ display:grid; grid-template-columns:minmax\(240px,1fr\) minmax\(160px,220px\) auto auto/);
  assert.match(styles, /\.page-action-list \{[^}]*position:absolute/);
  assert.match(entitySource, /new ResizeObserver/);
  assert.match(entitySource, /target === "viewport"/);
  assert.match(entitySource, /setPage\(Math\.floor\(selectedIndex \/ pageSize\)\)/);
  assert.match(styles, /\.entity-index-list \{ display:grid; grid-template-columns:repeat\(var\(--entity-per-page,6\),minmax\(0,1fr\)\)/);
  assert.match(styles, /\.entity-browser \{ display:grid; grid-template-columns:28px minmax\(0,1fr\) 28px/);
  assert.match(styles, /@keyframes entity-items-next/);
  assert.match(entitySource, /slide-\$\{pageDirection\}/);
  assert.match(styles, /-webkit-line-clamp:2/);
  assert.match(styles, /\.research-section-nav \{ position:sticky/);
  assert.match(styles, /\.horizontal-tabs button\.selected/);
  assert.match(styles, /\.research-module-panel\[hidden\] \{ display:none; \}/);
  assert.doesNotMatch(shellSource, /className="eyebrow"/);
  assert.doesNotMatch(shellSource, /eyebrow:\s*string/);
  assert.match(source, /INSTRUMENT SELECTION/);
  assert.match(source, /candidateInstrumentId/);
  assert.match(source, /instrument_resolve/);
  assert.match(source, /role="combobox"/);
  assert.match(source, /candidate-instrument-suggestions/);
  assert.match(styles, /\.research-selection-create > \.research-field > input,\.research-selection-create > \.research-field > select \{ height:38px; \}/);
  assert.match(styles, /\.research-combobox-control input \{[^}]*height:100%/);
  assert.doesNotMatch(source, /custom values cannot be submitted/);
  assert.doesNotMatch(source, /For example, keep this blank/);
  assert.match(source, /research-field-immutable/);
  assert.match(source, /disabled=\{editing\}/);
  assert.match(source, /title="Instruments"/);
  assert.doesNotMatch(source, /Primary identity and the durable Instrument Selection pool/);
  assert.match(styles, /\.research-selection-card > \.card-head \{ margin-bottom:0; padding-bottom:0; border-bottom:0; \}/);
  assert.match(source, /research-overview-instruments/);
  assert.match(source, /additionalInstrumentCandidates/);
  assert.match(source, /ATTACHED_INSTRUMENT_STATUSES/);
  assert.match(source, /readOnly placeholder="Filled after Instrument selection"/);
  assert.match(source, /disabled=\{!candidateInstrumentId \|\| !candidateDisplayName\}/);
  assert.match(source, /required-mark/);
  assert.match(source, /notifyConsole\(\{ title: "Research Updated"/);
  assert.doesNotMatch(source, /Write succeeded; refreshing Research durable state/);
  assert.doesNotMatch(source, /Instrument ID, display name, and reason are all required/);
  assert.match(source, /candidateProposalSuccess/);
  assert.match(source, /Instrument proposal created\. Approve it in Pending Candidates to add it directly to Instruments/);
  assert.match(source, /Propose Instrument/);
  assert.match(source, /Approve Instrument/);
  assert.doesNotMatch(source, />Shortlist</);
  assert.doesNotMatch(source, />Select</);
  assert.doesNotMatch(source, /proposeCandidateStatus/);
  assert.match(source, /research-candidate-decision/);
  assert.match(source, /Confirm Candidate/);
  assert.match(source, /Reject Candidate/);
  assert.match(source, /Withdraw Proposal/);
  assert.doesNotMatch(source, /if \(window\.confirm\(confirmCopy\)\)/);
  assert.match(source, /"shortlisted"/);
  assert.match(source, /"selected"/);
  assert.doesNotMatch(source, /ATTACHED_INSTRUMENT_STATUSES = new Set\([^\n]*"rejected"/);
  assert.match(source, /subject-activate-propose/);
  assert.match(source, /Start Tracking/);
  assert.doesNotMatch(source, /Submit a candidate to move this Research Subject/);
  assert.match(continuitySource, /Create Monitor From Plan/);
  assert.match(continuitySource, /trade_plan_version=/);
  assert.match(source, /thesisStatusExplicit/);
  assert.match(source, /Edit Thesis · New Revision/);
  assert.match(source, /Parent PRIMARY Thesis/);
  assert.match(source, /Rival Theses/);
  assert.match(source, /ThesisRelationshipList/);
  assert.match(source, /research-thesis-facts/);
  assert.match(source, /DescriptionList columns=\{3\}/);
  assert.match(source, /formatDate\(thesis\.created_at\)/);
  assert.ok(source.indexOf('className="research-thesis-facts"') < source.indexOf('className="research-latest-revision"'));
  assert.match(source, /parent_thesis_id/);
  assert.match(source, /rival_thesis_ids/);
  assert.match(source, /decideCandidate\(item, "withdraw"\)/);
  assert.match(source, /#subject-/);
  assert.match(source, /\^#case-/);
  assert.match(source, /\/api\/monitors\?run_limit=1&event_limit=1/);
  assert.match(source, /master-detail/);
  assert.doesNotMatch(source, /Read-only display of all Research Subjects/);
  assert.match(shellSource, /href: "\/research"/);
});

test("card headings separate domain, object, and supporting context", async () => {
  const overviewSource = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const agendaSource = await readFile(new URL("../app/agenda/page.tsx", import.meta.url), "utf8");
  const uiSource = await readFile(new URL("../app/components/ui.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(uiSource, /subtitle\?: string/);
  assert.match(uiSource, /description\?: string/);
  assert.match(uiSource, /card-heading-copy/);
  assert.match(uiSource, /const bodyDescription = kicker \? \(description \?\? subtitle\) : description/);
  assert.match(uiSource, /!kicker && subtitle && <p className="card-subtitle">/);
  assert.ok(uiSource.indexOf('</header>') < uiSource.indexOf('{bodyDescription && <p className="card-description">'));
  assert.match(uiSource, /export function DescriptionList/);
  assert.match(uiSource, /export function HorizontalTabs/);
  assert.match(uiSource, /role="tablist"/);
  assert.match(uiSource, /event\.key === "Home"/);
  assert.match(uiSource, /event\.key === "End"/);
  assert.match(styles, /\.card-head[^}]*border-bottom:1px solid var\(--line\)/);
  assert.match(styles, /\.card-subtitle/);
  assert.match(styles, /\.description-list > div[^}]*border:1px solid var\(--line\)[^}]*background:var\(--panel\)/);
  assert.match(styles, /\.badge \{[^}]*border:0;[^}]*padding:0;[^}]*background:transparent/);
  assert.match(styles, /\.badge::before/);
  assert.match(overviewSource, /kicker="EVENT COVERAGE" title="Catalyst Pulse" subtitle="Upcoming schedule and unresolved timing gaps"/);
  assert.match(overviewSource, /kicker="DECISION WORKFLOW" title="Today’s Inbox" subtitle="Manual actions that need a deliberate response"/);
  assert.doesNotMatch(overviewSource, /kicker="CATALYST AGENDA" title="Catalyst Agenda pulse"/);
  assert.doesNotMatch(overviewSource, /kicker="TODAY" title="Decision Inbox"/);
  assert.match(agendaSource, /kicker="SCHEDULE HEALTH" title="Catalyst Pulse"/);
  assert.doesNotMatch(agendaSource, /AGEND A PROVIDER SYNC/);
});

test("overview Monitor titles deep-link to async-loaded definition cards", async () => {
  const overviewSource = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const attentionSource = await readFile(new URL("../app/lib/attention.ts", import.meta.url), "utf8");
  const monitorsSource = await readFile(new URL("../app/monitors/page.tsx", import.meta.url), "utf8");
  const editorSource = await readFile(new URL("../app/monitors/monitor-editor.tsx", import.meta.url), "utf8");

  assert.match(overviewSource, /href=\{`\/monitors#\$\{monitorAnchorId\(monitor\.monitor_id\)\}`\}/);
  assert.match(overviewSource, /buildConsoleNotices/);
  assert.match(overviewSource, /Waiting for Next Evaluation/);
  assert.match(overviewSource, /No Manual Action Required/);
  assert.match(attentionSource, /\/research#subject-/);
  assert.match(attentionSource, /OIL_WEEKEND_REFERENCE_UNAVAILABLE/);
  assert.match(attentionSource, /does not mean data is currently unavailable/);
  assert.match(attentionSource, /target Monitor is paused or archived/i);
  assert.match(overviewSource, /runs\.slice\(0, 8\)/);
  assert.match(monitorsSource, /id=\{monitorAnchorId\(selectedDefinition\.monitor_id\)\}/);
  assert.match(monitorsSource, /function selectMonitor/);
  assert.match(monitorsSource, /window\.history\.replaceState/);
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
  assert.match(monitorsSource, /Filter Monitors by Target Symbol/);
  assert.match(monitorsSource, /Filter by Monitor Status/);
  assert.match(monitorsSource, /Restore & Activate/);
  assert.match(monitorsSource, /changeMonitorStatus/);
  assert.match(monitorsSource, /statusFilter === "ALL"/);
  assert.match(monitorsSource, /MONITOR_STATUSES\)\[number\]>\("ACTIVE"\)/);
  assert.match(monitorsSource, /Archiving…/);
  assert.match(monitorsSource, /monitor-list-panel/);
  assert.match(monitorsSource, /Monitor Library/);
  assert.match(monitorsSource, /<EntityBrowser/);
  assert.match(monitorsSource, /entity-filter-notice/);
  assert.match(monitorsSource, /HorizontalTabs/);
  assert.match(monitorsSource, /Monitor modules/);
  assert.match(monitorsSource, /monitor-panel-overview/);
  assert.match(monitorsSource, /monitor-panel-rules/);
  assert.match(monitorsSource, /monitor-panel-runs/);
  assert.match(monitorsSource, /monitor-panel-events/);
  assert.match(monitorsSource, /CompositeJudgmentCard/);
  assert.match(monitorsSource, /Current Read/);
  assert.match(monitorsSource, /Next Trigger/);
  assert.match(monitorsSource, /Evidence & Diagnostics/);
  assert.match(monitorsSource, /web_source_urls/);
  assert.match(editorSource, /missing a human-readable meaning/);
  assert.match(editorSource, /source\?\.subject_id/);
  assert.match(editorSource, /case_id: subjectId\.trim\(\)/);
  assert.match(editorSource, /compile_trade_plan_conditions: compilePlanConditions/);
  assert.match(editorSource, /Compile Monitorable Conditions/);
  assert.match(monitorsSource, /trade_plan_id/);
  assert.match(monitorsSource, /newMonitorTemplate/);
});

test("portfolio is a four-tab durable hub with explicit account writes", async () => {
  const portfolioSource = await readFile(new URL("../app/portfolio/page.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

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
  assert.match(portfolioSource, /ACCOUNT_PROVENANCE_NOTES/);
  assert.match(portfolioSource, /ACCOUNT_QUALITY_ISSUES/);
  assert.match(portfolioSource, /Data Provenance/);
  assert.match(portfolioSource, /qualityCodes\.length > 0 \? "LIMITED" : "AVAILABLE"/);
  assert.match(portfolioSource, /<DescriptionList columns=\{6\}/);
  assert.match(portfolioSource, /label: "Data Time"/);
  assert.doesNotMatch(portfolioSource, /portfolio-account-facts/);
  assert.doesNotMatch(portfolioSource, /<dt>account_as_of<\/dt>|<dt>fetched_at<\/dt>|<dt>Account environment<\/dt>/);
  assert.match(portfolioSource, /portfolio-account-title[^>]*>\{accountSource\(account\)\}/);
  assert.match(portfolioSource, /Broker valuation only/);
  assert.match(portfolioSource, /ChevronsUpDown/);
  assert.match(portfolioSource, /className=\{`sort-indicator\$\{active \? " active" : ""\}`\}/);
  assert.match(styles, /\.portfolio-desktop-table \.sort-header[^}]*min-height:44px/);
  assert.match(styles, /\.sort-indicator \{[^}]*width:20px;[^}]*height:24px/);
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

test("Chat exposes the confirmation-gated Agent stream boundary", async () => {
  const response = await render("/chat");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Agent Chat/);
  assert.match(html, /Confirmation-Gated Agent Runtime/);

  const workspace = await readFile(new URL("../app/chat/chat-workspace.tsx", import.meta.url), "utf8");
  const railSource = await readFile(new URL("../app/components/agent-rail.tsx", import.meta.url), "utf8");
  const messageContentSource = await readFile(new URL("../app/components/agent-message-content.tsx", import.meta.url), "utf8");
  const pageContextSource = await readFile(new URL("../app/lib/agent-page-context.ts", import.meta.url), "utf8");
  const apiSource = await readFile(new URL("../app/lib/agent-api.ts", import.meta.url), "utf8");
  const shellSource = await readFile(new URL("../app/components/console-shell.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(shellSource, /label: "Chat"/);
  assert.match(shellSource, /AgentRail/);
  assert.match(railSource, /Agent Rail/);
  assert.match(shellSource, /trading-partner-agent-rail-collapsed/);
  assert.match(railSource, /collectEphemeralContext/);
  assert.match(railSource, /nativeEvent\.isComposing/);
  assert.match(railSource, /Cancel Current Agent Turn/);
  assert.match(railSource, /reconnectAgentTurnStream/);
  assert.match(railSource, /cancelAgentTurn/);
  assert.match(railSource, /Confirm/);
  assert.match(railSource, /Reject/);
  assert.match(railSource, /Resume Confirmation/);
  assert.match(railSource, /fetchAgentTurns/);
  assert.match(railSource, /fetchAgentPendingActions/);
  assert.match(railSource, /fetchAgentProviderModels/);
  assert.match(railSource, /aria-label="Agent Provider"/);
  assert.match(railSource, /aria-label="Agent Model"/);
  assert.match(railSource, /aria-label="Reasoning Effort"/);
  assert.match(railSource, /continues on the server/);
  assert.match(railSource, /AgentMessageContent/);
  assert.match(railSource, /Continue in Telegram/);
  assert.match(railSource, /archiveAgentConversation/);
  assert.match(railSource, /Resize Agent Panel/);
  assert.match(railSource, /Expand Agent research mode/);
  assert.match(railSource, /role={overlayViewport \? "dialog" : "complementary"}/);
  assert.match(railSource, /event\.key !== "Tab"/);
  assert.match(railSource, /AgentMessageCard/);
  assert.match(railSource, /Editing an earlier prompt/);
  assert.match(railSource, /void sendMessage\(candidate\.content\)/);
  assert.match(railSource, /Retry Turn/);
  assert.match(railSource, /Agent Preferences/);
  assert.match(railSource, /PRESENTATION ONLY/);
  assert.match(railSource, /Web Search Background/);
  assert.match(railSource, /ON BY DEFAULT/);
  assert.match(railSource, /Conversation Usage/);
  assert.match(railSource, /Runtime Components/);
  assert.match(railSource, /NOT INSTALLED/);
  assert.match(messageContentSource, /AgentMessageContent/);
  const messageCardSource = await readFile(new URL("../app/components/agent-message-card.tsx", import.meta.url), "utf8");
  assert.match(messageCardSource, /Evidence &amp; Tools/);
  assert.match(messageCardSource, /Copy Message/);
  assert.match(messageCardSource, /Edit This Prompt and Resend/);
  assert.match(messageCardSource, /Retry the Prompt for This Response/);
  assert.match(messageCardSource, /AgentArtifactPreview/);
  assert.match(messageCardSource, /authenticatedFetch/);
  assert.doesNotMatch(messageCardSource, /dangerouslySetInnerHTML/);
  assert.doesNotMatch(messageContentSource, /dangerouslySetInnerHTML/);
  assert.match(messageContentSource, /agent-message-table-wrap/);
  assert.match(messageContentSource, /agent-entity-link/);
  assert.match(pageContextSource, /navigation-only context/);
  assert.match(workspace, /Continue in Telegram/);
  assert.match(workspace, /One-Time Code/);
  assert.match(workspace, /\/continue/);
  assert.match(workspace, /nativeEvent\.isComposing/);
  assert.match(workspace, /Stop Waiting/);
  assert.doesNotMatch(workspace, /dangerouslySetInnerHTML/);
  assert.match(apiSource, /messages\/stream/);
  assert.match(apiSource, /turns.*cancel/s);
  assert.match(apiSource, /turns.*stream/s);
  assert.match(apiSource, /turns.*retry/s);
  assert.match(apiSource, /\/api\/agent\/preferences/);
  assert.match(apiSource, /\/metrics/);
  assert.match(apiSource, /providers.*models/s);
  assert.match(apiSource, /body\.model = model/);
  assert.match(apiSource, /external_message_ref/);
  assert.match(apiSource, /ephemeral_context/);
  assert.match(apiSource, /route_hash/);
  assert.match(apiSource, /selected_subject_id/);
  assert.match(apiSource, /pending-actions/);
  assert.match(apiSource, /pending-actions.*reissue/s);
  assert.match(apiSource, /window\.location\.hash/);
  assert.match(apiSource, /function routeHash/);
  assert.doesNotMatch(apiSource, /Navigation context \(untrusted\)/);
  assert.match(apiSource, /confirmation_token/);
  assert.match(apiSource, /message_id: idFor\(source, "message_id"\)/);
  assert.match(workspace, /Confirm Exact Action/);
  assert.doesNotMatch(workspace, /localStorage.*confirmation/i);
});

test("keeps the Agent rail width and focus mode accessible and durable", async () => {
  const layoutSource = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");
  const railSource = await readFile(new URL("../app/components/agent-rail.tsx", import.meta.url), "utf8");
  const railConstants = await readFile(new URL("../app/lib/agent-rail-layout.mjs", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(layoutSource, /trading-partner-agent-rail-width/);
  // The boot script and the rail must clamp to the SAME shared bounds.
  assert.match(railConstants, /AGENT_RAIL_MIN_WIDTH = 320/);
  assert.match(railConstants, /AGENT_RAIL_MAX_WIDTH = 840/);
  assert.match(railConstants, /AGENT_RAIL_MAX_VIEWPORT_RATIO = 0\.68/);
  assert.match(layoutSource, /AGENT_RAIL_MIN_WIDTH/);
  assert.match(layoutSource, /AGENT_RAIL_MAX_WIDTH/);
  assert.match(railSource, /from "\.\.\/lib\/agent-rail-layout\.mjs"/);
  assert.match(railSource, /Math\.min\(AGENT_RAIL_MAX_WIDTH, Math\.floor\(window\.innerWidth \* AGENT_RAIL_MAX_VIEWPORT_RATIO\)\)/);
  assert.match(railSource, /aria-orientation="vertical"/);
  assert.match(railSource, /ArrowLeft/);
  assert.match(railSource, /ArrowRight/);
  assert.match(railSource, /agent-focus-mode/);
  assert.match(styles, /--agent-rail-user-width/);
  assert.match(styles, /agent-focus-mode[\s\S]*60vw/);
  assert.match(styles, /agent-rail-resizing/);
  assert.match(styles, /@media \(hover:none\)[\s\S]*agent-message-actions/);
});

test("registers typed navigation context on specialist pages without copying page facts", async () => {
  for (const [page, surface] of [
    ["research/page.tsx", "research"],
    ["monitors/page.tsx", "monitors"],
    ["portfolio/page.tsx", "portfolio"],
    ["decision-workbench/page.tsx", "decision-workbench"],
  ]) {
    const source = await readFile(new URL(`../app/${page}`, import.meta.url), "utf8");
    assert.match(source, /useAgentPageContext/);
    assert.match(source, new RegExp(`surface: ["']${surface}["']`));
  }
});
