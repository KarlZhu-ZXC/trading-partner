import { expect, test, type Page } from "@playwright/test";

const revision = "external_note_revision_00000000-0000-7000-8000-000000000001";
const olderRevision = "external_note_revision_00000000-0000-7000-8000-000000000002";
const item = { note_id: "synthetic", note_revision_id: revision, version: 2, title: "Synthetic thinking", instrument_id: "equity:US:SYNM", coverage: "FULL", observed_at: "2026-10-10T00:00:00Z", change: "NEW_USER_NOTE" };

async function setup(page: Page) {
  const writes: Record<string, unknown>[] = [];
  const privateReads: string[] = [];
  const historyReads: string[] = [];
  let saved = false;
  let failures = 0;
  let savedBody = "";
  await page.route("**/api/console/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname.replace("/api/console", "");
    if (path === "/api/session") return route.fulfill({ json: { token: "synthetic-note-memory-session-token" } });
    if (req.method() === "POST") {
      expect(path).toBe("/api/tools/invoke");
      const body = req.postDataJSON(); writes.push(body);
      if (failures > 0) { failures--; return route.fulfill({ status: 500, json: { detail: "Synthetic save failure" } }); }
      saved = true; savedBody = body.arguments.request.body_markdown;
      // Simulate a committed write followed by a lost HTTP response.
      return route.abort("failed");
    }
    if (path === "/api/note-desk") return route.fulfill({ json: { data: { items: [item], offset: 0, has_more: false } } });
    if (path.endsWith("/conclusion-receipt")) return route.fulfill({ json: { data: {
      status: saved ? "RECORDED" : "NOT_FOUND", note_revision_id: revision,
      ...(saved ? { journal_id: "journal_synthetic", title: "Synthetic conclusion", formal_judgment_updated: false } : {}),
    } } });
    if (path.endsWith("/history")) {
      historyReads.push(path);
      return route.fulfill({ json: { data: {
        note_revision_id: revision, scope: "INSTRUMENT", instrument_id: item.instrument_id,
        cursor: null, next_cursor: null, journals_status: "AVAILABLE", coverage: "COMPLETE",
        window_start: "2026-10-01T00:00:00Z", window_end: "2026-10-10T00:00:00Z",
        items: [
          { ...item, event_id: revision, kind: "NOTE_REVISION", at: item.observed_at },
          { event_id: "journal_synthetic", kind: "PERSONAL_CONCLUSION", journal_id: "journal_synthetic", note_revision_id: olderRevision, title: "Earlier personal conclusion", at: "2026-10-03T00:00:00Z" },
          { ...item, note_id: "another_note", note_revision_id: olderRevision, event_id: olderRevision, title: "Earlier separate note", kind: "NOTE_REVISION", version: 1, at: "2026-10-01T00:00:00Z" },
        ],
      } } });
    }
    if (path.endsWith("/history-trades")) {
      expect(url.searchParams.get("start")).toBe("2026-10-01T00:00:00Z");
      expect(url.searchParams.get("end")).toBe("2026-10-10T00:00:00Z");
      return route.fulfill({ json: { data: { cursor: null, next_cursor: null, transactions_status: "AVAILABLE", history_coverage: "UNVERIFIED", items: [{ kind: "TRANSACTION", transaction: { instrument_id: item.instrument_id, occurred_at: "2026-10-02T00:00:00Z", side: "buy", quantity: "2", price: "12", fees: null, currency: "USD", provider: "synthetic", account_ref: "fixture-account", source_type: "SYNTHETIC" } }] } } });
    }
    if (path.startsWith("/api/note-desk/")) {
      const selected = path.split("/").at(-1);
      const include = url.searchParams.get("include_content") === "true";
      if (include) privateReads.push(req.url());
      return route.fulfill({ json: { data: { ...item, note_revision_id: selected,
        blocks: [{ index: 0, speaker_kind: "USER", speaker_label: "USER" }],
        content: include && !url.searchParams.has("journal_id") ? { block_index: 0, text_offset: 0, speaker_kind: "USER", text: "Exact historical private source" } : null,
        discussion_content: url.searchParams.has("journal_id") ? { journal_id: "journal_synthetic", note_revision_id: selected, text_offset: 0, next_text_offset: null, text: savedBody || "Earlier personal conclusion body" } : null,
        portfolio_context: { status: "UNMAPPED", items: [] },
      } } });
    }
    if (path === "/api/overview") return route.fulfill({ json: { scope: "notes", partial_failures: [], facts: [], priorities: [], exceptions: [] } });
    return route.fulfill({ json: {} });
  });
  return { writes, privateReads, historyReads, failNext: () => { failures++; } };
}

