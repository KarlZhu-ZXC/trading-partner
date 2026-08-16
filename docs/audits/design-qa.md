# Design QA — Console dual side panels

## Evidence

- Source visual truth: `/var/folders/j8/k8fdfz056s94hb0sgdtbrk8h0000gn/T/codex-clipboard-70d8fcd1-8d8c-4357-a554-a392feb30268.png`
- Source pixels: 3560 × 1980 at the captured macOS/Obsidian desktop density.
- Implementation: `http://127.0.0.1:3000/research`
- Implementation screenshot: `/tmp/trading-partner-sidepanels-desktop-final.png`
- Implementation pixels: 2033 × 1145 from a 2048 × 1153 CSS viewport; browser scrollbar/chrome account for the capture delta.
- Compact screenshot: `/tmp/trading-partner-sidepanels-compact.png`, 900 × 900 CSS viewport.
- Full-view normalized comparison: `/tmp/sidepanels-reference-comparison.png`; the source was proportionally scaled to the implementation height before horizontal comparison.
- Focused controls comparison: `/tmp/sidepanels-focused-comparison.png`; the source and implementation top regions were normalized to 2032 × 228 before vertical comparison.
- State: light theme, Research workspace, both desktop side panels open. The compact capture shows the Agent overlay open.

## Full-view comparison

The Console preserves the reference's main composition: persistent left navigation, central working document, and a right Agent panel with a fixed composer. The Agent panel remains visually secondary to the workspace, uses a clear divider, and independently scrolls. Unlike a skin clone, it retains Trading Partner typography, palette, spacing, and dense investment-data components.

## Focused controls comparison

The top-region comparison shows equivalent left/right panel affordances in the central workspace header. Both buttons use familiar panel icons, provide open/closed states, and remain available after a panel vacates its width. The right panel maintains the reference hierarchy of compact status/actions, transcript space, and bottom composer.

## Required fidelity surfaces

- Fonts and typography: Console serif headings, sans-serif content, and mono operational metadata are preserved. Panel labels remain readable at the compact sizes already used by the product.
- Spacing and layout rhythm: desktop tracks are 248px / fluid / 340px; the right track becomes 0px when hidden. At ≤1100px both panels become bounded overlay drawers with a dimmed backdrop.
- Colors and tokens: all controls use existing Console line, panel, green, hover, shadow, and focus tokens. Contrast and focus rings remain consistent with the product.
- Image and icon fidelity: the existing Trading Partner logo is retained. Panel controls use the project's installed icon library; no placeholder or handcrafted icon assets were introduced.
- Copy and content: controls clearly say Open/Close navigation and Open/Close Agent. Tooltips expose shortcuts without adding persistent visual noise.

## Interaction verification

- Left and right panel toggles work independently on desktop.
- Hidden Agent panel has computed width 0px, opacity 0, and visibility hidden, so the workspace receives the full width.
- State persists through local storage and is applied before paint.
- Keyboard shortcuts: Cmd/Ctrl+Shift+L toggles navigation; Cmd/Ctrl+Shift+A toggles Agent.
- At ≤1100px only one overlay is active at a time; clicking the backdrop or pressing Escape closes it and restores body scrolling.
- 900 × 900 compact Agent overlay visually checked; navigation remains off-canvas beneath it.
- Browser console errors checked: none.

## Comparison history

1. P2 — the first browser capture was served from a stale Next development cache and still showed the old 54px collapsed Agent rail. The generated cache was moved aside and the development server restarted. Post-fix evidence confirmed the hidden rail computes to width 0px and the central workspace expands fully.
2. Post-fix desktop and compact captures found no remaining P0/P1/P2 mismatch in the requested side-panel toggle behavior.

## Findings

- P0: none.
- P1: none.
- P2: none.
- P3: the Console intentionally keeps its own visual language instead of copying Obsidian's native window chrome.

final result: passed

---

# Design QA — Portfolio disclosures

## Evidence

- Source visual truth: `/tmp/portfolio-collapse-audit-before.png`.
- Source pixels: 1265 × 13578 full-page Activity capture. The first 1265 × 889 viewport region was used for normalized comparison because the source page repeated its fixed shell during full-page capture.
- Implemented Holdings screenshot: `/tmp/portfolio-collapse-holdings-after.png`, 628 × 868.
- Implemented Activity screenshot: `/tmp/portfolio-collapse-activity-after.png`, 643 × 889.
- Normalized comparison: `/tmp/portfolio-collapse-comparison.png`, 1286 × 889. The source first viewport was scaled to 643px wide and padded to the 643 × 889 implementation viewport before horizontal comparison.
- State: light theme; Agent and navigation panels closed; Activity before/after comparison plus Holdings first-account-open state.

