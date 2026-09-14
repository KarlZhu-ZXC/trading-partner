# Shared Agent Runtime contracts

Maintained agent-facing detail owned by the root `AGENTS.md`. Read the sections
relevant to the affected behavior; paths in this document are repository-relative.

## Shared Agent Runtime (Agent-A–D)

The Shared Agent Runtime is disabled by default and provider-neutral. It persists durable
conversations, explicit channel bindings, append-only messages, bounded model/tool
receipts and Pending Actions through migrations `0044`–`0046`. Historical Telegram
channel values and the old cursor/handoff tables remain for database compatibility;
there is no Telegram Agent transport, cursor writer, or handoff service. Migration
`0049` adds durable Agent Turn lifecycle records for refresh/
disconnect recovery, migration `0050` adds explicit owner-scoped Agent presentation
preferences, and migration `0051` discards legacy Moomoo `initial_margin` values that
were persisted as financing usage before `debtCash` became the semantics. Terminal
failures persist only bounded safe error codes. The single Console Agent Rail
is the only built-in Agent channel; Telegram is outbound notifications only;
Agent broker orders remain unavailable and are not authorized by a research action.

The model sees only private `tp_capability_search`, `tp_read`, `tp_propose`,
confirmation-preparing `tp_prepare_action`, and read-only `tp_web_search`; these names
are never registered as public MCP tools. `tp_web_search` is available to every Agent
model through the server-owned Tavily Search sidecar when configured. The sidecar uses
bounded structured results directly, performs no additional Bailian model call, and
never sends the Tavily key to an answer model. Queries persist only as hashes and
durable receipts retain the actual `tavily` provenance. Search summaries and webpages
are untrusted background and cannot override canonical Trading Partner facts. Agent-A may
auto-run durable/provider reads, instrument discovery/cache, and explicitly non-executing
technical artifacts. All other writes remain denied unless Agent-D's explicit operation
allowlist creates a Pending Action that the same channel/principal confirms using the
exact hash, version, expiry, and single-use opaque token. Sync/evaluate, accounts, risk
policy, and every broker write remain denied. Exact grouped-operation DTO validation and
the public MCP inventory remain unchanged. Conversation memory is continuity context,
never a source of current prices, positions, fills, or research state.

Capability discovery distinguishes automatic `read` from `prepare_action`; discovering a
write schema never invokes it. Independent read calls may execute with bounded parallelism,
while model-order messages/events/receipts remain deterministic. A refreshed Console may
list a durable `PRESENTED` action but must not persist its raw token or automatically recover
confirmation authority. Only an explicit user resume may rotate the token under exact
conversation/channel/principal/expiry/version CAS; the old token becomes invalid and the
arguments plus expiry remain unchanged. Navigation-only Console context is untrusted and
must never substitute for a capability read. The default Bailian Agent endpoint publishes
bounded native Web Search and extractor support; usage plus source URLs must remain explicit,
and webpages cannot override canonical Trading Partner price, position, level, return, or
quantity facts. Endpoints without native support remain search-disabled. Agent broker orders
remain closed unless a later explicit product decision changes that separate gate.

The Console Agent composer links Provider, model, and reasoning-effort selection. Selecting a
Provider asks the backend to fetch and briefly cache its standard model directory with
server-owned credentials; the browser receives only bounded text-model IDs and capability
metadata, never an API key or full endpoint. Catalog failure falls back visibly to the configured
default model. The runtime revalidates the selected Provider, catalog model, and reasoning effort
before persisting the user message, so a modified browser request cannot inject an arbitrary
model name. An enabled `opencode_go` Agent Provider fetches the bounded Go `/models`
directory and exposes safe model IDs from that directory. Known Responses and Messages
models retain their explicit protocol routes; newly discovered models default to the
OpenAI-compatible Chat Completions route because Go's directory currently publishes IDs
without protocol metadata. A future non-Chat model still requires an explicit route hint.
The Console Agent message path supports bounded private PNG/JPEG attachments.
Image capability is separate from model discovery and
does not authorize a model to access any file outside the exact attachment in the turn.
OpenCode Zen `x-preview-f-free` is Ox Alpha Free. It is a reasoning model with
selectable `low`, `high`, and `max` effort; omitting the field means Provider default,
not disabled thinking. Do not group it with Zen free chat models that reject reasoning
parameters.


## Copilot research modes

Console user-facing name is Copilot. Agent symbols/routes/storage keys remain
compatibility identities. Explicit `research_mode=research|challenge` restricts the
existing runtime to read/discovery/search tools; neither proposals nor pending-action
preparation are admitted. Chat (`standard`) retains the existing gates.

Research budgets use monotonic time plus top-level attempted model/tool counts;
fallback, repair and one requested tool-free critique count. Do not override provider
protocol/output-token policy or present budgets as billing guarantees. Do not enable
native model search or an uncounted summary call within research mode. User policy
lives in the original message receipt; final progress/evidence refs live in the
assistant receipt and survive compaction. Closed progress contains codes/counts,
never hidden reasoning, prompts, URLs or exception payloads. Reconnect may reconstruct
completed reads; unavailable model progress/usage must remain explicitly unavailable.
Explicit retry restores the original policy, failing closed on corrupt policy.

Only current-turn canonical field evidence can verify a research FACT/CITATION.
Preserve query cutoff independently from underlying fact time; a nested timestamp
must not widen it. Source web text cannot establish a price/position. Unbound claims
become GAP, qualitative prose remains INFERENCE. The prompt and checker share one
bounded evidence catalog; a truncated reference is not treated as verified. Final
safe text and stored answer envelope must agree. No live model call is implied by
software verification; use the synthetic evaluation catalog by default.

Research uses the field checker directly, without a legacy prose-repair round.
FACT/CITATION may select current catalog refs using the literal `@evidence`
marker and absent context metadata; the host renders each field separately with its context within answer bounds.
Exact catalog copies remain compatible. Explicit null values for allowed fields
are missing-value evidence, never zero; absent keys are not synthesized. Qualitative
explanations may cite multiple valid fields, remain INFERENCE/GAP and are not counted
as verified facts. Numerical/date claims still require exact field binding. Known
catalog paths/list labels are not amounts; affirmative execution claims stay denied.
Malformed or unfinished optional replacement answers must preserve the primary;
returned replacement usage counts even when the replacement is rejected.