async function openComposer(page: Page) {
  await page.getByRole("button", { name: "Open Note", exact: true }).click();
  await page.getByText("My Conclusion", { exact: true }).first().click();
  return page.getByRole("region", { name: "Personal Conclusion" });
}

test("personal conclusion has an explicit save and recovers a lost response and reload without private storage", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/");
  const composer = await openComposer(page);
  expect(state.writes).toEqual([]);
  await composer.getByLabel("My Conclusion").fill("Synthetic personal uncertainty, no formal Decision.");
  await composer.getByRole("button", { name: "Save My Conclusion", exact: true }).click();
  await expect(composer.getByText("Your conclusion is saved for this note version.")).toBeVisible();
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0]).toMatchObject({ tool_name: "research_memory_append", confirmation: "research_memory_append", arguments: { request: {
    operation: "journal", case_id: null, authored_by: "user", confirmed_by: "user",
    related_entity_type: "external_note_revision", related_entity_id: revision, topic_tags: ["personal_conclusion_v1"],
  } } });
  expect(await page.evaluate(() => JSON.stringify(sessionStorage))).not.toContain("Synthetic personal");
  expect(state.privateReads).toEqual([]);
  await page.reload();
  const restored = await openComposer(page);
  await expect(restored.getByText("Your conclusion is saved for this note version.")).toBeVisible();
  expect(state.writes).toHaveLength(1);
  expect(state.privateReads).toEqual([]);
  await restored.getByRole("button", { name: "Read Saved Conclusion", exact: true }).click();
  await expect(restored.getByRole("region", { name: "Saved Conclusion Text" })).toContainText("Synthetic personal uncertainty");
  expect(state.privateReads).toHaveLength(1);
});

test("unconfirmed personal save locks the draft and retries the same request", async ({ page }) => {
  const state = await setup(page); state.failNext();
  await page.goto("/");
  const composer = await openComposer(page);
  await composer.getByLabel("My Conclusion").fill("Keep this exact draft.");
  await composer.getByRole("button", { name: "Save My Conclusion", exact: true }).click();
  await expect(composer.getByText(/Save is unconfirmed/)).toBeVisible();
  await expect(composer.getByLabel("My Conclusion")).toHaveValue("Keep this exact draft.");
  await expect(composer.getByLabel("My Conclusion")).toBeDisabled();
  await page.reload();
  const restored = await openComposer(page);
  await expect(restored.getByText(/No saved receipt found yet/)).toBeVisible();
  expect(state.writes).toHaveLength(1);
  await restored.getByLabel("My Conclusion").fill("A different conclusion.");
  await restored.getByRole("button", { name: "Retry Same Conclusion", exact: true }).click();
  await expect(restored.getByText(/differs from the original pending conclusion/)).toBeVisible();
  expect(state.writes).toHaveLength(1);
  await restored.getByLabel("My Conclusion").fill("Keep this exact draft.");
  await restored.getByRole("button", { name: "Retry Same Conclusion", exact: true }).click();
  await expect(restored.getByText("Your conclusion is saved for this note version.")).toBeVisible();
  expect(state.writes).toHaveLength(2);
  expect(state.writes[1]).toEqual(state.writes[0]);
});

test("thinking history joins exact notes and records with independently requested period transactions", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  const state = await setup(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Open Note", exact: true }).click();
  expect(state.historyReads).toEqual([]);
  await page.getByText("Thinking History", { exact: true }).click();
  const history = page.getByRole("region", { name: "Thinking History" });
  await expect(history.getByText("Earlier separate note", { exact: true })).toBeVisible();
  expect(state.privateReads).toEqual([]);
  await history.getByRole("button", { name: "Read Saved Record", exact: true }).click();
  await expect(history.getByRole("region", { name: "Saved Conclusion Text" })).toContainText("Earlier personal conclusion body");
  expect(state.privateReads[0]).toContain(olderRevision);
  await history.getByRole("button", { name: "Open Note Version", exact: true }).last().click();
  await history.getByRole("button", { name: "Read Private Note Text", exact: true }).click();
  await expect(history.getByText("Exact historical private source", { exact: true })).toBeVisible();
  expect(state.privateReads.at(-1)).toContain(olderRevision);
  await history.getByRole("button", { name: "Show Transactions in This Period", exact: true }).click();
  const trades = history.getByRole("region", { name: "Transactions in This Period" });
  await expect(trades).toContainText("fixture-account");
  await expect(trades).toContainText("Fees Unavailable");
  await expect(trades).toContainText("UNVERIFIED");
  expect(state.writes).toEqual([]);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
