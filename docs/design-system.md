# CardVault v2 — design system

The look the owner chose on 2026-09-16: Glass Premium with the Refined ideas, no
warm accent. Every screen is built from this. If a screen needs something
that is not here, add it here first.

Mockups and the generator that produced them: `design/cardvault-look/`
(`build.py`, `build_variants.py`). Canvas: https://claude.ai/artifact/E49JrzabymKCRyEWgWkiAN

## Principles

1. **One focal surface per screen.** The dashboard hero, the selected card's
   drawer, the leaving card on a deal. Everything else sits on the canvas,
   separated by type and space, never by boxes.
2. **No grid of equal-weight containers.** Zones differ in size and emphasis.
   The dominant zone is obvious at squint distance.
3. **Top navigation, not a sidebar.** Five tabs, a More menu, the global
   search in the bar, one primary action.
4. **Cards first.** Anywhere a deal is summarised, lead with the cards that
   moved, then who and when. the owner remembers the cards.
5. **Red means one thing.** `--neg` only on a realized loss and destructive
   confirms. A paper loss is a plain negative number. PSA's red is a grader
   chip and nothing else. Attention states are amber.
6. **Only a blank is derived.** Typed numbers are never restyled to look
   derived and never rewritten.
7. **No decoration.** No gradients, no glows, no `transition: all`. Emphasis
   comes from size, weight, contrast and space.

## Tokens

### Colour — dark (default)

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0b0a09` | canvas |
| `--bg2` | `rgba(11,10,9,.6)` | top bar |
| `--surface` | `rgba(255,255,255,.05)` | the focal surface |
| `--surface-hi` | `rgba(255,255,255,.07)` | raised surface, drawer |
| `--border` | `rgba(255,255,255,.08)` | hairlines |
| `--border-hi` | `rgba(255,255,255,.16)` | emphasised hairline, focused input |
| `--text` | `#f3efe6` | ivory type |
| `--muted` | `#a29d92` | secondary text, labels |
| `--faint` | `#67635a` | decorative only: chevrons, separators, close icons. Never data. |
| `--accent` | `#f3efe6` | the chart line, the capital bar, a selected row, the primary button |
| `--pos` | `#3ddc84` | gains |
| `--neg` | `#ff5c6c` | realized loss, destructive |
| `--warn` | `#e8a94f` | stale, unpriced, attention |
| `--input` | `rgba(255,255,255,.04)` | inputs, ghost buttons, filter pills |
| `--row-hover` | `rgba(255,255,255,.035)` | |
| `--row-sel` | `rgba(243,239,230,.08)` | selected row, plus 2px inset accent bar |

Grader identity (chips only): PSA `#ff6b62`, BGS `#5aa2ff`, TAG `#4fd1b8`,
CGC `#c98bf0`, PC `#e0a52a`. Fill at 12% alpha, border at 34%, text solid.

### Colour — light

`--bg #f6f5f1`, `--bg2 rgba(246,245,241,.85)`, `--surface #ffffff`,
`--border rgba(20,20,18,.08)`, `--border-hi rgba(20,20,18,.16)`,
`--text #141412`, `--muted #6b6a64`, `--faint #a19f97`, `--accent #141412`,
`--pos #1a9e5c`, `--neg #d6404c`, `--warn #b07a12`, `--input #f3f2ee`.
Graders: PSA `#c8362e`, BGS `#2563eb`, TAG `#0f8f7a`, CGC `#7c3aed`.
Applied with `[data-theme="light"]` on `<html>`. Dark is the default; the
sun/moon button in the top bar flips it and the choice is saved in
`localStorage` (`cardvault_theme`) and applied before first paint. Values
that are not "a colour" in the system but still flip with the theme are
tokens too: `--pop` (menus, dialogs), `--on-accent` (text on the primary
button), `--btn-hover`, `--spec` / `--spec-lo` (specular edge), `--dots`
(canvas grid), `--backdrop`. Nothing in a stylesheet or template should
name a dark-only hex; light grader chips are overridden explicitly.

