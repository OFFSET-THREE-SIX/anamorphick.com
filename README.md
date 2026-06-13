# Anamorphick — Build &amp; Deploy Handoff

A production build + deploy package for **anamorphick.com**, the photographic-studio
site designed in this project. Hand this folder to a developer (or Claude Code) to
stand the real site up.

---

## The stack (decided)

```
┌─ Cloudflare ─────────────────────────────────────────────┐
│  • DNS for anamorphick.com                                │
│  • Pages → hosts the Astro site (static, edge CDN)        │  ← SERVERLESS UI
│  • R2 / Images → serves photographs, auto-optimized       │
└───────────────┬──────────────────────────────────────────┘
                │  fetch content at BUILD time via Content API
┌───────────────▼─── DigitalOcean droplet ─────────────────┐
│  • Ghost (HEADLESS) — you write surveys + journal here    │  ← HEADLESS CMS
│  • MySQL 8                                                │
│  • nginx + Let's Encrypt → https://cms.anamorphick.com    │
└───────────────────────────────────────────────────────────┘

Presentations (the survey decks) ship as standalone interactive HTML — the
Astro site links/embeds them. Editions (prints) sell via Stripe Payment Links.
```

- **UI:** Astro, static-built, deployed to **Cloudflare Pages** (free, edge-fast, ships ~0 JS). React islands only where interactive (lightbox, search, cart).
- **Content:** **Ghost headless** on a DO droplet. The Astro build pulls posts through the **Ghost Content API** and bakes them into static pages. Re-deploy (a webhook from Ghost → Cloudflare) rebuilds on publish.
- **Images:** Cloudflare R2/Images, or Ghost's own media — either way fronted by Cloudflare's CDN. Astro's `astro:assets` handles local optimization.
- **Why this shape:** static edge delivery is the lightest/fastest/cheapest UI; Ghost gives you a real editor + native **members/newsletter**; the two are decoupled, so the front-end never depends on the droplet being up.

---

## About the design files (references, not production code)

The files at the **project root** are **high-fidelity design references built in HTML** —
they define the intended look, copy, and behavior. They are **not** meant to ship as-is.
The task is to **recreate them as Astro components** fed by Ghost, using the structure below.

| File | What it is |
|---|---|
| `index.html` | The homepage design — canonical look &amp; feel. Recreate as `src/pages/index.astro`. |
| `anamorphick.css` | All site styles. Move to `src/styles/anamorphick.css` (see note on the `@import`). |
| `anamorphick.js` | Sticky nav, slide-in menu, search overlay, reveal, newsletter. Port to small React islands / vanilla. |
| `Vernacular Strange Light - No. 03.html` | A **survey deck** — the reusable presentation template (No. 03). |
| `Vernacular Capitol Hill - No. 02.html` | Survey deck No. 02 (B&amp;W). |
| `Vernacular NW DC - Deck.html` | Survey deck No. 01 (color). |
| `deck-stage.js` | The deck engine (keyboard nav, scaling, PDF export). Ships with each deck unchanged. |
| `assets/` | Fonts CSS (`colors_and_type.css`), icons, logos, and all survey photography. |

**Fidelity: high.** Recreate pixel-for-pixel using the tokens in this doc.

> **CSS note:** `anamorphick.css` begins with `@import url('assets/colors_and_type.css')`.
> When you move it to `src/styles/`, either copy `colors_and_type.css` alongside it and fix
> the path, or paste the `:root` token block in directly. Tokens are listed below regardless.

---

## Repo structure to build

