import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render(route = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request(`http://localhost${route}`, { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the local control room", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /Trading Partner/);
  assert.match(html, /投资研究控制台/);
  assert.match(html, /Loopback only/);
  assert.match(html, /data-theme="light"/);
  assert.match(html, /界面主题/);
  assert.match(html, /浅色/);
  assert.match(html, /深色/);
  assert.doesNotMatch(html, /Your site is taking shape|react-loading-skeleton/);
});

test("renders all primary local-console routes", async () => {
  for (const [route, heading] of [
    ["/monitors", "Monitor 运行与事件"],
    ["/capabilities", "全部 MCP 能力"],
    ["/portfolio", "账户"],
    ["/operations", "操作中心"],
  ]) {
    const response = await render(route);
    assert.equal(response.status, 200);
    assert.match(await response.text(), new RegExp(heading));
  }
});

test("overview Monitor titles deep-link to async-loaded definition cards", async () => {
  const overviewSource = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const monitorsSource = await readFile(new URL("../app/monitors/page.tsx", import.meta.url), "utf8");
  const editorSource = await readFile(new URL("../app/monitors/monitor-editor.tsx", import.meta.url), "utf8");

  assert.match(overviewSource, /href=\{`\/monitors#\$\{monitorAnchorId\(monitor\.monitor_id\)\}`\}/);
  assert.match(monitorsSource, /id=\{monitorAnchorId\(monitor\.monitor_id\)\}/);
  assert.match(monitorsSource, /window\.scrollTo/);
  assert.doesNotMatch(monitorsSource, /scrollIntoView/);
  assert.match(monitorsSource, /monitor_created_at/);
  assert.match(monitorsSource, /monitor_updated_at/);
  assert.match(monitorsSource, /最近编辑/);
  assert.match(monitorsSource, /rule\.description/);
  assert.match(monitorsSource, /monitorPriceObservation/);
  assert.match(monitorsSource, /monitor-price-observation/);
  assert.match(monitorsSource, /最近运行价格/);
  assert.match(monitorsSource, /showIndividualObservation/);
  assert.match(monitorsSource, /monitorMatchesInstrument/);
  assert.match(monitorsSource, /按标的代码筛选 Monitor/);
  assert.match(monitorsSource, /monitor-list-panel/);
  assert.match(editorSource, /条件缺少具体释义/);
});

test("portfolio keeps Watchlist controls out of the account workspace", async () => {
  const portfolioSource = await readFile(new URL("../app/portfolio/page.tsx", import.meta.url), "utf8");

  assert.doesNotMatch(portfolioSource, /WATCHLIST|watchlistResult|同步自选|自选标的/);
  assert.match(portfolioSource, /SortableHeader/);
  assert.match(portfolioSource, /position\.market_price_at/);
  assert.match(portfolioSource, /未提供带时间价格/);
});

test("operations provides a guarded foreground Schwab OAuth flow", async () => {
  const operationsSource = await readFile(new URL("../app/operations/page.tsx", import.meta.url), "utf8");

  assert.match(operationsSource, /\/api\/schwab\/oauth/);
  assert.match(operationsSource, /schwab_oauth_renew/);
  assert.match(operationsSource, /schwab_oauth_renew_confirmed/);
  assert.match(operationsSource, /等待授权回调/);
  assert.match(operationsSource, /请只操作这次新打开的标签页/);
});