### Surfaces

Surfaces are distinguished by a hairline border and a specular top edge, not
by fill. `box-shadow: inset 0 1px 0 rgba(255,255,255,.10), 0 8px 32px
rgba(0,0,0,.40)`. Raised: `inset 0 1px 0 rgba(255,255,255,.14), 0 16px 48px
rgba(0,0,0,.55)`. The canvas carries a 24px dot grid at 2.8% white.

### Type

| Role | Face | Size / weight |
|---|---|---|
| Display figures, page titles, section titles | Playfair Display | 500; hero 72, page title 30, section 21, stat 24 |
| Interface | Geist | 400; 14 body, 13.5 table, 12 labels, 11 caps eyebrows |
| Numbers in tables, inputs, amounts | Geist Mono | 400/500, `tabular-nums` |

Scale: 11 · 12 · 13.5 · 14 · 15 · 21 · 24 · 30 · 72. Line height 1.5 body,
1.1 display. Letter-spacing −.005em on display, +.14em on uppercase eyebrows.
Fonts load from Google Fonts with system fallbacks; offline at a show the
fallbacks must still read well (Georgia / system-ui / Menlo).

### Radius grammar

| Tier | Value | Use |
|---|---|---|
| shell | 24 | hero |
| surface | 18 | focal surface, drawer |
| inset | 14 | blocks inside a surface |
| control | 12 | reserved; controls are pills in this look |
| tight | 8 | kbd hints, tiny plates |
| pill | 999 | buttons, segmented controls, inputs, chips, avatars |

Nested surfaces step down. Nothing inside a surface is rounder than the
surface.

### Spacing

4px base. 4 · 8 · 12 · 16 · 24 · 32 · 40 · 48. Gap inside a group is
visibly smaller than the gap between groups. Section gutter 40, section
header padding-bottom 14, row padding 12–13.

### Motion

Entering: fade + 8px rise + scale from .985, 600ms, `cubic-bezier(.16,1,.3,1)`,
staggered 40ms. Hover: colour and opacity only, 150ms. Nothing loops.
`prefers-reduced-motion` disables the reveal.

## Components

- **Top bar**: 60px, `--bg2`, hairline bottom. Wordmark (ivory plate + name),
  tabs Dashboard · Collection · Deals · Grading · Reports · More, search
  (320px, ⌘K), New deal (primary), avatar.
- **Section header**: display 21px title, muted subtitle, right-aligned link,
  hairline under.
- **Hero**: shell radius, raised surface. Eyebrow in uppercase 11px, figure
  72px, delta pill, counts. Chart on the right half with y gridlines, y
  labels, month labels and a hover crosshair. Figure strip along the bottom
  above a hairline: label 12px muted, value 24px display.
- **Feed row**: grader chip (58px fixed) · card names (out → in) · who and
  when (12px muted) · amounts in mono. 13px vertical padding, hairline between.
- **Stat block**: 24px display value, 12.5px muted caption, optional 6px bar.
- **Table**: 13.5px, hairline rows, 13px padding, mono numerals right-aligned,
  selected row tinted with a 2px inset accent bar. Row height 40–52.
- **Chips**: pill, 22px, 11px 600 caps. Grader colours as above. RAW is
  outline-only.
- **Pills**: pos / neg / warn / muted tints at 12% alpha; 24px.
- **Buttons**: pill, 36px. Primary = ivory on black. Ghost = input fill and
  hairline. Small = 30px.
- **Inputs**: pill, 36px, input fill, hairline; focused border `--border-hi`.
- **Segmented control**: pill track with pill thumb.
- **Toast, confirm dialog, modal**: existing components restyled with the
  tokens; kept as built.

## Do not change

The collection keyboard model, the deal picker, the one-price deal model and
its chips, the "As entered" log, void behind confirm, stock check tap-to-check,
raw bulk bar, PSA cert rule, print sheets (unstyled, uncoloured).