```
anamorphick/
├─ src/
│  ├─ pages/
│  │  ├─ index.astro                 # homepage (mirror index.html)
│  │  ├─ vernaculars/index.astro     # survey archive
│  │  ├─ vernaculars/[slug].astro    # one survey → links to its deck
│  │  ├─ journal/index.astro         # journal feed
│  │  ├─ journal/[slug].astro        # one post
│  │  ├─ editions/[slug].astro       # one print → Stripe link
│  │  └─ rental.astro                # The Bench — rentals & studio services
│  ├─ components/                    # ALL BUILT — faithful ports of index.html
│  │  ├─ Masthead.astro  Nav.astro  MenuSearch.astro
│  │  ├─ Hero.astro  LatestRail.astro  SurveyCard.astro  EssayStrip.astro
│  │  ├─ EditionCard.astro  JournalFeed.astro  Newsletter.astro  Footer.astro
│  ├─ layouts/Base.astro  SiteShell.astro
│  ├─ lib/ghost.ts                   # Content API client (provided)
│  └─ styles/anamorphick.css
├─ public/assets/…                   # photography (or move to R2)
├─ astro.config.mjs                  # provided
├─ package.json                      # provided
├─ .env.example                      # provided
└─ deploy/
   ├─ docker-compose.ghost.yml       # provided — Ghost + MySQL on DO
   └─ nginx-cms.conf                 # provided — TLS reverse proxy
```

**This is a complete static port, not just a scaffold.** Every homepage section is a
working Astro component; `index.astro` composes them and pulls surveys/editions from Ghost
with a **static fallback** so it builds even before Ghost is connected. `rental.astro` (The
Bench) is fully built as a static page. Detail pages (`vernaculars/[slug]`, `journal/[slug]`,
`editions/[slug]`) generate from Ghost via `getStaticPaths` and no-op cleanly when Ghost is
absent. Interactive chrome (menu, search, sticky nav, reveal, newsletter) reuses
the proven **vanilla** `public/scripts/anamorphick.js` — **no React**, keeping it lightest.

> The survey-archive and journal-archive *landing* pages (`vernaculars/index.astro`,
> `journal/index.astro`) are the one optional add — the homepage `#vernaculars` / `#journal`
> sections and the per-item `[slug]` pages cover the current nav. Add them when you want
> dedicated archive routes.

> **Assets are already bundled.** The survey photography lives in **`public/assets/`**
> (`strange-light/`, `no2/`, `img/`, plus the icons, mark, and footer flag), so
> `npm run build` works out of the box. To move photos to R2/Images later, swap the
> `/assets/...` paths for remote URLs. Photos that come from Ghost use remote
> `feature_image` URLs and need no local copy.

To run: `cp .env.example .env` → add your Ghost keys → `npm install` → `npm run dev`.

---

## Ghost content model (conventions)

Ghost core has no arbitrary custom fields, so encode structure with **internal tags** +
the **feature image** + the **post body**. Suggested convention:

**Surveys** — one Ghost **post** per survey, tagged `#survey`:
- Title → survey name (e.g. "Strange Light")
- Internal tags: `#no-03`, `#tone-color` or `#tone-bw`, `#place-dc`
- Feature image → the survey's lead frame
- Custom excerpt → the deck/sentence shown on the card
- Body → the frames as an image gallery card (caption per frame), **plus** a link to the
  standalone deck (store the deck URL in a `#deck:/decks/strange-light/` style internal tag,
  or as the first link in the post). The Astro `[slug].astro` reads these.

**Journal** — ordinary Ghost posts **without** `#survey`. Category = first public tag
(shown as the blue eyebrow). Author/date/feature image used directly.

**Editions** — two options:
1. **Lightweight:** a Ghost post tagged `#edition` with internal tags `#price-180`,
   `#ed-25`, `#size-16x24`, and `#stripe:<payment-link-url>`. Astro renders the card and
   the "Acquire" button → the Stripe link. (Recommended to start.)
2. **Full commerce:** Shopify "buy button" embed or Snipcart if you need inventory/variants.

Ghost can't process physical-goods payments itself — **Stripe Payment Links** are the
simplest bridge and need no server code.

**Bookings (The Bench)** — `rental.astro` is static. The buttons wire up as follows:
- **Camera gear → "Reserve a date"** funnels to **`#join`** (the free Ghost "Community"
  membership). Gear booking is intentionally gated: a free community sign-up comes first,
  then we send a reservation link (Lend Engine or a manual flow). The intro note states this.
- **Darkroom / Studio / Interviews** link to **Cal.com**: `https://cal.com/anamorphick/darkroom`,
  `.../studio`, `.../interview` (placeholders — swap for your real Cal.com event handles).
  Edit the `services[].book` URLs at the top of `src/pages/rental.astro`.

---

## Front-end data layer

