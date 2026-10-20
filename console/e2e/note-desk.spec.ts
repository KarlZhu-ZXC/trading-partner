import { expect, test, type Page } from "@playwright/test";
const revision = "external_note_revision_00000000-0000-7000-8000-000000000001";
const item = { note_id: "note_synthetic", note_revision_id: revision, version: 1, title: "My Moomoo thinking", instrument_id: "equity:US:SYNM", coverage: "FULL", observed_at: "2026-10-10T00:00:00Z", change: "NEW_USER_NOTE", requires_status_update: false };
const savedCoverage = {
  status: "AVAILABLE", has_more: false, warning_codes: [],
  receipts: [{ receipt_id: "synthetic_receipt", provider: "moomoo", account_ref: "synthetic-one",
    requested_start: "2026-09-01T00:00:00Z", requested_end: "2026-10-01T00:00:00Z",
    effective_start: "2026-09-15T00:00:00Z", effective_end: "2026-10-01T00:00:00Z",
    fetched_at: "2026-10-01T00:00:00Z", status: "INCOMPLETE",
    unavailable_kinds: ["dividend"], gap_codes: [] }],
};
async function mock(page: Page, discussed = false, partial = false, master = false) {
  const writes: Record<string, unknown>[] = [];
  const contentReads: string[] = [];
  const reads: string[] = [];
  const overviewScopes: string[] = [];
  await page.route("**/api/console/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname.replace("/api/console", "");
    if (req.method() === "GET") reads.push(path);
    if (req.method() === "POST") { writes.push(req.postDataJSON()); return route.fulfill({ json: { result: { ok: true, data: { status: partial ? "PARTIAL" : "SUCCEEDED", warning_codes: partial ? ["MOOMOO_NOTES_REMOTE_AUTH_REQUIRED"] : [] } } } }); }
    if (path === "/api/session") return route.fulfill({ json: { token: "synthetic-note-desk-session-token-only" } });
    if (path === "/api/note-desk") return route.fulfill({ json: { data: { items: [{ ...item, discussion: discussed ? { journal_id: "journal_synthetic" } : null }], coverage: "COMPLETE", has_more: false } } });
    if (path.startsWith("/api/note-desk/")) {
      const include = url.searchParams.get("include_content") === "true";
      if (include) contentReads.push(path);
      return route.fulfill({ json: { data: { ...item, blocks: [{ index: 0, speaker_kind: "USER", speaker_label: "USER", section_date: null }], content: include ? { block_index: 0, text_offset: 0, speaker_kind: "USER", text: "Synthetic private thought, not a formal judgment." } : null, portfolio_context: { status: "AVAILABLE", cursor: url.searchParams.get("context_cursor"), history_coverage: "UNVERIFIED", transaction_coverage: savedCoverage, positions_status: "AVAILABLE", transactions_status: "AVAILABLE", next_cursor: url.searchParams.has("context_cursor") ? null : "synthetic-next", items: url.searchParams.has("context_cursor") ? [{ kind: "TRANSACTION", transaction: { instrument_id: "equity:US:SYNM", provider: "schwab", account_ref: "synthetic-two", quantity: "4", price: "120", currency: "USD", side: "buy", occurred_at: "2026-10-01T00:00:00Z" } }] : [{ kind: "POSITION", provider: "moomoo", account_ref: "synthetic-one", fetched_at: "2026-10-01T00:00:00Z", position: { instrument_id: "option:US:SYNM261218C00120000", quantity: "1", currency: "USD", side: "long" }, option: { option_type: master ? null : "call", expiration_date: master ? null : "2026-12-18", strike: master ? null : "120", identity_basis: master ? "DURABLE_INSTRUMENT_MASTER" : "CANONICAL_SYMBOL_DERIVED_UNVERIFIED", multiplier: master ? "25" : null }, warning_codes: ["BROKER_COST_PNL_BASIS_MISMATCH"] }] }, discussion_history: [], discussion_coverage: "COMPLETE" } } });
    }
    if (path === "/api/overview") overviewScopes.push(url.searchParams.get("scope") ?? "full");
    if (path === "/api/overview") return route.fulfill({ json: { review_items: [], workflow_attention_projection: [{ source_ref: "order_synthetic", review_item_id: "review_pending", source_type: "BROKER_ORDER_INTENT", status: "OPEN", title: "Unresolved Synthetic Order", href: "/decision-workbench#timeline" }], partial_failures: [] } });
    return route.fulfill({ json: { items: [], enabled: false, configured: false } });
  });
  return { writes, contentReads, reads, overviewScopes };
}

