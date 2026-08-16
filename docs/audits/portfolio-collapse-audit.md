# Portfolio disclosure audit

## Audit scope

- Surface: Trading Partner Console → Portfolio.
- User goal: scan account and portfolio state quickly, then expand only the dense detail needed for the current task.
- Accessibility target: every disclosure must expose a real button, an `aria-expanded` state, a labelled content region, visible focus, and a minimum practical pointer target.

## Evidence and steps

### Step 1 — Activity before disclosure controls

- Evidence: `/tmp/portfolio-collapse-audit-before.png`.
- Health before change: needs improvement.
- Strength: transactions and coverage were complete and readable.
- UX risk: 175 transaction rows and 21 diagnostic receipts shared one uninterrupted page, pushing the second section far below the entry point.
- Accessibility risk: no shortcut existed for keyboard or magnification users to skip the dense ledger and reach coverage.

### Step 2 — Holdings account disclosure

- Evidence: `/tmp/portfolio-collapse-holdings-after.png`.
- Health after change: healthy.
- Result: Account #1 opens by default; later accounts remain compact. Every header is the disclosure button and keeps account identity, provider suffix, position count, quality state, and chevron visible.
- Interaction checked: Account #2 was expanded from its header and exposed the correct facts and four position records.

### Step 3 — Activity section disclosure

- Evidence: `/tmp/portfolio-collapse-activity-after.png`.
- Health after change: healthy.
- Result: Activity opens with Transaction history and Activity coverage as two compact summaries. Counts and the coverage status remain visible while their large bodies are closed.
- Interaction checked: Transaction history was expanded and exposed all durable records. The controls are native buttons with heading semantics, `aria-expanded`, `aria-controls`, hover, focus-visible, and open/closed chevrons.

## Overall findings

### Strengths

- The existing Portfolio tabs already provide the correct first level of navigation.
- Exposure, Performance controls, and Risk forms are short enough to remain directly visible.
- Performance and Risk already use local drill-down disclosures for instrument events, policy editing, Position Sizing, and what-if inputs.

### Resolved UX risks

- Account cards no longer require scrolling through every broker before reaching exposure.
- Activity no longer renders hundreds of rows before the user can see coverage exists.
- Collapsed summaries retain enough context to decide whether opening the section is useful.

### Evidence limits

- Screenshot evidence cannot prove full screen-reader support. DOM semantics, focus styles, pointer activation, and expanded states were inspected; a dedicated VoiceOver pass was not run.
- Performance populated-result disclosures were reviewed from implementation structure and existing local drill-down behavior, not regenerated with a new attribution request because that would be outside this visual task.

## Recommendation

Keep disclosure at these three density boundaries. Do not add it to Exposure, Performance inputs, Risk policy summary, or individual transaction/coverage rows unless their content grows materially; extra nesting there would slow scanning more than it saves space.