## Full-view comparison

The before/after view preserves the Portfolio shell, typography, tokens, tabs, toolbar, data, and card widths. The implementation changes only disclosure hierarchy: Activity now presents two compact, clearly labelled section summaries instead of beginning one uninterrupted ledger, while counts and coverage status remain visible above the fold.

## Focused comparison

The Holdings implementation screenshot is the focused account-control evidence. Account #1 remains fully readable and expanded; Account #2 and Account #3 compress to one header row that preserves identity, broker suffix, position count, state badge, and chevron. This region was readable at native capture density, so no additional crop was needed.

## Required fidelity surfaces

- Fonts and typography: existing Georgia display headings, sans-serif body copy, and monospace operational metadata remain unchanged. Disclosure counts use the existing operational hierarchy.
- Spacing and layout rhythm: headers retain the card grid and padding, while closed sections remove only their bodies. No horizontal overflow or clipped account header was observed at the captured compact viewport.
- Colors and visual tokens: existing panel, line, hover, active-border, green, muted, and focus tokens are reused.
- Image quality and asset fidelity: the changed surface contains no raster imagery or custom illustration. Chevrons use the installed Lucide icon library; no handcrafted SVG, text glyph, or CSS-drawn icon was added.
- Copy and content: account/provider identity, durable record counts, coverage status, warnings, and financial facts are unchanged. Collapsed states add only factual position/record/receipt counts.

## Interaction verification

- Account #1 starts expanded; later accounts start collapsed.
- Clicking Account #2's header changes `aria-expanded` and reveals the correct four positions.
- Transaction history and Activity coverage start collapsed and expose their counts/status in the header.
- Clicking Transaction history expands all durable records.
- Disclosure controls are native buttons with `aria-controls`; focus-visible styles are defined.
- Browser console error/warning log checked after interaction: none.
- ESLint, TypeScript, and the production Next.js build passed.

## Comparison history

1. P1 — the source Activity page placed 175 transaction rows before 21 coverage receipts with no section-level shortcut, making coverage hard to discover and producing an extreme page length.
2. P2 — all Holdings accounts expanded at once, so later accounts and exposure required unnecessary scrolling.
3. The implementation added density-boundary disclosures, retained summary facts in every closed header, and used the existing visual system.
4. Post-fix Holdings and Activity captures found no remaining P0/P1/P2 issue in the requested expand/collapse behavior.

## Findings

- P0: none.
- P1: none.
- P2: none.
- P3: a dedicated VoiceOver announcement pass could further verify assistive-technology wording; DOM semantics and focus states are already present.

final result: passed

---

# Design QA — Agent configured-model selector

## Evidence

- Source visual truth: `/var/folders/j8/k8fdfz056s94hb0sgdtbrk8h0000gn/T/codex-clipboard-9dc86801-0f0f-4642-9c9a-a6a6313d2292.png`.
- Source pixels: 1400 × 182.
- Implementation: `http://localhost:3000/portfolio`, shared Agent rail open.
- Implementation screenshot: `/tmp/trading-partner-agent-model-selector.png`, 643 × 889.
- Normalized comparison: `/tmp/trading-partner-agent-model-comparison.png`, 700 × 252. The source composer was scaled to 700px wide; the implementation composer was cropped at 359 × 126 and padded to the same comparison width.
- State: light theme, Portfolio workspace, Agent rail open, empty conversation, `deepseek-v4-flash` selected.

## Full-view comparison

The Codex reference is adapted to the Console's narrower 360px Agent rail rather than copied at desktop-composer width. Both preserve the same hierarchy: a prominent freeform message area, a low-distraction model selector at bottom left, and a circular send control at bottom right inside one rounded surface. The Console keeps its existing product shell and tokens while making the configured model a first-class per-turn choice.

## Focused comparison

The normalized crop confirms the requested relationship at comparable scale. The model control remains readable without competing with the prompt, the footer controls align to one baseline, and the send button has the same compact circular emphasis as the reference. The selected value is an actual configured model, not illustrative copy.

## Required fidelity surfaces

