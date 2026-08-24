# MCP host decision-loop smoke — 2026-08-20

Status: **passed through a real current-checkout FastMCP stdio child process**.

Scope was read-only. No sync, Monitor evaluation, Candidate decision, ReviewItem
transition, Pending Action confirmation, broker preview/submit/cancel, or scheduler
operation was invoked.

## Receipt

- Public tools: exactly `27`.
- `system_health`: `ok=true`; `attention_summary` present.
- `investment_case_read/attention`: `ok=true`, `mode=durable_only_read`.
- Visible Attention total: `16`; returned: `10`; `truncated=true`.
- `total_count_is_lower_bound=true` because at least one source is partial; the Agent
  must not call 16 a complete universe count.
- Catalyst Agenda coverage remained `PARTIAL`; the returned limitations retained
  `CATALYST_AGENDA_SYNC_RECEIPT_MISSING` and the existing durable coverage warnings.
- All ten returned items carried an exact public read-only `next_read`.
- The first suggested follow-up was executed through the same stdio session:
  `research_judgment_get/state`, `ok=true`.
- Writes: `false`.

This receipt proves the current source transport, schema, compaction, Attention query,
and one exact follow-up read. It does not claim that the historical Grok process from
the 2026-08-17 receipt was refreshed.

## Shared Console Agent smoke

A separate bounded Bailian `qwen3.8-max/low` Console turn asked for today's items and
explicitly prohibited executing `next_read` or any action. The client connection ended
while the durable turn was `WAITING_TOOL`; the server continued without resending the
message and converged to `COMPLETED`.

- Tool trace: `tp_capability_search → tp_read`.
- Durable business receipt: `investment_case_read/attention` only.
- Assistant message persisted: yes.
- Pending Action or domain write: none.
- Test conversation: archived after verification.

## Browser verification

The production Console was opened through the legacy `/chat` URL in a real browser.
It redirected to `/?agent=open`, rendered the right Rail expanded and reached `READY`.
Provider, model, and reasoning selectors were enabled. A temporary Bailian
`qwen3.7-plus/low` choice survived a switch to DeepSeek and back, proving the
Provider-scoped persistence; the original `qwen3.8-max/Auto` selection was restored.
No Agent message was sent and the page emitted no warning/error console logs.
