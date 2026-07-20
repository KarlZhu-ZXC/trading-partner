# a-stock-data reference notes

## Intent

Phase 1 (later phases, especially 1E) will implement A-share market data
capabilities informed by established A-share data library patterns:

- Prefer lower-block-risk quote/K-line sources before East Money
- East Money serial rate limiting
- Independent fallback sources with different domains / risk controls
- Full-stack A-share fact types (quotes, financials, fund flow, limit-up ecology, etc.)

## Phase 1A scope

Phase 1A only records upstream metadata and license attribution. No a-stock-data
code is copied into the runtime package. The only market provider in Phase 1A is
the in-memory mock (`MockAShareMarketSnapshotProvider`).

## Pin

Reference snapshot: `9ed665cc9773457bc23fed6b770b2b5a8cede40f`. See `UPSTREAM.md`.

## Runtime design rule

The pinned Skill is the primary reference for A-share capability coverage, endpoint
field maps, source priority, known-dead endpoints, throttling, and fallback strategy.
Trading Partner must still independently live-verify every adopted endpoint and
reimplement it behind project-owned typed ports, adapters, codecs, caches, provenance,
and Tool Envelopes.

The upstream Skill is never imported or executed at runtime. Its guidance is applied
as follows:

1. Prefer Tencent / other low-block-risk sources for capabilities they can truthfully
   provide; do not route ordinary quote or K-line traffic through East Money by default.
2. Use East Money for its unique datasets and always pass its requests through the
   process-wide serial gate.
3. Choose fallbacks from a different host and risk-control surface when one exists.
4. Preserve adjustment, unit, timestamp, authority, and reliability semantics; a
   fallback may not pretend to provide a basis it cannot prove.
5. Pin and document the consulted upstream commit before changing provider routing.
