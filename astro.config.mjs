import { defineConfig } from 'astro/config';

// Static output → deploys to Cloudflare Pages as a pure CDN site.
// Interactive chrome (menu, search, reveal, newsletter) is handled by the
// proven vanilla script at /scripts/anamorphick.js — no framework runtime needed.
export default defineConfig({
  site: 'https://anamorphick.com',
  image: {
    // allow remote optimization of Ghost / R2 image hosts
    domains: ['cms.anamorphick.com'],
  },
});
