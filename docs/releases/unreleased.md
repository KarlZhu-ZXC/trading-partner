# Unreleased

- Added the Phase 3C-0 QuantConnect Free manual bridge without increasing the
  28-tool MCP inventory: prepare hashed LEAN packages and import user-downloaded
  result JSON with explicit remote-code and dataset-version limitations.
- Slimmed the runtime package by removing unused provider/codec compatibility
  façades and moving delivery-evaluation validators into test support while
  retaining the declarative eval catalogs.
- Removed the deprecated Polymarket-only proxy setting; CME, DCE, Dukascopy,
  Polymarket, and Telegram now consistently use `PROVIDER_PROXY_URL`.
- Published `compact-v4`: optional discriminator mappings and schema defaults are
  omitted from `tools/list` while server-side Pydantic validation/defaults remain
  unchanged, reducing aggregate input schema from 40,544 to 35,882 bytes.
- Consolidated completed implementation notes into current phase specifications,
  release notes, and a bounded active known-issues document.
- Replaced the flat `ApplicationContainer` service locator with five explicit
  capability bundles, extracted infrastructure-only persistence and Provider graph
  builders, and reduced `bootstrap.py` to a bounded cross-layer connector.
- Split the 2,327-line ORM declaration monolith into ten capability modules under a
  single metadata registry without changing tables, constraints, or migrations.
