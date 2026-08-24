# Technical Platform Upgrades — 2026-08

Status: **implemented and verified (2026-08-20)**.

The upgrade preserves the local-first product boundary, 27 public MCP tools, explicit
confirmation gates, and the SGOV-only unattended exception. It does not introduce a
remote database, message broker, autonomous research write, or generic order agent.

## Delivered

1. **Secret-safe OpenTelemetry**
   - Disabled by default; optional OTLP/HTTP exporter.
   - Agent Turn and Provider Router spans share one trace context.
   - Only `tp.*` allowlisted attributes; no prompt, payload, URL, headers, tokens,
     exception text, or stack trace.
   - Safe 32-hex trace correlation persists in Agent model receipts.

2. **Playwright browser gate**
   - Production Next build is tested with Chromium.
   - Covers `/chat → /?agent=open`, Provider-scoped model/reasoning persistence, and
     incomplete SSE → durable reconnect without resending the user turn.

3. **SQLite WAL**
   - `journal_mode=WAL`, `synchronous=NORMAL`, 30-second busy timeout, and 1,000-page
     autocheckpoint on production engine connections.
   - Maintenance status exposes safe WAL/checkpoint diagnostics.

4. **Durable Operational Job Runtime**
   - Migration `0052_operational_job_runs`.
   - Atomic claim, idempotency key, lease owner hash, heartbeat CAS, attempt count,
     terminal receipt, expired-lease recovery, and no exception body persistence.
   - Wired into Monitor due, Post-market run/catch-up, and SGOV auto-run; existing
     business idempotency and process locks remain defense-in-depth.

5. **Generated contracts**
   - Pydantic validation schemas generate tracked TypeScript contracts and SHA-256
     manifest through `npm run generate:contracts`.
   - `npm run check:contracts` fails CI on drift.
   - Console Ephemeral Context consumes the generated type.

6. **Agent LLM resilience routing**
   - Console/Telegram Agent endpoints on Bailian and DeepSeek use shared admission
     scheduling, circuit breaker, and durable Provider route receipts under
     `GLOBAL/INTERACTIVE_QA`.
   - Authentication, contract, caller cancellation, and quota signals do not
     incorrectly trip endpoint-unavailable circuits.
   - Streaming still never retries after content is emitted.

7. **Typed Agent Answer protocol**
   - Versioned SUMMARY/FACT/INFERENCE/GAP/NEXT_STEP/CITATION blocks.
   - Bounded evidence refs, source URLs, `as_of`, and basis metadata.
   - Runtime validates and renders blocks, persists the envelope beside the evidence
     manifest, and preserves exact legacy plain text through a fallback block.

8. **Optional local hybrid Research Search**
   - Migration `0053_research_search_vectors` adds a rebuildable local vector projection.
   - FastEmbed is an optional dependency and is disabled by default.
   - No research text is sent to a remote embedding service.
   - FTS5 and semantic ranks combine through deterministic reciprocal-rank fusion.
   - Missing dependency/model/vector failures safely fall back to lexical search and
     never block a durable research write.

## Operator commands

```bash
# Optional tracing
OTEL_TRACING_ENABLED=true
OTEL_EXPORTER=otlp_http
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces

# Optional local semantic search (requires the optional dependency)
uv sync --extra semantic-search
RESEARCH_SEMANTIC_SEARCH_ENABLED=true

# Contract drift and browser gates
cd console
npm run check:contracts
npm run test:e2e
```

Tracing and semantic search remain opt-in. Enabling either is an operational choice,
not an authorization to sync accounts, confirm research, or execute an order.

## Verification

- Python: current checkout `2442 passed`; Ruff and mypy passed.
- Console: production build, 41 Node tests, and 5 Chromium Playwright tests passed.
- Agent behavior evaluation: 15/15 plus schema repair passed.
- Isolated wheel smoke reached migration head `0056_decision_review_due_items` and emitted
  `ISOLATED_WHEEL_SMOKE_OK`.
- Gitleaks history scan passed; changed and untracked source files are scanned separately
  from ignored dependency/build directories.
