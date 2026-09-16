# Trading Partner — Known Issues

> Updated: 2026-09-16
> Scope: active defects and accepted operational constraints only. Resolved work
> belongs in the current product specification and release notes.

## Active issues

No active implementation defects are currently recorded. The four synthetic
real-model acceptance scenarios passed with DeepSeek Flash at explicitly selected
`high` effort; this does not qualify every model, effort or research question.

Resolved fixes are recorded in [Unreleased](../releases/unreleased.md).

## Accepted operational constraints

- `max` effort can exhaust the default 180-second Research budget before a final
  answer. The configured default is unchanged. The acceptance runner supports
  explicit `--reasoning-effort high` and per-call timing/usage reports so effort
  choices can be tested without changing configuration. The observed passing
  `high` samples are not a general speed/quality ranking. A budget stop remains
  incomplete, not a passing answer; see the
  [operator guide](local-console-and-maintenance.md#copilot-真实模型验收显式执行).
- Research explanations can embed exact current-turn fields with host-rendered
  context. Unbound numerical/date assertions still fail closed, even if a matching
  number exists elsewhere in the catalog. Field checks do not semantically verify
  qualitative interpretations or causal claims; cited explanations may still
  misread scope. Console labels this distinction explicitly. Synthetic regression
  gates do not establish live-model research quality or investment effectiveness.
- Trusted-LAN Console mode is authenticated HTTP, not public hosting. The data API
  remains loopback-only; use a private TLS/device-identity network across untrusted
  links.
- Exact Moomoo Watchlist synchronization is bounded by OpenD group request limits;
  scheduled sync performs the slow exact replay while ordinary reads stay durable-only.
- Moomoo private-note enrichment uses an unofficial read-only internal Web surface and
  may degrade to local cache when authentication, throttling, or page shape changes.
  Summary-only text never reaches model analysis or Decision adoption.
- Reddit RSS and free public CME/DCE/Dukascopy/Polymarket routes are best effort.
  Unavailability remains typed and may use only explicitly configured bounded fallbacks.
- Portfolio performance remains native-currency and fail-closed. Cross-currency
  aggregation, missing corporate-action lots, unavailable fees, and transferred cost
  basis are not estimated.
- Migrations `0069` and `0070` are intentional forward-only data repairs. Restore uses
  a verified database backup, not an empty Alembic downgrade.
- Local/automated backtests, general autonomous trading, order replacement, complex
  orders, short selling, and unattended execution remain unavailable except for the
  closed installed SGOV cash-sweep scheduler.

See the [current product specification](../product.md) and
[Unreleased](../releases/unreleased.md) for implemented behavior.
