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

test("uses the sidebar project logo as the browser tab icon", async () => {
  const layoutSource = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");
  assert.match(layoutSource, /icons:/);
  assert.equal(
    (layoutSource.match(/\/assets\/trading-partner-brand\/logo\.png/g) ?? []).length,
    2,
  );
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
  assert.match(shellSource, /agentRequested/);
  assert.match(shellSource, /nextSidebarCollapsed/);
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

test("keeps the primary navigation focused on the Journal workflow", async () => {
  const shellSource = await readFile(new URL("../app/components/console-shell.tsx", import.meta.url), "utf8");
  assert.match(shellSource, /CONSOLE_PAGE_LABELS/);
  assert.match(shellSource, /"decision-workbench": "Journal"/);
  assert.match(shellSource, /const label = CONSOLE_PAGE_LABELS\[item\.key\]/);
  assert.match(shellSource, /<h1>\{CONSOLE_PAGE_LABELS\[active\]\}<\/h1>/);
  assert.ok(shellSource.indexOf('href: "/decision-workbench"') < shellSource.indexOf('href: "/research"'));
  assert.ok(shellSource.indexOf('href: "/research"') < shellSource.indexOf('href: "/monitors"'));
  assert.doesNotMatch(shellSource, /href: "\/(?:agenda|scorecards|retro)"/);
});

test("automatically authenticates Console writes with a restart-safe session token", async () => {
  const apiSource = await readFile(new URL("../app/lib/api.ts", import.meta.url), "utf8");
  assert.match(apiSource, /\/api\/session/);
  assert.match(apiSource, /X-Trading-Partner-Console-Token/);
  assert.match(apiSource, /response\.status === 403/);
});

test("boundedly reconnects read pages while the local API restarts", async () => {
  const apiSource = await readFile(new URL("../app/lib/api.ts", import.meta.url), "utf8");
  assert.match(apiSource, /READ_RETRY_DELAYS_MS = \[0, 1_000, 3_000, 7_000, 8_000\]/);
  assert.match(apiSource, /TRANSIENT_READ_STATUSES = new Set\(\[502, 503, 504\]\)/);
  assert.match(apiSource, /NonRetryableReadError/);
  assert.match(apiSource, /readJsonWithRetry<T>\(route, controller\.signal\)/);
});

test("keeps the data API on loopback while LAN mode uses authenticated same-origin access", async () => {
  const apiSource = await readFile(new URL("../app/lib/api.ts", import.meta.url), "utf8");
  const authSource = await readFile(new URL("../app/lib/lan-auth.ts", import.meta.url), "utf8");
  const proxySource = await readFile(new URL("../app/api/console/[...path]/route.ts", import.meta.url), "utf8");
  const loginRouteSource = await readFile(new URL("../app/api/lan-auth/route.ts", import.meta.url), "utf8");
  const startSource = await readFile(new URL("../scripts/start-lan.mjs", import.meta.url), "utf8");

  assert.match(apiSource, /"\/api\/console"/);
  assert.match(apiSource, /Console API routes must be same-origin relative paths/);
  assert.match(proxySource, /http:\/\/127\.0\.0\.1:8765/);
  assert.match(proxySource, /isLoopbackRequest/);
  assert.match(proxySource, /x-trading-partner-console-token/);
  assert.match(proxySource, /path\[0\] === "api"/);
  assert.match(proxySource, /`api\/\$\{path\.join\("\/"\)\}`/);
  assert.doesNotMatch(proxySource, /request\.headers\.get\(["'](?:origin|cookie|x-forwarded)/i);
  assert.match(authSource, /LAN_PASSWORD_MIN_LENGTH = 16/);
  assert.match(authSource, /HMAC/);
  assert.match(loginRouteSource, /isSameOrigin/);
  assert.match(loginRouteSource, /failureBuckets/);
  assert.match(startSource, /"--hostname", "0\.0\.0\.0"/);
  assert.match(startSource, /TRADING_PARTNER_CONSOLE_LAN_PASSWORD_FILE/);
  assert.match(startSource, /metadata\.mode & 0o077/);
  assert.match(startSource, /TRADING_PARTNER_CONSOLE_LAN_PASSWORD: password/);
  assert.doesNotMatch(startSource, /NEXT_PUBLIC_TRADING_PARTNER_CONSOLE_LAN_PASSWORD/);
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

test("specialist pages share compact Header actions while Overview remains independent", async () => {
  for (const path of [
    "research/page.tsx",
    "portfolio/page.tsx",
    "decision-workbench/page.tsx",
    "monitors/page.tsx",
    "agenda/page.tsx",
    "scorecards/page.tsx",
    "operations/page.tsx",
    "capabilities/page.tsx",
  ]) {
    const source = await readFile(new URL(`../app/${path}`, import.meta.url), "utf8");
    assert.match(source, /PageActionMenu/, path);
  }
  const overviewSource = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(overviewSource, /PageActionMenu/);
  const chatSource = await readFile(new URL("../app/chat/page.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(chatSource, /PageActionMenu/);
  assert.match(chatSource, /redirect\("\/\?agent=open"\)/);
});

test("journal reuses durable workflow stages without replacing specialist pages", async () => {
  const source = await readFile(new URL("../app/decision-workbench/page.tsx", import.meta.url), "utf8");
  const observationSource = await readFile(new URL("../app/decision-workbench/observation-inbox.tsx", import.meta.url), "utf8");
  const scenarioSource = await readFile(new URL("../app/decision-workbench/scenario-digest.tsx", import.meta.url), "utf8");
  const autosuggestSource = await readFile(new URL("../app/components/multi-select-autosuggest.tsx", import.meta.url), "utf8");
  const cycleAdjustmentSource = await readFile(new URL("../app/decision-workbench/cycle-adjustment-editor.tsx", import.meta.url), "utf8");
  const journalStyles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(source, /\/api\/decision-workbench/);
  assert.match(source, /useApi<JournalWorkbenchResponse>/);
  assert.match(source, /useApi<ObservationInboxResponse>/);
  assert.match(source, /enabled: journalTab === "notes"/);
  assert.doesNotMatch(source, /useApi<Dict>\("\/api\/(research|monitors|agenda|retro|scorecards)/);
  assert.match(source, /partial_failures/);
  assert.match(source, /postApi/);
  assert.match(source, /"Acknowledge"/);
  assert.match(source, />Resolve</);
  assert.match(source, /INCOMPLETE/);
  assert.match(source, /This does not submit or authorize an order/);
  assert.match(source, /Record Decision/);
  assert.match(source, /ScenarioDigest/);
  assert.match(scenarioSource, /UPSIDE\|SIDEWAYS\|PULLBACK\|INVALIDATION/);
  assert.match(scenarioSource, /View Full Thesis/);
  assert.match(source, /ObservationInbox/);
  assert.match(observationSource, /title="Latest Thinking"/);
  assert.match(observationSource, /Refresh Sources/);
  assert.match(observationSource, /Note Scope/);
  assert.match(observationSource, /All Notes/);
  assert.match(observationSource, /Open Research/);
  assert.match(observationSource, /create: "observation"/);
  assert.match(source, /compactNextSteps/);
  assert.match(source, /\/api\/observations\/sync/);
  assert.match(observationSource, /Review as Decision/);
  assert.match(observationSource, /Revision History/);
  assert.match(observationSource, /attributionSections/);
  assert.match(observationSource, /notes-attribution-sections/);
  assert.match(observationSource, />Date</);
  assert.match(observationSource, /added_lines/);
  assert.match(observationSource, /removed_lines/);
  assert.match(observationSource, /aria-live="polite"/);
  assert.match(source, /Source Note Revision/);
  assert.match(source, /external_note_revision_id: decisionSourceRevisionId/);
  assert.match(observationSource, /unprefixed\s+text\s+is\s+your\s+view/i);
  assert.match(source, /research_memory_append/);
  assert.match(source, /confirmation: "research_memory_append"/);
  assert.match(source, /\["watch", "no_action", "research_more"\]\.includes\(decisionAction\)/);
  assert.match(source, /strategy_code: "strategy_v1"/);
  assert.match(source, /scenario: decisionScenario/);
  assert.match(source, /trade_plan_id: planLinkReady \? planId : null/);
  assert.match(source, /trade_plan_version: planLinkReady \? planVersion : null/);
  assert.match(source, /review_due_at: reviewDueAt/);
  assert.match(source, /futureDateInput\(7\)/);
  assert.match(source, /Invalidation cannot initiate, add, or hold under strategy_v1/);
  assert.match(source, /Initiate or Add requires an exact current Trade Plan/);
  assert.match(source, /query\.get\("capture"\) === "decision"/);
  assert.match(source, /query\.get\("supersedes_decision_id"\)/);
  assert.match(source, /supersedes_decision_id: supersedesDecisionId/);
  assert.match(source, /workbenchApi\.data\?\.timeline/);
  assert.match(source, /workbenchApi\.data\?\.trade_cycles/);
  assert.match(source, /Latest Trade Cycle/);
  assert.match(source, /JOURNAL_TABS/);
  assert.match(source, /Journal Sections/);
  assert.match(source, /journal-panel-timeline/);
  assert.match(source, /journal-panel-cycles/);
  assert.match(source, /Gross P\/L/);
  assert.match(source, /Fees unavailable/);
  assert.match(source, /journal-panel-behavior/);
  assert.match(source, /journal-panel-notes/);
  assert.doesNotMatch(source, /Unlinked Activity/);
  assert.doesNotMatch(source, /Save Classification/);
  assert.match(source, /BehaviorPanel/);
  assert.match(source, /Custom Range/);
  assert.match(source, /behavior_start=/);
  assert.match(source, /behavior_end=/);
  assert.match(source, /label><span>Start Date/);
  assert.match(source, /label><span>End Date/);
  assert.match(source, /Other Metrics & Audit Details/);
  assert.match(source, /payload value does not match/);
  assert.match(source, /metricInteger\(metric\.numerator\)/);
  assert.match(source, /Payoff ratio/i);
  assert.doesNotMatch(source, /behaviorPercent/);
  assert.match(source, /CycleAdjustmentEditor/);
  assert.match(source, /title="Traded Instruments"/);
  assert.match(source, /label="Instrument Filter"/);
  assert.match(source, /SortableTableHeader/);
  assert.match(source, /INSTRUMENT_TABLE_PAGE_SIZE/);
  assert.doesNotMatch(source, /title="Contributors"/);
  assert.match(cycleAdjustmentSource, /title="Cycle Adjustments"/);
  assert.match(cycleAdjustmentSource, /Partition Every Activity/);
  assert.match(cycleAdjustmentSource, /Preview Impact/);
  assert.match(cycleAdjustmentSource, /Apply Revision/);
  assert.match(source, /Create Weekly Review/);
  assert.match(source, /NEW, PERSISTENT, RESOLVED, and RECURRED/);
  assert.ok(source.indexOf("Data Confidence") < source.indexOf("Results"));
  assert.ok(source.indexOf("Results") < source.indexOf("Holding Patterns"));
  assert.ok(source.indexOf("Holding Patterns") < source.indexOf("Latest Changes"));
  assert.ok(source.indexOf("Latest Changes") < source.indexOf("Needs Review"));
  assert.match(source, /journal-panel-reviews/);
  assert.match(source, /journalTimelineRows/);
  assert.match(source, /<Paginator step=\{cyclePageSize\}/);
  assert.match(source, /cycleLatestActivityTime/);
  assert.match(source, /useState<CycleSortMode>\("LATEST_DESC"\)/);
  assert.match(source, /label><span>Sort Cycles/);
  assert.match(source, /Latest Activity · Newest First/);
  assert.match(source, /CYCLE_STATUS_OPTIONS/);
  assert.match(source, /label="Status" placeholder="All Statuses"/);
  assert.match(source, /setCycleStatusFilters/);
  assert.match(source, /Recent Decisions/);
  assert.match(source, /1 · DECIDE/);
  assert.match(source, /2 · OBSERVE/);
  assert.match(source, /3 · EXECUTE/);
  assert.match(source, /4 · REVIEW/);
  assert.doesNotMatch(source, /title="Catalyst Agenda"/);
  assert.doesNotMatch(source, /title="Judgment Scorecard"/);
  assert.match(source, /research#subject-/);
  assert.match(source, /href="\/monitors"/);
  assert.doesNotMatch(source, /href="\/retro"/);
  assert.match(source, /All Research Subjects/);
  assert.match(source, /All History/);
  assert.match(source, /MultiSelectAutosuggest/);
  assert.match(source, /accountFilters/);
  assert.match(source, /instrumentFilters/);
  assert.match(source, /subjectFilters/);
  assert.match(source, /classificationFilters/);
  assert.match(source, /cyclePageSizeForViewport/);
  assert.match(source, /window\.addEventListener\("resize", updateCyclePageSize\)/);
  assert.match(source, /journal-cycle-browser rows-\$\{cyclePageSize\}/);
  assert.match(autosuggestSource, /role="combobox"/);
  assert.match(autosuggestSource, /role="listbox"/);
  assert.match(autosuggestSource, /Typed text is not applied until a suggestion is selected/);
  assert.match(autosuggestSource, /onClick=\{\(\) => select\(option\)\}/);
  assert.match(journalStyles, /--journal-filter-control-height:48px/);
  assert.match(journalStyles, /journal-filter-bar > label > select[^}]*height:var\(--journal-filter-control-height\)/);
  assert.match(journalStyles, /multi-autosuggest-control[^}]*min-height:var\(--journal-filter-control-height,48px\)/);
  assert.match(journalStyles, /journal-more-filters > summary[^}]*height:var\(--journal-filter-control-height\)/);
  assert.match(source, /cycleStatusTone/);
  assert.match(source, /cycleQualityTone/);
  assert.match(source, /cycleClassificationTone/);
  assert.match(source, /Data Quality/);
  assert.match(source, /<QuickLink href="\/portfolio#activity">Open Portfolio<\/QuickLink>/);
  assert.match(source, /Open Portfolio/);
  assert.match(source, /Trade Cycle Status and Quality Guide/);
});

test("agenda route uses the durable Catalyst Agenda write contract", async () => {
  const response = await render("/agenda");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Catalyst Agenda/);

  const shellSource = await readFile(new URL("../app/components/console-shell.tsx", import.meta.url), "utf8");
  const source = await readFile(new URL("../app/agenda/page.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(shellSource, /href: "\/agenda"/);
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
  assert.match(source, /agenda-sync-disclosure/);
  assert.match(source, /Configure Provider Calendar Sync/);
  assert.match(source, /optionLabel\(item\)/);
});

test("specialist history and capability directories keep repeat detail out of the main scan path", async () => {
  const retroSource = await readFile(new URL("../app/retro/page.tsx", import.meta.url), "utf8");
  const scorecardSource = await readFile(new URL("../app/scorecards/page.tsx", import.meta.url), "utf8");
  const capabilitySource = await readFile(new URL("../app/capabilities/page.tsx", import.meta.url), "utf8");

  assert.match(retroSource, /redirect\("\/decision-workbench#reviews"\)/);
  assert.match(scorecardSource, /scorecardTotal > 0 && <Paginator/);
  assert.match(scorecardSource, /Select a Research Subject to generate a scorecard/);
  assert.match(capabilitySource, /expandedGroups/);
  assert.match(capabilitySource, /className="capability-group"/);
  assert.doesNotMatch(capabilitySource, /Open →/);
});

test("content disclosures and cross-page shortcuts use shared Console primitives", async () => {
  const appRoot = new URL("../app/", import.meta.url);
  const entries = await readdir(appRoot, { recursive: true });
  const sourceFiles = entries.filter((entry) => entry.endsWith(".tsx"));
  for (const entry of sourceFiles) {
    const source = await readFile(new URL(entry, appRoot), "utf8");
    const nativeDetails = source.match(/<details\b/g) ?? [];
    const nativeSummaries = source.match(/<summary\b/g) ?? [];
    if (entry === "components/ui.tsx") {
      assert.equal(nativeDetails.length, 1, "Disclosure owns the native details element");
      assert.equal(nativeSummaries.length, 1, "Disclosure owns the native summary element");
    } else if (entry === "decision-workbench/page.tsx") {
      assert.equal(nativeDetails.length, 1, "Journal keeps only the dedicated More Filters popover");
      assert.equal(nativeSummaries.length, 1, "Journal keeps only the dedicated More Filters trigger");
      assert.match(source, /className="journal-more-filters"/);
    } else {
      assert.equal(nativeDetails.length, 0, `${entry} must use the shared Disclosure component`);
      assert.equal(nativeSummaries.length, 0, `${entry} must use the shared Disclosure component`);
    }
  }
  const uiSource = await readFile(new URL("../app/components/ui.tsx", import.meta.url), "utf8");
  assert.match(uiSource, /export function Disclosure/);
  assert.match(uiSource, /export function QuickLink/);
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
  assert.match(source, /exposurePositionSummaries/);
  assert.match(source, /sortedExposureItems/);
  assert.match(source, /DEFAULT_POSITION_SORT: PositionSort = \{ key: "market_value", direction: "desc" \}/);
  assert.match(source, /portfolio-exposure-position/);
  assert.match(source, /<dt>Quantity<\/dt>/);
  assert.match(source, /<dt>Cost<\/dt>/);
  assert.ok((source.match(/preserve_full_result: true/g) ?? []).length >= 2);
  assert.match(source, /Portfolio Total Value/);
  assert.match(source, /exposureExpanded/);
  assert.match(source, /Show Top Exposures/);
  assert.match(source, /Show All \$\{exposures\.length\} Exposures/);
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

  assert.doesNotMatch(shellSource, /href: "\/scorecards"/);
  assert.match(shellSource, /Scorecards/);
  assert.match(source, /\/api\/scorecards/);
  assert.match(source, /window\.location\.search/);
  assert.match(source, /research_workflow_run/);
  assert.match(source, /operation:\s*["']judgment_scorecard["']/);
  assert.match(source, /case_id:\s*subjectId/);
  assert.match(source, /thesis_id:\s*thesisId/);
  assert.doesNotMatch(source, /window\.confirm/);
  assert.match(source, /TARGET_DIMENSION_OUTCOMES = \["NOT_EVALUATED", "EVALUATED", "PARTIAL", "PASS", "FAIL"\]/);
  assert.match(source, /function optionLabel/);
  assert.match(source, /scorecardTotal > 0 && <Paginator/);
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
  assert.match(source, /decision-workbench\?subject_id=.*capture=decision/);
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
  assert.match(source, /PageActionMenu/);
  assert.match(source, /pageActions=\{<PageActionMenu/);
  assert.match(source, /ariaLabel="Research Page Actions"/);
  assert.match(styles, /page-action-menu/);
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
  assert.match(source, /searchParams\.get\("create"\) !== "observation"/);
  assert.match(source, /observation_source/);
  assert.match(source, /optionLabel/);
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
  assert.match(source, /instrument_resolve[^\n]*preserve_full_result: true/);
  assert.ok((continuitySource.match(/preserve_full_result: true/g) ?? []).length >= 3);
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
  assert.match(source, /CandidateReviewDetails/);
  assert.match(source, /Complete Proposal Payload/);
  assert.match(source, /Why This Change Was Proposed/);
  assert.match(source, /Candidate ID/);
  assert.match(source, /Confirmation Mode/);
  assert.match(source, /candidate\._truncated !== true/);
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
  assert.match(source, /onDecision=\{decideCandidate\}/);
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
  assert.match(uiSource, /export function Disclosure/);
  assert.match(uiSource, /export function QuickLink/);
  assert.match(uiSource, /disclosure-meta/);
  assert.match(uiSource, /role="tablist"/);
  assert.match(uiSource, /event\.key === "Home"/);
  assert.match(uiSource, /event\.key === "End"/);
  assert.match(styles, /\.card-head[^}]*border-bottom:1px solid var\(--line\)/);
  assert.match(styles, /\.card-subtitle/);
  assert.match(styles, /\.description-list > div[^}]*border:1px solid var\(--line\)[^}]*background:var\(--panel\)/);
  assert.match(styles, /\.badge \{[^}]*border:1px solid currentColor;[^}]*padding:3px 7px;[^}]*background:var\(--panel-solid\)/);
  assert.match(styles, /\.badge::before/);
  assert.match(overviewSource, /kicker="EVENT COVERAGE" title="Catalyst Pulse" subtitle="Upcoming schedule and unresolved timing gaps"/);
  assert.match(overviewSource, /kicker="DECISION WORKFLOW" title="Action & Review Inbox" subtitle="Grouped manual actions and durable closure metrics"/);
  assert.match(overviewSource, /Review Queue/);
  assert.match(overviewSource, /monitor-state-summary/);
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
  assert.match(monitorsSource, /dashboardEnvelope\._truncated === true/);
  assert.match(monitorsSource, /item\._truncated !== true/);
  assert.match(editorSource, /instrument_resolve/);
  assert.match(editorSource, /preserve_full_result: true/);
  assert.match(monitorsSource, /<EntityBrowser/);
  assert.match(monitorsSource, /entity-filter-notice/);
  assert.match(monitorsSource, /HorizontalTabs/);
  assert.match(monitorsSource, /Monitor modules/);
  assert.match(monitorsSource, /monitor-panel-overview/);
  assert.match(monitorsSource, /monitor-panel-rules/);
  assert.match(monitorsSource, /monitor-panel-runs/);
  assert.match(monitorsSource, /monitor-panel-events/);
  assert.match(monitorsSource, /CompositeJudgmentCard/);
  assert.match(monitorsSource, /selectedAttentionRules/);
  assert.match(monitorsSource, /monitor-attention-list/);
  assert.match(monitorsSource, /All deterministic rules are quiet/);
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
  const uiSource = await readFile(new URL("../app/components/ui.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(portfolioSource, /\/api\/portfolio\?transaction_limit=500&coverage_limit=100/);
  assert.match(portfolioSource, /Holdings/);
  assert.match(portfolioSource, /Activity/);
  assert.match(portfolioSource, /Trade Cycles/);
  assert.match(portfolioSource, /cyclePnlDisplay/);
  assert.match(portfolioSource, /cycleStatusTone/);
  assert.match(portfolioSource, /cycleClassificationTone/);
  assert.match(portfolioSource, /Gross P\/L/);
  assert.match(portfolioSource, /Fees unavailable/);
  assert.match(portfolioSource, /tradeCyclesEnvelope/);
  assert.match(portfolioSource, /cyclePageSize = 6/);
  assert.match(portfolioSource, /<Paginator step=\{cyclePageSize\}/);
  assert.match(portfolioSource, /Performance/);
  assert.match(portfolioSource, /Return Series/);
  assert.match(portfolioSource, /Time-Weighted Return/);
  assert.match(portfolioSource, /Money-Weighted Return/);
  assert.match(portfolioSource, /Income Return/);
  assert.match(portfolioSource, /Fee Drag/);
  assert.match(portfolioSource, /Closed Cycle Returns/);
  assert.match(portfolioSource, /Risk/);
  assert.match(portfolioSource, /external_state_sync/);
  assert.doesNotMatch(portfolioSource, /watchlist_manage/);
  assert.doesNotMatch(portfolioSource, /Sync Watchlist/);
  assert.match(portfolioSource, /risk_policy_update/);
  assert.match(portfolioSource, /portfolio_risk_get/);
  assert.match(portfolioSource, /window\.location\.hash/);
  assert.match(portfolioSource, /SortableTableHeader/);
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
  assert.match(uiSource, /ChevronsUpDown/);
  assert.match(uiSource, /className=\{`sort-indicator\$\{active \? " active" : ""\}`\}/);
  assert.match(styles, /\.portfolio-desktop-table \.sort-header[^}]*min-height:44px/);
  assert.match(styles, /\.sort-indicator \{[^}]*width:20px;[^}]*height:24px/);
  assert.match(styles, /\.trade-cycle-list/);
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

test("keeps small metadata contrast above the normal-text threshold", async () => {
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(styles, /:root[\s\S]*--dim: #627067;/);
  assert.match(styles, /html\[data-theme="dark"\][\s\S]*--dim: #8d9992;/);
  const luminance = (hex) => {
    const channels = [1, 3, 5].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16) / 255)
      .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  };
  const contrast = (foreground, background) => {
    const values = [luminance(foreground), luminance(background)].sort((left, right) => right - left);
    return (values[0] + 0.05) / (values[1] + 0.05);
  };
  assert.ok(contrast("#627067", "#f3f6f2") >= 4.5);
  assert.ok(contrast("#8d9992", "#141b18") >= 4.5);
});

test("legacy Chat route redirects to the shared Agent Rail", async () => {
  const response = await render("/chat");
  assert.equal(response.status, 200);
  assert.match(response.url, /\/?\?agent=open$/);
  const html = await response.text();
  assert.match(html, /Overview/);

  const chatPageSource = await readFile(new URL("../app/chat/page.tsx", import.meta.url), "utf8");
  const railSource = await readFile(new URL("../app/components/agent-rail.tsx", import.meta.url), "utf8");
  const conversationSource = await readFile(new URL("../app/lib/use-agent-conversation.ts", import.meta.url), "utf8");
  const streamSource = await readFile(new URL("../app/lib/agent-stream.ts", import.meta.url), "utf8");
  const messageContentSource = await readFile(new URL("../app/components/agent-message-content.tsx", import.meta.url), "utf8");
  const pageContextSource = await readFile(new URL("../app/lib/agent-page-context.ts", import.meta.url), "utf8");
  const apiSource = await readFile(new URL("../app/lib/agent-api.ts", import.meta.url), "utf8");
  const shellSource = await readFile(new URL("../app/components/console-shell.tsx", import.meta.url), "utf8");
  assert.match(chatPageSource, /redirect\("\/\?agent=open"\)/);
  assert.match(shellSource, /agentRequested/);
  assert.doesNotMatch(shellSource, /label: "Chat"/);
  assert.match(shellSource, /AgentRail/);
  assert.match(railSource, /Agent Rail/);
  assert.match(shellSource, /trading-partner-agent-rail-collapsed/);
  assert.match(railSource, /collectEphemeralContext/);
  assert.match(railSource, /nativeEvent\.isComposing/);
  assert.match(railSource, /Cancel Current Agent Turn/);
  assert.match(railSource, /useAgentConversation/);
  assert.match(conversationSource, /reconnectAgentTurnStream/);
  assert.match(streamSource, /reduceAgentStream/);
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
  assert.match(railSource, /Agent Provider Error Notification/);
  assert.match(railSource, /Dismiss Provider Error Notification/);
  assert.match(railSource, /trading-partner-agent-dismissed-failures/);
  assert.match(railSource, /HTTP Status/);
  assert.match(railSource, /Retryable/);
  assert.match(streamSource, /parseAgentFailureNotice/);
  assert.match(railSource, /Agent Preferences/);
  assert.match(railSource, /PRESENTATION ONLY/);
  assert.match(railSource, /Web Search Background/);
  assert.match(railSource, /ON BY DEFAULT/);
  assert.match(railSource, /Conversation Usage/);
  assert.match(railSource, /Runtime Components/);
  assert.match(railSource, /NOT INSTALLED/);
  assert.match(messageContentSource, /AgentMessageContent/);
  const messageCardSource = await readFile(new URL("../app/components/agent-message-card.tsx", import.meta.url), "utf8");
  assert.match(messageCardSource, /Evidence & Tools/);
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
  assert.doesNotMatch(railSource, /localStorage.*confirmation/i);
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
