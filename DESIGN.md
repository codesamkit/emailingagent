# DESIGN.md — Valence

Visual system for the Valence review UI (`api/static/index.html`). Register: **product** (see `PRODUCT.md`). Direction: **native macOS app** — the page should read like Apple Mail / Things, not like a website. The atom identity (logo, score rings, unread "charge" dot) rides on top of that native base.

## Theme

Auto dark/light via `prefers-color-scheme`, manual override on `<html data-theme="dark|light">` persisted in `localStorage("valence-theme")`, cycled by the toolbar button (auto → dark → light).

## Color

macOS system palette, hex (Apple system colors, AA-adjusted where used as text). Strategy: **Restrained** — neutral Apple grays + systemBlue as the one accent; systemRed reserved for `urgent`/errors, systemGreen for success/outline-ready.

| Token | Light | Dark | Role |
|---|---|---|---|
| `--bg` | `#f5f5f7` | `#161618` | window chrome (toolbar) |
| `--surface` | `#ffffff` | `#1e1e20` | content panes |
| `--fill` | `rgba(120,120,128,.12)` | `rgba(120,120,128,.26)` | control fills (macOS quaternary) |
| `--fill-2` | `rgba(120,120,128,.22)` | `rgba(120,120,128,.38)` | control hover |
| `--line` | `rgba(0,0,0,.08)` | `rgba(255,255,255,.1)` | hairlines |
| `--ink` | `#1d1d1f` | `#f5f5f7` | primary text |
| `--ink-soft` | `#5d5d63` | `#a1a1a6` | secondary text (AA) |
| `--ink-faint` | `#86868b` | `#7c7c80` | timestamps, placeholders (large/short only) |
| `--accent` | `#0071e3` | `#409cff` | systemBlue as text/links (AA) |
| `--accent-vivid` | `#007aff` | `#0a84ff` | systemBlue fills & graphics |
| `--urgent` | `#d70015` | `#ff453a` | urgent level, errors |
| `--ok` | `#1f8a3b` | `#30d158` | success, outline-ready |

Tints via `color-mix(in srgb, …)` — **never `in oklch` against these neutral grays**: an achromatic endpoint drags the hue toward 0 (pink).

## Typography

**Native system stack** (`-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, …`) for everything — no webfonts; the tool should feel installed, not generated. Body 13px (macOS standard). Numbers use `font-variant-numeric: tabular-nums` wherever they align (scores, counts, timestamps, slots). Mono (`ui-monospace, SF Mono, …`) only where content is genuinely monospaced: email addresses, CLI commands, kbd hints. No uppercase-tracked labels; tags and chips are sentence-case fills.

## macOS idiom (the rules that make it read native)

- **Borderless fill controls.** Buttons, chips, search, textareas use `--fill` backgrounds with no 1px borders; hover = `--fill-2`. Primary action = solid `--accent-vivid` with white text.
- **Flat list, not cards.** Queue rows are transparent, separated by inset hairlines (`left: 64px`, clearing the score ring); hover = `--fill`; selection = rounded 9px rect tinted `accent-vivid 14%`. No row borders, no shadows.
- **Translucent toolbar.** Sticky header at 78% `--bg` with `backdrop-filter: blur(20px) saturate(1.8)`, hairline bottom.
- **Unread = blue dot** before the sender, exactly like Mail.app (7px, `--accent-vivid`, no glow).
- Radii: 9px list selection, 7px controls, 999px chips/tags.

## Atom motif — where it's allowed

Only where it encodes something:

1. **Logo**: nucleus + two crossed elliptical orbits (±60°), one electron each, orbiting via SMIL `animateMotion` (11s/17s); reduced motion swaps to static dots.
2. **Score ring**: importance as an SVG orbital arc — sweep = score/100, terminal dot = the electron's position, stroke = level (urgent red / high blue / medium `ink-soft` / low `ink-faint`). Level label always printed beneath (color is never the only signal).
3. **Unread charge**: the Mail-style blue dot doubles as the "charged" electron.

Nothing else — no particle backgrounds, no glows, no HUD decoration.

## Motion

140–220ms, `cubic-bezier(0.22, 1, 0.36, 1)`. State only: hover/selection fades, detail content fade + 3px rise on select, skeleton shimmer while the queue first loads. The logo orbit is the one ambient exception; paused under `prefers-reduced-motion`.

## Detail pane order

Subject/from/tags + big ring → Summary → **Message** (full original plain-text body from `raw_email`, pre-wrap) → Why this score → Calendar slots (when scheduling) → Reply outline (edit / expand, human-in-the-loop) → Draft preview.

## Assets

- `api/static/valence-logo.svg` — standalone lockup (mark + wordmark, wordmark inherits `currentColor`).
- Favicon: inline SVG data URI — mark on `#1e1e20`, systemBlue orbits.
