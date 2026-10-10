# Capability usage and audit

The MCP surface has nine public tools, 24 internal capabilities and 106 operations.
These are different inventory counts. Console's Public Tools value uses the public
inventory; the Capability directory labels its internal entries as capabilities.

## Focused discovery

`capability_discover(category="portfolio")` returns only the five portfolio
capabilities. Other closed categories are `research` (7), `market` (6), `monitoring`
(3) and `operations` (3). The groups partition the inventory exactly. Omitting the
category retains the full catalogue; exact tool/operation discovery remains
available for every operation. Mixing category with tool/operation is rejected.
This backward-compatible optional input is schema version `mcp-vnext-shadow-v13`.
No capability or authorization was removed or merged.

## Observations, not evidence of non-use

New runtime instances record best-effort invocation aggregates in the owner-only
`RUNTIME_ROOT/data/diagnostics/capability-usage.sqlite3` sidecar. It is disposable
operational data, separate from the business database. Test application containers
disable it by default; recorder tests use isolated sidecars explicitly.

The only dimensions are UTC day, channel (`MCP`, `CONSOLE`, `AGENT`, `INTERNAL`),
registered capability/operation and one closed outcome. Values are count, total
elapsed milliseconds and maximum elapsed milliseconds. Unknown input names become
fixed sentinels. No arguments, request IDs, entity IDs, results, private text,
exception messages, URLs, credentials or account values are recorded. Duration is
an invocation measurement with millisecond resolution, not provider-only latency;
zero can mean a sub-millisecond call.

Outcomes distinguish normal return, known failed envelopes, invalid requests,
denial, exceptions and cancellation. Normal return does not prove complete data or
a correct financial interpretation. Instrumentation does not retry the business
call. A recording failure cannot replace its result and increments a process-local
failure counter. SQLite contention waits at most 50 ms; retention is 30 days with
a bounded aggregate-row budget. The export remains labelled `best_effort`.

`GET /api/capability-usage` exports the retained aggregates and inventory counts
without initializing a missing file. Capabilities shows the most observed operations.
This is a prospective observation window, not a reconstruction of historical usage.
It omits discovery, direct service/CLI calls and rejections before shared-registry
dispatch; Agent-local review queue operations likewise bypass this graph.
`NOT_OBSERVED` never means unused or safe to delete.

## Development audit

```sh
uv run python scripts/audit_capabilities.py --output /tmp/capability-audit.json
uv run python scripts/audit_capabilities.py --usage /tmp/capability-usage.json --output /tmp/capability-audit.json
uv run python scripts/benchmark_capability_usage.py --output /tmp/capability-meter-benchmark.json
```

The audit assembles the real operation graph using synthetic collaborators. It
never initializes the owner runtime or executes business operations. It reports
all operation identities, implementation references, static literal references,
category sizes and serialized MCP/discovery byte counts. The optional usage input
accepts only known aggregate dimensions; arbitrary payload fields are not copied.
Generated receipts belong outside Git.

Static reference coverage is explicitly partial: dynamic dispatch and direct
service dependencies can exist without a literal capability name. The hypothetical
seven-entry size estimate only measures bytes saved by hiding proposal/chart entry
metadata. It is not a validated routing, latency, approval or model-quality result.
Keep the nine entries until representative task evidence supports a migration.

A retirement decision needs an adequate observation window, confirmation of
Console/Agent/CLI/monitor dependencies, an explicit product decision, and contract
migration tests. Discovery filtering offers smaller responses now without removing
rare but potentially necessary operations.