`src/lib/ghost.ts` (in this folder) wraps `@tryghost/content-api`:

```ts
getSurveys()  // posts tagged #survey, newest first, tags included
getJournal()  // posts NOT tagged #survey
getEditions() // posts tagged #edition
getPost(slug) // single post by slug
```

`index.astro` calls these at build time and maps results into the design's components.
Set up a **Ghost → Cloudflare Pages deploy webhook** (Ghost Admin → Settings → Integrations →
custom integration → "Post published" webhook → Cloudflare Pages deploy hook URL) so the
static site rebuilds whenever you publish.

---

## Homepage spec (sections, top → bottom)

All within a **1200px** centered column (`.wrap`, `max-width: calc(100% - 56px)`), white page.

1. **Top utility bar** — left "Commissions & licensing", center **wordmark** (Anton,
   uppercase, the "a" in `--red`), tagline "Photographic Studio · Washington DC", right
   "Subscribe · Editions".
2. **Sticky nav** — menu icon · The Vernaculars / Strange Light / Editions / Journal / Studio
   (Anton 18px, uppercase) · search icon. Gains `--shadow-header` after 140px scroll.
3. **Hero** — `border: 4px solid #000`; full-bleed featured image (`height: min(60vh,560px)`),
   floating red `New Series · No. 03` tag top-left, "Washington, DC · MMXXV" bottom-right;
   below: blue eyebrow, Anton headline `clamp(42–78px)`, Georgia deck, byline (blue author,
   red dot). Featured = **Strange Light**.
4. **The Latest** — module head (black block, red word) + 3-col grid `1fr 1.7fr 1fr`:
   left = field-note list (blue tags), center = feature image + headline + deck,
   right = **red Editions module** (`--red` bg, white type) listing prints "from $180".
5. **The Vernaculars** — module head + 3 `SurveyCard`s (No. 03 color, No. 02 B&W via
   `filter: grayscale(1)`, No. 01 color). Each: number tab, tone flag, Anton title, deck,
   "View the survey →". Card links to `/vernaculars/<slug>` (which links to the deck).
6. **Photo-essay strip** — full black section, 3-up frames with red frame numbers.
7. **Editions** — module head + 3 `EditionCard`s: white mat in a hairline frame, Anton title,
   italic series, edition/size, Anton price, "Acquire print →". Below: 4 reassurance notes.
8. **Journal** — module head + `1.4fr 1fr` grid: feature essay + 4-row list (thumbnails).
9. **Newsletter** — `4px` framed two-up: copy + email form (→ Ghost members) | image.
10. **Studio** — short statement + contact mailto.
11. **Footer** — wordmark + epigraph, 4 columns (Surveys / Studio / Follow / about + chop),
    legal row "© 2026 Anamorphick · An Offset LLC publication" + "Powered by Ghost".

Interactions (`anamorphick.js`): sticky-shadow on scroll; slide-in menu (430px, left,
dimmed scrim); full-screen search overlay (Esc closes both); IntersectionObserver `.reveal`
fade-up (respect `prefers-reduced-motion`); newsletter submit → success state.

---

## Survey deck template

