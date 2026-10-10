import { expect, test, type Page } from "@playwright/test";
const revision = "external_note_revision_00000000-0000-7000-8000-000000000001";
const item = { note_id: "note_synthetic", note_revision_id: revision, version: 1, title: "My Moomoo thinking", instrument_id: null, coverage: "FULL", observed_at: "2026-10-10T00:00:00Z", change: "NEW_USER_NOTE", requires_status_update: false };
async function mock(page: Page, discussed = false) {
  const writes: Record<string, unknown>[] = [];
  const contentReads: string[] = [];
  await page.route("**/api/console/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname.replace("/api/console", "");
    if (req.method() === "POST") { writes.push(req.postDataJSON()); return route.fulfill({ json: { result: { ok: true, data: { status: "SUCCEEDED" } } } }); }
    if (path === "/api/session") return route.fulfill({ json: { token: "synthetic-note-desk-session-token-only" } });
    if (path === "/api/note-desk") return route.fulfill({ json: { data: { items: [{ ...item, discussion: discussed ? { journal_id: "journal_synthetic" } : null }], coverage: "COMPLETE", has_more: false } } });
    if (path.startsWith("/api/note-desk/")) {
      const include = url.searchParams.get("include_content") === "true";
      if (include) contentReads.push(path);
      return route.fulfill({ json: { data: { ...item, blocks: [{ index: 0, speaker_kind: "USER", speaker_label: "USER", section_date: null }], content: include ? { block_index: 0, text_offset: 0, speaker_kind: "USER", text: "Synthetic private thought, not a formal judgment." } : null, discussion_history: [], discussion_coverage: "COMPLETE" } } });
    }
    if (path === "/api/overview") return route.fulfill({ json: { review_items: [], workflow_attention_projection: [{ source_ref: "order_synthetic", review_item_id: "review_pending", source_type: "BROKER_ORDER_INTENT", status: "OPEN", title: "Unresolved Synthetic Order", href: "/decision-workbench#timeline" }], partial_failures: [] } });
    return route.fulfill({ json: { items: [], enabled: false, configured: false } });
  });
  return { writes, contentReads };
}

test("notes are the default with no case prerequisite, model call or automatic private read", async ({ page }) => {
  const state = await mock(page);
  await page.goto("/");
  await expect(page.getByRole("tab", { name: "Your Notes", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("My Moomoo thinking", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Unresolved Synthetic Order", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Update Review Queue", exact: true })).not.toBeVisible();
  await page.getByRole("button", { name: "Open Note", exact: true }).click();
  expect(state.contentReads).toEqual([]);
  expect(state.writes).toEqual([]);
  await page.getByRole("button", { name: "Read Private Note Text" }).click();
  await expect(page.getByText("Synthetic private thought, not a formal judgment.")).toBeVisible();
  expect(state.contentReads).toHaveLength(1);
  expect(state.writes).toEqual([]);
});

test("sync only requests notes and recorded discussions do not demand state maintenance", async ({ page }) => {
  const state = await mock(page, true);
  await page.goto("/");
  await expect(page.getByText(/No undiscussed owner changes/)).toBeVisible();
  await page.getByRole("button", { name: "Show All Recent Notes" }).click();
  await expect(page.getByText("My Moomoo thinking", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Sync Moomoo Notes" }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0]).toMatchObject({ tool_name: "external_state_sync", arguments: { request: { operation: "notes" } }, confirmation: "external_state_sync" });
  expect(state.contentReads).toEqual([]);
});

test("note reading is usable at narrow width and remains an explicit read", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await mock(page);
  await page.goto("/");
  const open = page.getByRole("button", { name: "Open Note", exact: true });
  await open.focus(); await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Read Private Note Text" }).click();
  await expect(page.getByText("Synthetic private thought, not a formal judgment.")).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  expect(state.writes).toEqual([]);
});
