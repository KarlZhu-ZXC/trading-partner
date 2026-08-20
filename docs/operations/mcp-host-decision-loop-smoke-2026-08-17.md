# MCP host decision-loop smoke — 2026-08-17

Status: **passed on current source checkout**. The already-connected Grok
stdio process was still the pre-PR3 binary and could not exercise
`attention`.

Machine-readable receipts:
[mcp-host-decision-loop-smoke-2026-08-17.json](mcp-host-decision-loop-smoke-2026-08-17.json)

## Scope

Read-only only. No `external_state_sync`, no `monitor_evaluate`, no
candidate confirm, no broker preview/submit/cancel.

| Host | Result |
|---|---|
| This Grok session’s live MCP process | Stale. `system_health` has no `attention_summary`. `investment_case_read/attention` failed with `union_tag_invalid` (`query`/`context` only). `monitor_read/dashboard` returned a full 57,597-byte envelope with no `_truncated`. Reload `/mcps` to pick up the current tree. |
| Current checkout via `create_capability_registry` | Passed. Numbers below. |

## Current-tree receipts

Generated at `2026-08-17T13:20:04Z`. Public tool count remains 27.

| Call | ok | bytes | truncated | notes |
|---|---|---:|---|---|
| `system_health` | true | 13,603 | no | Process `ok`. `attention_summary.basis=materialized_review_items`, `live_projections_not_included=true`, `coverage_status=UNKNOWN`, `open_review_item_count=0`, `catalyst_sync_receipt_missing=true`. |
| `investment_case_read/attention` | true | 11,352 | no | `mode=durable_only_read`. 12 open items (5 Trade Retro, 7 Data Quality). Limitations include `CATALYST_AGENDA_SYNC_RECEIPT_MISSING`. All nine coverage sources present; agenda is `PARTIAL`. |
| `monitor_read/dashboard` | true | 6,473 | **yes** `monitor_read_dashboard_v1` | Same dashboard is 57,597 bytes on the stale Grok process. New path keeps `ok` and is not cut mid-JSON. |
| `research_judgment_get/state` | true | 12,214 | **yes** `research_judgment_get_state_v1` | Read-only TTWO Research Subject (`active`). No pending candidate existed, so this used a retro-linked `case_id` instead of confirming anything. |

Health summary was `COMPLETE`-looking zero ReviewItems (`UNKNOWN` / `materialized_at=null`). Attention still returned 12 live projections. That is the intended “summary cannot skip inbox” contract.

## Reload the live Grok server

In the TUI: `/mcps` then `r`. After reload, `system_health` must show
`attention_summary`, and `investment_case_read` must accept
`operation=attention`.