- Fonts and typography: the Console's established sans-serif UI typography is retained instead of imitating the Codex application font; the prompt/model hierarchy and compact control sizing match the reference intent.
- Spacing and layout rhythm: the composer is one rounded frame with an expandable text region and a compact footer. It fits the fixed Agent rail without clipping or horizontal scrolling.
- Colors and tokens: existing Console panel, ink, muted, line, green, hover, and focus tokens are used so the Codex interaction pattern remains native to Trading Partner.
- Image and icon fidelity: controls use the installed Lucide icon library. No placeholder, handcrafted SVG, CSS-drawn asset, or approximate image was introduced.
- Copy and content: the selector exposes only the runtime's configured catalog: `qwen3.8-max` and `deepseek-v4-flash`.

## Interaction verification

- Both configured models are visible in the selector; Bailian/Qwen remains the server default.
- Selecting `deepseek-v4-flash` updates the request's `model_id`, and the backend validates and routes the turn to that exact configured provider.
- The selected model persists across browser reloads.
- Unknown model IDs are rejected before the user message is appended.
- Model status metadata is secret-safe and does not expose API keys or endpoint credentials.
- Browser console errors checked: none.

## Comparison history

1. The initial implementation had one P2 control-fidelity issue: the native select arrow and Lucide chevron appeared together and were vertically misaligned.
2. The native appearance was disabled and the installed chevron was centered explicitly within the control.
3. The post-fix comparison found no remaining P0/P1/P2 mismatch in the requested composer hierarchy or model-selection interaction.

## Findings

- P0: none.
- P1: none.
- P2: none.
- P3: the independent full-page Chat composer has not been restyled; the requested shared workbench Agent rail is complete and functional.

final result: passed

---

# Design QA — Portfolio account identity hierarchy

## Evidence

- Source visual truth: `/tmp/trading-partner-portfolio-before.png`.
- Implementation screenshot: `/tmp/trading-partner-portfolio-after.png`.
- Normalized comparison: `/tmp/trading-partner-portfolio-comparison.png`.
- Source pixels: 628 × 868; implementation pixels: 628 × 868; comparison pixels: 1256 × 868.
- CSS viewport and density: the same visible Codex in-app browser viewport and device density were used before and after; no density normalization was required.
- State: light theme, Portfolio → Holdings, durable account data loaded, navigation and Agent panels collapsed.

## Full-view comparison

The before/after comparison keeps the existing Portfolio shell, card geometry, tabs, controls, data density, colors, and holdings content unchanged. The account card now gives the human-facing sequence the dominant heading (`Account #1`) and moves the broker plus six-character reference suffix to a smaller source line (`MOOMOO · 90d3b7`). The full internal account reference and repeated provider kicker no longer compete with the account identity.

## Focused comparison

A separate crop was not needed: the requested account header is fully legible in the normalized full-view comparison and occupies the central comparison region at native density. The same account identity component was also inspected in the Activity card state, where transaction rows preserve the stable account number and broker/suffix order.

## Required fidelity surfaces

- Fonts and typography: the existing Console serif display heading remains the primary account label; mono operational text remains secondary. Weight, size, and letter spacing now express the requested hierarchy without introducing a new font.
- Spacing and layout rhythm: the account identity is isolated in a compact header band with an existing-token divider and a restrained left accent. Fact-grid spacing and holdings density are unchanged.
- Colors and visual tokens: only existing `--ink`, `--ink-soft`, `--muted`, `--green`, `--panel`, `--line`, and `--active-border` tokens are used.
- Image quality and asset fidelity: the changed region contains no raster imagery, logos, illustrations, or custom icons; no placeholder or CSS-drawn asset was introduced.
- Copy and content: account labels now follow `Account #N` above `PROVIDER · suffix`. Internal durable data, warning copy, balances, timestamps, and status badges are unchanged.

## Interaction verification

- Holdings renders stable `Account #1`, `Account #2`, and `Account #3` identities from the durable account order.
- Activity transactions reuse those same account numbers instead of numbering rows.
- Activity tab navigation remains functional and the account source remains readable in compact cards.
- Production build and TypeScript compilation passed.
- ESLint passed with zero warnings.
- Browser console errors checked: none.

## Comparison history

1. The source capture showed one P1 hierarchy issue: broker, account number, and suffix were merged into one dominant line, while the full internal reference was repeated beneath it.
2. The implementation split the identity into a primary account sequence and a secondary broker/suffix line, removed the duplicate internal reference from the card header, and reused the same identity component across Holdings, Activity, Performance, and coverage receipts.
3. Post-fix Holdings and Activity captures found no remaining P0/P1/P2 issue in the requested account identity order or responsive card layout.

## Findings

- P0: none.
- P1: none.
- P2: none.
- P3: Performance account identity was verified by shared component and production build; its populated result state was not triggered because doing so was outside the requested visual change.

final result: passed
