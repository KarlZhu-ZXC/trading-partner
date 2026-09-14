# Console design system

Console uses a navy workspace with cyan accents and complete light/dark themes.
The brand mark remains unchanged. Body text is 14px and supporting labels are
12px; tabular numbers and code use the monospace family. A saved theme wins over
the default light theme. Reduced-motion preferences suppress decorative animation.

## Ownership and composition

- `console/app/tokens.css` owns colors, typography, spacing, radii, surfaces and motion.
- `console/app/components/ui/` owns ordinary controls and their CSS Modules.
  `components/ui.tsx` remains the compatibility export for existing callers.
- Use `Button` (primary/secondary/danger, sm/md), `LinkButton`, `TextLink` and
  accessible `IconButton`. Navigation uses links; mutations use buttons.
- Use `Input`, `Select`, `Textarea`, `DateRange`, `FormField`, `FilterBar` and
  `MultiSelectAutosuggest`. The latter owns its embedded, borderless input variant.
- Use `Card`/`SectionHeader`, `Metric`, `DescriptionList`, `Table`, `Tabs`, `Dialog`,
  `Disclosure`, `Paginator`, `DataBoundary`, `Empty` and `ErrorNote` for shared patterns.
- `Badge` is a passive dot and status. `Tag` names a classification. `FilterChip`
  represents a removable filter. `SelectableRow` owns row interaction and selection.
- Page modules compose layouts and domain content. They must not paint new button,
  input, select or status styles, redefine primitive borders/fonts, or use literal
  colors. Add intentional variants to the owning component instead.

## Journal filter ownership

Journal owns filter state. Notes uses the page Instrument and Search Notes controls;
ObservationInbox receives the resulting items and never adds a second query/scope.
Account and date controls are hidden on Notes/Reviews; saved trade filters continue
on relevant tabs. Cycle quality/status controls are absent from Behavior, whose
exclusions remain authoritative. Text search covers note title and summary only.

## Enforcement across Console

All Console routes and the Copilot rail use the shared control layer. There is no
raw-control migration allowance. `node --test tests/component-conventions.test.mjs`
checks every surface, competing control styles, literal colors and CSS variables.
Native interactive elements belong only to the shared primitive/widget layer.

Surface and workspace styles are owned by the modules under `console/app/styles/`;
`globals.css` contains resets and document-wide accessibility behavior only. Their
scopes are activated at the Console boundary because domain widgets may be used on
more than one route. Primitives own control skins; surface modules own arrangement
and domain-content presentation. Add variants centrally rather than reviving old
page-specific control selectors.

The only runtime-owned CSS properties are the Copilot rail width and entity page size.
The layout smoke tests use synthetic data across the primary and auxiliary routes
in both themes at 390, 1440 and 1920px. Existing Journal/Research/Agent tests retain
confirmation, account filtering and durable reconnect coverage.

## Isolated, synthetic preview

Build separately so visual work cannot replace the running Console build:

```sh
cd console
CONSOLE_BUILD_DIR=.next-design-preview CONSOLE_DESIGN_PREVIEW=1 \
NEXT_PUBLIC_CONSOLE_DESIGN_PREVIEW=1 NEXT_PUBLIC_TRADING_PARTNER_API=/api/design-preview \
npm run build
CONSOLE_BUILD_DIR=.next-design-preview CONSOLE_DESIGN_PREVIEW=1 \
node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3101
```

Journal is `/decision-workbench`; the component gallery is `/design-system`.
The preview displays an explicit synthetic-data notice. Its API never calls the
business backend and rejects operational writes. Empty/error sample states are
selected in its banner. In ordinary production, the gallery is not in navigation
and the preview API returns 404 unless explicitly enabled. Real notes and account
values must never enter fixtures, screenshots, source code or documentation.

Production uses the ordinary build with preview flags disabled. After successful
UI verification, build `console/.next` and restart only the Console Web process for
a frontend-only release; the data API and scheduled jobs do not need a restart.

### Journal query and recovery semantics

Journal activity filtering runs against complete local durable history. The public
MCP read limits remain unchanged. Cycle reconstruction retains earlier opening
activity; only then does account/instrument/date selection apply. Closed Cycles use
their closing timestamp, open Cycles their opening timestamp, and activity rows their
occurrence timestamp, with inclusive endpoints. Behavior uses the same Cycle cohort.
The UI paginates the selected local result; transport cursor pagination is not added.
Local read completeness never implies complete broker activity or known cost basis.

Notes do not inherit hidden account/date filters. Their source refresh status and
read-only context use shared Card, DescriptionList, Button and LinkButton components.
Progress survives browser reload through a saved opaque run identity; restoring the
view never resubmits the refresh. Historical note text and current confirmed context
are explicitly distinguished.

## Page layout and interaction

Specialist pages share the Research workspace composition; Overview retains its dashboard composition. Agent conversations use the shared Rail, and `/chat` redirects to `/?agent=open`.

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

- Spacing and control dimensions come from `tokens.css` and the owning shared primitive; align controls in a row without page-local size overrides.
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

## Interactive technical chart

The Market & Technical Lens owns the client-only KLineChart workspace. Initialize it
after mount, give its container an explicit height, resize it with its stable parent,
and dispose it on unmount. Toolbar controls use the shared Button and Select skins.

Source bars and SMC come from one validated `technical_get_snapshot` read using
complete Console result mode. Derived overlays use the locked `derived:smc` group;
user drawings use the editable `user:drawing` group. Chart-library indicators are
visual aids and must not replace the sourced snapshot or create Monitor/Decision/order
facts. Drawings are session-only until a versioned local persistence contract exists.
Keep the Matplotlib PNG path for Agent/MCP artifacts and portable export.
