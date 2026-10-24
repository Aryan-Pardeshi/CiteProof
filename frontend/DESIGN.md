# CiteProof design system

Source: an adaptation of the unofficial Vercel-inspired `DESIGN.md` from the awesome-design-md collection (https://github.com/voltagent/awesome-design-md). It is not an official Vercel file. We keep its rules and drop its gaps (it has unresolved placeholders and no focus or hover tokens). The brand is **CiteProof** (capital C, capital P). The two run modes keep their names: **DeepSearch** and **Research Mode**.

Every page in `frontend/` reads its colors, type and spacing from `tokens.css`. Do not hard-code a hex value, font size or radius in a page file when a token exists.

## 1. Principles

1. Black and white first. Ink is the brand. There is no second brand color.
2. One loud thing per screen. The mesh gradient appears once, at hero scale, and nowhere else.
3. Borderless: no borders, separation comes from tone steps and soft shadows.
4. Sentence-case headlines with tight tracking. Never ALL CAPS except tiny mono labels.
5. Honest copy. Say "checked by a model", never "verified". Do not invent numbers, logos, quotes or customers. Every number on the page must come from this repo.

## 2. Color

Light mode is the reference. Dark mode keeps the same roles with inverted values. The site toggles dark mode with the existing `light-mode` class on `<html>` (dark is the default; `html.light-mode` is light). `tokens.css` defines both.

| Role | Token | Light | Dark |
|---|---|---|---|
| Page background | `--cp-canvas` | `#ffffff` | `#000000` |
| Soft band | `--cp-canvas-soft` | `#fafafa` | `#0a0a0a` |
| Softer band | `--cp-canvas-soft-2` | `#f5f5f5` | `#111111` |
| Ink (headings, primary button) | `--cp-ink` | `#171717` | `#ededed` |
| Text on ink | `--cp-on-ink` | `#ffffff` | `#000000` |
| Body text | `--cp-body` | `#4d4d4d` | `#a1a1a1` |
| Muted text | `--cp-muted` | `#666666` | `#8f8f8f` |
| Hairline | `--cp-hairline` | `#ebebeb` | `#2e2e2e` |
| Link | `--cp-link` | `#0070f3` | `#52a8ff` |
| Error | `--cp-error` | `#ee0000` | `#ff6166` |
| Warning | `--cp-warning` | `#c50000` | `#ff6166` |
| Success | `--cp-success` | `#0062d6` | `#52a8ff` |

Muted text on white must stay at or above 4.5:1 contrast. `#888888` fails, so we use `#666666`.

Three-color rule: the site uses only neutrals (white, black, grays, ink), blue and red. Blue: `#0070f3` (light), `#0062d6` (deep, for text on white), `#52a8ff` (bright, dark mode). Red: `#ee0000` (light), `#c50000` (deep, for text on white), `#ff6166` (bright, dark mode). Success uses blue. Warning uses red. Partial status uses gray (`--cp-muted`).

Hero gradient (used once, in the hero background only, soft and blurred): `--cp-grad-a` blue to lighter blue, `--cp-grad-b` blue to red, `--cp-grad-c` red to lighter red. Gradients go only between blue and red (or white). Never put a gradient behind body text, on a button, or in a small icon.

## 3. Typography

- Sans: **Geist** (weights 400, 500, 600). 600 is the maximum for display text.
- Mono: **Geist Mono** for code, the PRISMA numbers, DOIs, eyebrows and small labels.
- Fallbacks: `ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` and `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`.
- Load from Google Fonts with `display=swap`. The site CSP already allows `fonts.googleapis.com` and `fonts.gstatic.com`.

| Style | Size / line | Weight | Tracking |
|---|---|---|---|
| Display XL (hero h1) | 56 / 60 (40 / 44 on phones) | 600 | -2.4px (-1.6px on phones) |
| Display L (section h2) | 40 / 44 (30 / 36 on phones) | 600 | -1.28px |
| Heading (card h3) | 20 / 28 | 600 | -0.4px |
| Body L | 18 / 28 | 400 | 0 |
| Body | 16 / 24 | 400 | 0 |
| Small | 14 / 20 | 400 or 500 | 0 |
| Eyebrow / label (mono) | 12 / 16 | 500 | 0.04em, uppercase allowed |

## 4. Spacing and layout

- Base unit 4px. Scale: 4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192.
- Section vertical padding: 96px desktop, 64px phone. Hero top padding: 128px desktop, 96px phone.
- Content max width 1200px, centered, 24px side padding (16px on phones).
- Breakpoints: 600, 960, 1200. Design phone first. Nothing scrolls sideways at 360px.
- Nav bar: 64px tall, sticky, borderless, background is canvas at 80% opacity with a backdrop blur.

## 5. Radius and elevation

- Marketing buttons: pill, `--cp-radius-pill` 100px. In-app buttons and inputs: 6px. Do not mix the two on one screen.
- Cards: 12px. Small tags: 6px.
- Card elevation: no borders or rings; separation comes from tone steps and soft shadows: `box-shadow: 0 8px 30px rgba(0,0,0,.35)` in dark mode and `0 8px 30px rgba(0,0,0,.06)` in light mode. On hover add 4px of lift with a 150ms ease.
- Modal: no borders or rings, `0 16px 48px rgba(0,0,0,.6)` in dark mode and `0 16px 48px rgba(0,0,0,.18)` in light mode.

## 6. Components

- **Primary button**: ink background, on-ink text, pill, 40px tall (48px large), weight 500. Hover: ink at 88% opacity. No gradient, no glow.
- **Secondary button**: canvas background, ink text, 1px hairline ring, same shape. Hover: canvas-soft background.
- **Ghost / link button**: ink text, underline on hover, link blue only for inline links in body copy.
- **Nav link**: 14px, body color, ink on hover or when current.
- **Card (marketing)**: canvas background, 12px radius, card elevation, 24px padding.
- **Code or output window**: canvas-soft background, hairline ring, 12px radius, a thin top bar with three neutral dots, Geist Mono 13px.
- **Form input**: 40px tall, 6px radius, 1px hairline ring, ink ring on focus (see below).
- **Tag**: mono 12px, canvas-soft-2 background, 6px radius, 4px 8px padding.
- **Dark showcase band**: one per page at most. Black background, `#ededed` ink, hairlines `#2e2e2e`. Used for the example output.
- **Footer**: canvas-soft background, 1px top hairline, four link columns, mono legal line.

## 7. States and motion (gaps in the source file, filled here)

- Focus: `outline: 2px solid var(--cp-ink); outline-offset: 2px` on every interactive element. Never remove an outline without a replacement.
- Hover: 150ms ease. Active: 98% scale on buttons.
- Disabled: 40% opacity, `cursor: not-allowed`.
- Reveal on scroll: fade and 8px rise, 300ms, once. Respect `prefers-reduced-motion: reduce` (no movement, no autoplay).
- Skeleton: canvas-soft-2 block with a 1.2s opacity pulse (static under reduced motion).

## 8. Do and do not

Do: keep the primary call to action in ink; use mono only for technical labels; use real product output as the visual (the PRISMA numbers and evidence table from `assets/examples.json`); keep one h1 per page; give every image alt text; keep body contrast at 4.5:1 or better.

Do not: add a second accent color; use weight above 600 for display text; shrink the gradient into an icon or button; use emoji as icons (use the pinned Lucide set); use stock photos of people; claim "unlimited"; claim a result no one has measured.

## 9. Page map for the rebuild

1. Landing (`index.html`): nav, hero with gradient and product output, how it works (PRISMA flow), evidence and citation checks, the two modes, human review steps, the MCP connector for Claude Science, final call to action, footer.
2. App (`app.html`): the research workspace, styled by `components.css` and `app.css` on top of the same tokens.