test("notes support keyboard reading at narrow width without prerequisites or automatic private reads", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await mock(page);
  await page.goto("/");
  await expect(page.getByRole("tab", { name: "Your Notes", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("My Moomoo thinking", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Unresolved Synthetic Order", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Update Review Queue", exact: true })).not.toBeVisible();
  const open = page.getByRole("button", { name: "Open Note", exact: true });
  await open.focus();
  await page.keyboard.press("Enter");
  expect(state.contentReads).toEqual([]);
  expect(state.writes).toEqual([]);
  await page.getByRole("button", { name: "Read Private Note Text" }).click();
  await expect(page.getByText("Synthetic private thought, not a formal judgment.")).toBeVisible();
  expect(state.contentReads).toHaveLength(1);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  expect(state.writes).toEqual([]);
});

test("sync only requests notes and discussion receipts do not hide owner changes", async ({ page }) => {
  const state = await mock(page, true);
  await page.goto("/");
  await expect(page.getByText(/Discussion recorded/)).toBeVisible();
  await expect(page.getByText("My Moomoo thinking", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Sync Moomoo Notes" }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0]).toMatchObject({ tool_name: "external_state_sync", arguments: { request: { operation: "notes" } }, confirmation: "external_state_sync" });
  expect(state.contentReads).toEqual([]);
});


test("notes, holdings and trades work without opening a discussion, including reload", async ({ page }) => {
  const state = await mock(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Open Note", exact: true }).click();
  await expect(page.getByRole("button", { name: "Copy Codex Request" })).not.toBeVisible();
  await expect(page.getByRole("link", { name: "Review This Note in Journal" })).toHaveAttribute("href", `/decision-workbench?note_revision_id=${revision}#notes`);
  await page.getByRole("button", { name: "Read Private Note Text" }).click();
  await expect(page.getByText("Synthetic private thought, not a formal judgment.")).toBeVisible();
  await page.getByRole("button", { name: "More Trades and Holdings" }).click();
  await expect(page.getByText(/synthetic-two/)).toBeVisible();
  await page.getByRole("button", { name: "Reload Note Context" }).click();
  await expect(page.getByText(/synthetic-one/).first()).toBeVisible();
  const privateReads = state.contentReads.length;
  await page.reload();
  await page.getByRole("button", { name: "Open Note", exact: true }).click();
  await expect(page.getByRole("button", { name: "Read Private Note Text" })).toBeVisible();
  await page.getByRole("button", { name: "Reload Note Context" }).click();
  await expect(page.getByText(/synthetic-one/).first()).toBeVisible();
  expect(state.contentReads).toHaveLength(privateReads);
  expect(state.reads.filter(path => path.startsWith("/api/agent/"))).toEqual([]);
  expect(state.writes).toEqual([]);
});

test("unchanged notes remain available and discussion failure does not hide owner changes", async ({ page }) => {
  const state = await mock(page);
  await page.route("**/api/console/api/note-desk", route => route.fulfill({ json: { data: {
    items: [item, { ...item, note_revision_id: "revision_unchanged", title: "Unchanged synthetic note", change: "USER_TEXT_UNCHANGED" }],
    discussion_coverage: "UNAVAILABLE", has_more: false,
  } } }));
  await page.goto("/");
  await expect(page.getByText("My Moomoo thinking", { exact: true })).toBeVisible();
  await expect(page.getByText("Unchanged synthetic note", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Show All Notes on This Page" }).click();
  await expect(page.getByText("Unchanged synthetic note", { exact: true })).toBeVisible();
  await expect(page.getByText(/Available notes remain readable/)).toBeVisible();
  expect(state.writes).toEqual([]);
});


test("daily navigation is compact and Copilot only loads on request", async ({ page }) => {
  const state = await mock(page);
  await page.goto("/");
  await expect(page.getByText("My Moomoo thinking", { exact: true })).toBeVisible();
  const navigation = page.getByRole("navigation", { name: "Console Navigation", exact: true });
  await expect(navigation.getByRole("link")).toHaveCount(3);
  await expect(page.getByRole("navigation", { name: "Advanced Navigation" })).not.toBeVisible();
  expect(state.reads.filter(path => path.startsWith("/api/agent/"))).toEqual([]);
  await page.getByRole("button", { name: "Close navigation panel", exact: true }).click();
  const advanced = page.locator(".sidebar-advanced > summary");
  await expect(advanced).toBeVisible();
  await expect.poll(async () => {
    const bounds = await advanced.boundingBox();
    const sidebar = await page.locator(".sidebar").boundingBox();
    return Boolean(bounds && sidebar && bounds.x >= sidebar.x
      && bounds.x + bounds.width <= sidebar.x + sidebar.width);
  }).toBe(true);
  await advanced.focus();
  await advanced.press("Enter");
  await expect(page.getByRole("button", { name: "Close navigation panel", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Advanced Navigation" }).getByRole("link", { name: "Research", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Open Copilot Panel", exact: true }).click();
  await expect.poll(() => state.reads.filter(path => path.startsWith("/api/agent/")).length).toBeGreaterThan(0);
  expect(state.writes).toEqual([]);
});

test("partial sync exposes authentication failure rather than claiming fresh cloud notes", async ({ page }) => {
  await mock(page, false, true);
  await page.goto("/");
  await page.getByRole("button", { name: "Sync Moomoo Notes" }).click();
  await expect(page.getByRole("status")).toContainText("Sync incomplete");
  await expect(page.getByRole("status")).toContainText("Cloud changes are not confirmed");
  await expect(page.getByRole("status")).not.toContainText("Notes synced.");
});

test("note context includes paginated holdings and trades before private text", async ({ page }) => {
  const state = await mock(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Open Note", exact: true }).click();
  await expect(page.getByText(/multiplier and Greeks require verified/)).toBeVisible();
  await expect(page.getByText(/Broker cost and reported P.L do not reconcile/)).toBeVisible();
  await page.getByRole("button", { name: "More Trades and Holdings" }).click();
  await expect(page.getByText(/Recorded Transaction/)).toBeVisible();
  await expect(page.getByText(/synthetic-two/)).toBeVisible();
  expect(state.contentReads).toEqual([]);
  expect(state.writes).toEqual([]);
});


test("saved option relation shows its recorded multiplier without inventing missing terms", async ({ page }) => {
  const state = await mock(page, false, false, true);
  await page.goto("/");
  await page.getByRole("button", { name: "Open Note", exact: true }).click();
  await expect(page.getByText(/Saved instrument relation · recorded multiplier 25/)).toBeVisible();
  await expect(page.getByText(/Expiry unavailable/)).toBeVisible();
  await expect(page.getByText(/Contract terms and Greeks are not fully verified/)).toBeVisible();
  expect(state.writes).toEqual([]);
});

test(`saved receipt windows retain gaps and full-history uncertainty at 390px`, async ({ page }, testInfo) => {
  const width = 390;
  await page.setViewportSize({ width, height: 900 });
  const state = await mock(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Open Note", exact: true }).click();
  const summary = page.locator("summary").filter({ hasText: "Recorded Transaction Coverage" });
  await summary.focus();
  await summary.press("Enter");
  await expect(page.getByText("Saved source windows do not prove complete account history.")).toBeVisible();
  await expect(page.getByText(/Recorded window:.*INCOMPLETE/)).toBeVisible();
  await expect(page.getByText(/Requested window:/)).toBeVisible();
  await expect(page.getByText("Unavailable activity types: dividend")).toBeVisible();
  await expect(page.getByText(/History coverage: UNVERIFIED/)).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  expect(state.contentReads).toEqual([]);
  expect(state.writes).toEqual([]);
  if (process.env.TP_CAPTURE_NOTE_COVERAGE === "1") {
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
    await page.screenshot({ path: testInfo.outputPath(`source-windows-${width}.png`), fullPage: true, animations: "disabled" });
  }
});


test("note home loads only recovery context until background details are opened", async ({ page }) => {
  const state = await mock(page);
  await page.goto("/");
  await expect(page.getByRole("link", { name: "Unresolved Synthetic Order", exact: true })).toBeVisible();
  expect(state.overviewScopes).toEqual(["notes"]);
  await expect(page.getByRole("heading", { name: "Operations & Workflow", exact: true })).toHaveCount(0);
  await page.getByText("Background Workspace Details", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Update Review Queue", exact: true })).toBeVisible();
  expect(state.overviewScopes).toEqual(["notes", "full"]);
  await expect(page.getByRole("link", { name: "System Health and Maintenance", exact: true })).toHaveAttribute("href", "/operations");
  await expect(page.getByRole("link", { name: "Monitor Status and Runs", exact: true })).toHaveAttribute("href", "/monitors");
  await page.getByText("Background Workspace Details", { exact: true }).click();
  await expect.poll(() => state.overviewScopes.at(-1)).toBe("notes");
  await expect(page.getByRole("link", { name: "Unresolved Synthetic Order", exact: true })).toBeVisible();
  expect(state.writes).toEqual([]);
});

for (const failure of ["partial", "failed"]) {
  test(`notes remain readable with visible ${failure} recovery coverage`, async ({ page }) => {
    const state = await mock(page);
    await page.route("**/api/console/api/overview?scope=notes", route => route.fulfill(failure === "failed"
      ? { status: 500, json: { detail: "Synthetic recovery read failed" } }
      : { json: { review_coverage: { scope: "operations", complete: false }, monitor_dashboard: { ok: false } } }));
    await page.goto("/");
    await expect(page.getByText("My Moomoo thinking", { exact: true })).toBeVisible();
    await expect(page.getByText(failure === "failed" ? /Risk and operational coverage is unavailable/ : /Some risk or recovery sources are incomplete/)).toBeVisible();
    expect(state.writes).toEqual([]);
  });
}


for (const clipboardWorks of [true, false]) {
  test(`Codex handoff copies only an exact reference with clipboard ${clipboardWorks ? "available" : "unavailable"}`, async ({ page }) => {
    const state = await mock(page);
    await page.addInitScript(works => {
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
        writeText: async (value: string) => {
          if (!works) throw new Error("Synthetic clipboard unavailable");
          document.documentElement.dataset.copiedRequest = value;
        },
      } });
    }, clipboardWorks);
    await page.goto("/");
    await page.getByRole("button", { name: "Open Note", exact: true }).click();
    await page.getByText("Optional Discussion", { exact: true }).click();
    await page.getByRole("button", { name: "Copy Codex Request", exact: true }).click();
    const copied = clipboardWorks
      ? await page.locator("html").getAttribute("data-copied-request")
      : await page.getByRole("textbox", { name: "Codex Request", exact: true }).inputValue();
    expect(copied).toContain(revision);
    expect(copied).toContain("Record a conclusion only if I explicitly ask");
    expect(copied).not.toMatch(/My Moomoo thinking|synthetic-one|SYNM|Synthetic private thought/);
    expect(state.contentReads).toEqual([]);
    expect(state.writes).toEqual([]);
    expect(state.reads.some(path => path.startsWith("/api/agent/"))).toBe(false);
  });
}

const previousRevision = "external_note_revision_00000000-0000-7000-8000-000000000002";
async function savedDiscussions(page: Page) {
  const state = await mock(page);
  const discussionReads: { journal: string; offset: number }[] = [];
  const historyReads: string[] = [];
  let failRead = false;
  let wrongRevision = false;
  await page.route("**/api/console/api/note-desk/*", async route => {
    const params = new URL(route.request().url()).searchParams;
    if (params.get("include_discussions") === "true") {
      expect(params.get("include_portfolio")).toBe("false");
      historyReads.push(route.request().url());
    }
    const journal = params.get("journal_id");
    const offset = Number(params.get("text_offset") ?? 0);
    if (journal) {
      expect(params.get("include_content")).toBe("true");
      discussionReads.push({ journal, offset });
      if (failRead) return route.fulfill({ status: 409, json: { detail: "Synthetic discussion unavailable" } });
    }
    return route.fulfill({ json: { data: { ...item,
      discussion_coverage: "PARTIAL",
      discussion_history: [
        { journal_id: "journal_one", title: "Earlier saved conversation", note_revision_id: previousRevision, created_at: "2026-10-09T00:00:00Z" },
        { journal_id: "journal_two", title: "Another saved conversation", note_revision_id: revision, created_at: item.observed_at },
      ],
      discussion_content: journal ? {
        journal_id: journal,
        note_revision_id: wrongRevision ? "wrong_revision" : journal === "journal_one" ? previousRevision : revision,
        text_offset: offset, next_text_offset: offset === 0 && journal === "journal_one" ? 6000 : null,
        text: journal === "journal_two" ? "Second saved discussion body" : offset === 0 ? "First saved discussion page" : "Remaining saved discussion text",
      } : null,
    } } });
  });
  return { ...state, discussionReads, historyReads, fail: (value: boolean) => { failRead = value; }, mismatch: () => { wrongRevision = true; } };
}

test(`saved discussions read exact historical revisions on demand at 390px`, async ({ page }, testInfo) => {
  const width = 390;
  await page.setViewportSize({ width, height: 900 });
  const state = await savedDiscussions(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Open Note", exact: true }).click();
  expect(state.historyReads).toEqual([]);
  await page.getByText("Optional Discussion", { exact: true }).click();
  expect(state.historyReads).toEqual([]);
  await page.getByText("Saved Discussions", { exact: true }).click();
  await expect(page.getByText("Earlier saved conversation", { exact: true })).toBeVisible();
  expect(state.historyReads).toHaveLength(1);
  expect(state.discussionReads).toEqual([]);
  await page.getByRole("button", { name: "Read Saved Discussion", exact: true }).first().click();
  const reader = page.getByRole("region", { name: "Saved Discussion Text" });
  await expect(reader.getByText("First saved discussion page", { exact: true })).toBeVisible();
  await expect(reader).toContainText(previousRevision);
  await reader.getByRole("button", { name: "Continue Saved Discussion" }).click();
  await expect(reader.getByText("Remaining saved discussion text", { exact: true })).toBeVisible();
  await expect(reader.getByText("First saved discussion page", { exact: true })).toHaveCount(0);
  await reader.getByRole("button", { name: "Back to Discussion Start" }).click();
  await expect(reader.getByText("First saved discussion page", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Read Saved Discussion", exact: true }).click();
  await expect(reader.getByText("Second saved discussion body", { exact: true })).toBeVisible();
  await expect(reader.getByText("First saved discussion page", { exact: true })).toHaveCount(0);
  expect(state.discussionReads).toEqual([
    { journal: "journal_one", offset: 0 }, { journal: "journal_one", offset: 6000 },
    { journal: "journal_one", offset: 0 }, { journal: "journal_two", offset: 0 },
  ]);
  expect(state.writes).toEqual([]);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  if (process.env.TP_CAPTURE_NOTE_CONVERSATION === "1") {
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
    await page.screenshot({ path: testInfo.outputPath(`note-conversation-${width}.png`), fullPage: true, animations: "disabled" });
  }
  await page.reload();
  await page.getByRole("button", { name: "Open Note", exact: true }).click();
  await page.getByText("Optional Discussion", { exact: true }).click();
  await page.getByText("Saved Discussions", { exact: true }).click();
  await expect(reader).toHaveCount(0);
  expect(state.discussionReads).toHaveLength(4);
});

test("failed saved discussion read retries without a duplicate save and rejects mismatched source", async ({ page }) => {
  const state = await savedDiscussions(page);
  state.fail(true);
  await page.goto("/");
  await page.getByRole("button", { name: "Open Note", exact: true }).click();
  await page.getByText("Optional Discussion", { exact: true }).click();
  await page.getByText("Saved Discussions", { exact: true }).click();
  await page.getByRole("button", { name: "Read Saved Discussion", exact: true }).first().click();
  await expect(page.getByText(/Saved discussion unavailable. Retry this read/)).toBeVisible();
  state.fail(false);
  await page.getByRole("button", { name: "Retry Discussion Read" }).click();
  await expect(page.getByText("First saved discussion page", { exact: true })).toBeVisible();
  state.mismatch();
  await page.getByRole("button", { name: "Continue Saved Discussion" }).click();
  await expect(page.getByText(/Saved discussion unavailable. Retry this read/)).toBeVisible();
  await expect(page.getByText("Remaining saved discussion text", { exact: true })).toHaveCount(0);
  expect(state.writes).toEqual([]);
});


test("summary-only handoff requests metadata without authorizing a private text read", async ({ page }) => {
  const state = await mock(page);
  await page.route("**/api/console/api/note-desk", route => route.fulfill({ json: { data: {
    items: [{ ...item, coverage: "SUMMARY_ONLY", change: "CONTENT_UNAVAILABLE" }], has_more: false,
  } } }));
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
      writeText: async (value: string) => { document.documentElement.dataset.copiedRequest = value; },
    } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Open Note", exact: true }).click();
  await expect(page.getByRole("button", { name: "Read Private Note Text" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Review This Note in Journal" })).toHaveCount(0);
  await page.getByText("Optional Discussion", { exact: true }).click();
  await page.getByRole("button", { name: "Copy Codex Request" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-copied-request", /inspect metadata/);
  await expect(page.locator("html")).toHaveAttribute("data-copied-request", /Full note text is unavailable/);
  expect(state.contentReads).toEqual([]);
  expect(state.writes).toEqual([]);
});


test("older notes and private title searches have real paging with no URL leakage", async ({ page }) => {
  const state = await mock(page);
  const searches: Record<string, unknown>[] = [];
  await page.route("**/api/console/api/note-desk{,?*,/search}", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const body = request.method() === "POST" ? request.postDataJSON() : null;
    if (body) { searches.push(body); expect(url.search).toBe(""); }
    const offset = body?.offset ?? Number(url.searchParams.get("offset") ?? 0);
    const title = body ? "Private synthetic match" : offset ? "Older synthetic note" : item.title;
    return route.fulfill({ json: { data: { items: [{ ...item, title }], offset, has_more: !body && offset === 0 } } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Older Notes", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Older synthetic note" })).toBeVisible();
  await page.getByRole("button", { name: "Newer Notes", exact: true }).click();
  await expect(page.getByRole("heading", { name: item.title })).toBeVisible();
  await page.getByLabel("Find Saved Notes").fill("Private synthetic");
  await page.getByRole("button", { name: "Search Notes", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Private synthetic match" })).toBeVisible();
  expect(searches).toEqual([{ query: "Private synthetic", offset: 0 }]);
  expect(page.url()).not.toContain("Private");
  await page.getByRole("button", { name: "Clear Search", exact: true }).click();
  await expect(page.getByRole("heading", { name: item.title })).toBeVisible();
  expect(state.writes).toEqual([]);
});
