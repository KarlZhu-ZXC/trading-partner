# Console Layout Standard

The Research workspace is the reference layout for specialist Console pages. This
standard applies to Research, Portfolio, Journal, Monitors, Catalyst Agenda,
Trade Retro, Scorecards, the Phase 4 Journal workflow, Operations, and Capabilities.
The Overview home page remains
intentionally excluded and retains its dashboard composition. Agent conversations live
only in the shared right Rail; legacy `/chat` redirects to `/?agent=open`.

## Page hierarchy

1. The global header owns navigation collapse, environment state, theme, and Agent
   rail controls.
2. The compact page header contains one canonical page name from
   `CONSOLE_PAGE_LABELS`. It has no kicker or explanatory paragraph.
3. Infrequent page-level actions such as Create, Sync, Run Due, and Refresh belong in
   the shared vertical `PageActionMenu` at the right of the page header.
4. Search, status, date, and current-object selectors are view controls. Keep them in
   a compact `workspace-controls` bar adjacent to the content they filter; do not mix
   them with page-level actions.
5. Peer modules use the shared `HorizontalTabs`. Master collections use the shared
   `EntityBrowser`; the selected object's detail receives the full available width.

## Section and Card headers

Use one of two header patterns:

- category kicker + primary title; or
- primary title + object name.

Do not stack a category, title, subtitle, and description in the same header. A Card
header identifies the section; supporting or policy text belongs below its divider,
close to the data or action it qualifies. Status uses `Badge`; it must not imitate an
action button.

## Density and controls

- Use the shared 8/10/14/16px spacing rhythm and avoid empty vertical bands.
- Form controls in one row share a 38px control height and align on their bottom edge.
- Required fields always show the shared red `RequiredMark`; optional fields do not
  need an “Optional” suffix unless omission has non-obvious semantics.
- Tags are passive metadata. Buttons use action styling and must remain visually
  distinct from tags and status badges.
- Long lists collapse, paginate, or use tabs rather than forcing unrelated page
  regions to grow with them.

## Disclosure and navigation primitives

- Content accordions use the shared `Disclosure` component. Use `panel` for a
  complete editor or workflow, `compact` for supporting detail, and `code` for
  receipts, schemas, or raw durable state. Do not add page-local `<details>` /
  `<summary>` styling, custom plus/minus glyphs, or a second chevron.
- A disclosure summary contains one action-oriented title, at most one short
  explanatory line, optional passive metadata, and the shared chevron. Form fields,
  policy paragraphs, and action buttons belong in the expanded body.
- Cross-page shortcuts use the shared `QuickLink` component. Do not ship naked
  route labels, improvised arrow characters, or card-specific “Open /path” buttons.
  Rich entity cards and links with custom navigation behavior may remain specialized.
- Compact menus and filter popovers are not content accordions. They may use native
  disclosure semantics only when their menu behavior and keyboard interaction are
  separately styled and tested.
- Multi-step corrections such as Cycle Split, Merge, or Relink must expose readable
  object choices and a guided preview flow. Never make users copy opaque IDs or use
  an unbounded native multi-select as the primary interaction.

## Interaction boundaries

- A page load remains durable-only unless the product contract explicitly says
  otherwise.
- Console BFF page reads use the same validated capability schemas and handlers as
  MCP but retain the complete local result. The MCP 15 KiB transport projection is
  reserved for MCP/Agent transport and the explicit Capability Workbench; it must
  never decide which local objects, positions, Candidates, or history rows exist.
- Provider refreshes and state-changing actions remain explicit even when collected
  in the page action menu.
- Moving an action into a shared layout component never weakens its confirmation,
  actor, idempotency, or audit requirements.
- The specialist page owns domain editing; aggregate pages link to it rather than
  duplicating a second write path.
