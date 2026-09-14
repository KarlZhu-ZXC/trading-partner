# Console implementation rules

Maintained agent-facing detail owned by the root `AGENTS.md`. Read the sections
relevant to the affected behavior; paths in this document are repository-relative.

## Console heading and control language

Console sections must use one consistent information hierarchy. A card header has **at most two
text levels** and uses exactly one of these modes: **kicker → title** for a functional section, or
**title → object subtitle** for an object overview such as `THEME` followed by the specific Theme
name. Never render kicker, title, subtitle, and description together in one header. The rendered
header keeps a divider between the heading and the card body.

- **Kicker** names the functional domain, workflow stage, or operating constraint. It is short,
  uppercase, and must not repeat the title with different capitalization. Examples:
  `EVENT COVERAGE`, `DECISION WORKFLOW`, `RESEARCH HEALTH`, `APPEND-ONLY EDITING`.
- **Title** names the stable object or section the user is viewing. Use concise Title Case for
  ordinary sections. For a Research Subject overview, the subject type is the title (for example,
  `THEME`) and the specific Research Subject title is the subtitle.
- **Object subtitle** is reserved for the specific identity beneath an object-type title. It must
  not be combined with a kicker.
- **Description** is an optional single, concise intro paragraph below the header divider. It may
  explain scope, behavior, provenance, or a safety boundary, but is never another header level.
  Do not use a metric list such as `Next 7D / upcoming / overdue` as a title; render metrics in the
  card body.

Avoid synonymous pairs such as `TODAY` / `Decision Inbox`, `CATALYST AGENDA` /
`Catalyst Agenda pulse`, or `RESEARCH SUBJECTS` / `All Research Subjects`. Prefer distinct
relationships such as `DECISION WORKFLOW` / `Today’s Inbox` and `EVENT COVERAGE` /
`Catalyst Pulse`; put any further explanation below the divider. A section should make sense from
its two header levels alone.

Passive labels and interactive controls must also read differently. Statuses use a passive
dot-plus-text treatment without border, fill, hover, or button-like padding. Tags describe nouns
or classification values and use the passive tag treatment. Buttons use an action verb, retain an
obvious border/fill plus hover/focus/disabled states, and must not be styled like tags. Description
Lists across pages use the shared component and top-rule treatment rather than page-specific
boxed variants. Primary, destructive, and secondary actions must be spatially and visually
distinct. When adding or renaming a prominent Console section, update the rendered-HTML regression
tests so the intended heading relationship cannot silently regress.

Every editable Console field that is required for the current action must show a red leading
asterisk in its visible label and expose matching native `required` or `aria-required="true"`
semantics. Conditional requirements show the asterisk only while the condition applies; an
either/or requirement marks the field group rather than incorrectly marking every member.
Placeholders, helper text, validation errors, and a `(Required)` suffix never replace the
asterisk. Optional fields receive no asterisk and do not need an `(Optional)` suffix. Immutable
disabled metadata is not marked required in edit mode. New or changed forms must extend the
Console UI-convention regression test so this contract cannot silently regress.

## Console design-system implementation

The Console uses `console/app/tokens.css` for navy/cyan dark and light themes.
Ordinary controls must come from `console/app/components/ui/`; `components/ui.tsx`
is a compatibility export, not a second visual implementation. Page CSS Modules own
layout and domain content, never a competing button/input/select/status skin. Add
component variants centrally. Navigation uses LinkButton/TextLink; actions use Button;
status dots, noun Tags, removable FilterChips and SelectableRows are distinct.

All Console routes and the Agent rail have migrated. Ordinary native controls are
forbidden outside the shared primitive/widget layer; there is no page allowlist.
Run the component-conventions check for UI changes. Module-owned surface styles live
under `console/app/styles/`, and `globals.css` contains only resets/document rules.
Do not create new page-specific control skins. Synthetic previews use their own build
directory and loopback port; never put private notes or account values in fixtures.
For frontend-only deployment, restart Console Web after building and verifying it;
do not unnecessarily restart the data API or scheduled jobs.

Current component usage and preview instructions: `docs/guide/console-design-system.md`.

The Market & Technical Lens uses KLineChart only inside a mounted client component.
Fetch bars through the validated technical snapshot with complete Console result
mode. Keep `derived:smc` overlays locked and `user:drawing` overlays editable and
session-only. Built-in chart indicators are visual aids; backend technical DTOs remain
the authoritative facts. Retain the Matplotlib PNG path for MCP/Agent artifacts.
