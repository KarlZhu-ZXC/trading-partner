# Trading Partner brand mark

## Primary asset

- File: `public/assets/trading-partner-brand/logo.png`
- Format: square 256 × 256 PNG
- Source direction: the repository README hero artwork in `docs/assets/readme/hero.png`

## Visual idea

The mark combines a conversation bubble with a three-point rising market path. A
cyan-to-blue-to-violet orbit suggests continuous monitoring and connects it to the
README hero's market-network visual language. The dark navy field keeps the mark
legible in both light and dark console themes.

## Usage

- Use the complete square mark; do not add an enclosing border or another tile.
- Keep it at least 32 × 32 px in navigation.
- Do not recolor the symbol, crop the orbit, or place text inside the mark.
- Pair with the `Trading Partner` wordmark in expanded navigation; use the mark
  alone when navigation is collapsed.

## Console visual system

Light slate surfaces are the default; navy surfaces are the optional dark theme.
Cyan emphasis aligns both with the existing mark.
`app/tokens.css` is the source of truth for both themes, typography and spacing.
See `../docs/guide/console-design-system.md` for component ownership and migration.
