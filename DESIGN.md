# AI News Radar — Wired Editorial Design

## Design Philosophy

White canvas, black ink, serif display, square edges. Inspired by Wired magazine's editorial typography and Bloomberg Terminal's data density.

## Color Tokens

| Token | Value | Usage |
|-------|-------|-------|
| `--bg` | `#ffffff` | Page background |
| `--ink` | `#000000` | Primary text, headings |
| `--muted` | `#757575` | Secondary text, labels |
| `--line` | `#e0e0e0` | Dividers, borders |
| `--link` | `#057dbc` | Hyperlinks |
| `--good` | `#1a7a3a` | Success indicators |
| `--warn` | `#9a6700` | Warning indicators |
| `--bad` | `#b42318` | Error indicators |
| `--radius` | `0px` | Square corners everywhere |

## Typography

| Role | Font | Weight | Size |
|------|------|--------|------|
| Display | Playfair Display | 400 | 48px hero, 32px stats, 18px cards |
| Body | Source Serif 4 | 400 | 16px body, 14px subtitles |
| Sans | Inter | 400/700 | 13px metadata, 11px labels |
| Mono | JetBrains Mono | 400 | 12px timestamps, 11px data |

CJK fallback: Noto Serif SC (serif), Noto Sans SC (sans).

## Layout

- Max width: 1080px, centered
- Hero: 2px black bottom border, magazine masthead style
- Stats: horizontal strip, no borders, just spacing
- News cards: no card borders, 1px hairline dividers between items
- Site group headers: 2px black bottom border, sticky on scroll
- Sidebar: right column (300px) on desktop, top on mobile

## Component Patterns

- **Category badges**: bordered pills, 0px radius, color by tone (official=black, newsletter=blue, builders=gray)
- **Coverage dots**: 6px colored circles (green/yellow/red) inline with labels
- **Mode switch**: inline button group with 1px black border
- **Source health**: metric cards with colored values (ok=green, warn=yellow, bad=red)

## Responsive Breakpoints

- 860px: single column, sidebar moves to top
- 560px: smaller title (28px), compact controls

## What We Don't Use

- No rounded corners
- No gradients or shadows
- No card backgrounds (transparent by default)
- No filled category badges (bordered only)
- No sans-serif display text