Each survey is a self-contained 1920×1080 deck (`<deck-stage>` + `deck-stage.js`):
**Cover** (Anton title, kicker "A Photographic Survey · No. NN", deck, byline, epigraph) →
**Plates** (`.plate-slide`: top slate `Anamorphick / <Series> · No. NN`, letterboxed image,
bottom caption = red number tab + Anton title + location) → **Colophon** (chop, italic
"everything experiential", credit). To make No. 04: copy `Vernacular Strange Light - No. 03.html`,
swap images/captions, bump the number. Decks use vermillion `#dd2a1b` + chamomile `#e6d9a3`
on black (the studio's print palette — distinct from the magazine red below).

---

## Design tokens

**Site (magazine) — from The Offset system**
```
--ink #000  --ink-1 #111  --ink-2 #515151  --ink-3 #767676  --ink-4 #8c8c8c
--paper #fff  --red #ec1c24  --red-ink #c8141b  --blue #0002ad  --blue-ink #00018a
--line #ccc  --line-soft #ddd  --fill #eee  --fill-2 #f1f1f1  --fill-3 #f7f7f7
--shadow-header 0 2px 5px rgba(0,0,0,.10)   --shadow-card 0 1px 4px rgba(0,0,0,.12)
Fonts: Anton (display) · Georgia (body/serif) · Inter (UI/sans)
Scale: display 72 · h1 40 · h2 28 · h3/lead 20 · body 16 · meta 14 · label 13 · tag 12
Body line-height 1.6 · headline 1.0–1.1 · tag tracking .04em
```

**Decks (print) palette**
```
bg #000 · paper #fff · gray #b0b0b0 · red(vermillion) #dd2a1b · chamomile #e6d9a3 · line #232323
```

---

## Deploy

### A. Ghost (headless) on the DigitalOcean droplet
1. Droplet: Ubuntu 22.04, **2 GB RAM** (1 GB is tight for Ghost+MySQL). Point an A record
   `cms.anamorphick.com` → droplet IP (in Cloudflare DNS, **grey cloud / DNS-only** so certbot can issue).
2. Install Docker + Compose. Copy `deploy/docker-compose.ghost.yml`, set the passwords and
   `url: https://cms.anamorphick.com`, then `docker compose up -d`.
3. Install nginx + certbot on the host, drop in `deploy/nginx-cms.conf`, run
   `certbot --nginx -d cms.anamorphick.com`. (Or use Caddy for auto-TLS.)
4. Finish Ghost setup at `https://cms.anamorphick.com/ghost`. Create a **Custom Integration**
   → copy the **Content API key** and URL.

### B. Astro site on Cloudflare Pages (serverless UI)
1. Push the repo to GitHub (this `design_handoff_anamorphick/` folder is the repo root).
   In Cloudflare Pages → **Create project** → **Connect to Git** → pick the repo.
2. Framework preset **Astro** (or None). Build command `npm run build`, output dir `dist`.
3. Env vars (optional for the first deploy — the site builds without them via static
   fallback): `GHOST_URL=https://cms.anamorphick.com`, `GHOST_CONTENT_KEY=<key>`.
4. Add custom domain `anamorphick.com` (Cloudflare proxied / orange cloud).
5. In Ghost → Integrations → add a **"Post published" webhook** pointing at the Pages
   **Deploy Hook** URL, so publishing rebuilds the site.
6. Swap the **Cal.com** booking handles in `src/pages/rental.astro` (`services[].book`)
   for your real event URLs before going live.

### C. Images
Either keep images in Ghost (served from the droplet via Cloudflare) or upload to **R2** and
reference by URL. For images committed to the repo, `astro:assets` optimizes them at build.

### D. Newsletter / members
Native to Ghost — enable **Members** + **Portal**, configure Mailgun for bulk email. The
homepage newsletter form posts to Ghost's members signup endpoint (or embed Ghost Portal).

### E. Local dev
`cp .env.example .env` → fill keys → `npm install` → `npm run dev`.

---

## Files in this handoff
- `README.md` — this document
- `package.json`, `astro.config.mjs`, `.env.example` — project config
- `src/lib/ghost.ts` — Content API client
- `src/layouts/` (`Base.astro`, `SiteShell.astro`) — page shell
- `src/pages/` — `index.astro` (homepage), `rental.astro` (The Bench), and the
  `vernaculars/`, `journal/`, `editions/` `[slug]` detail pages
- `src/components/` — all homepage/shell components (Masthead, Nav, MenuSearch, Hero,
  LatestRail, SurveyCard, EssayStrip, EditionCard, JournalFeed, Newsletter, Footer)
- `src/styles/anamorphick.css` (+ `src/styles/assets/colors_and_type.css`)
- `public/assets/` — survey photography (`strange-light/`, `no2/`, `img/`), icons, mark,
  footer flag; `public/scripts/anamorphick.js` — the vanilla interaction script
- `deploy/docker-compose.ghost.yml`, `deploy/nginx-cms.conf` — Ghost on DO
- Design references live at the **project root** (`index.html`, `anamorphick.css`,
  `anamorphick.js`, the survey decks + `Vernacular — Survey Template.html`, `deck-stage.js`,
  `assets/`).
