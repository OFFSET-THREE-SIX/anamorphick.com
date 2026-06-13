// Ghost headless Content API client.
// Reads posts at BUILD time; the static site is rebuilt via a Ghost publish webhook.
import GhostContentAPI from '@tryghost/content-api';

const api = new GhostContentAPI({
  url: import.meta.env.GHOST_URL,
  key: import.meta.env.GHOST_CONTENT_KEY,
  version: 'v5.0',
});

// Surveys — Ghost posts tagged #survey (internal tag). Newest first.
export async function getSurveys() {
  return api.posts.browse({ filter: 'tag:hash-survey', include: 'tags,authors', limit: 'all' });
}

// Journal — everything that is NOT a survey or an edition.
export async function getJournal() {
  return api.posts.browse({ filter: 'tag:-hash-survey+tag:-hash-edition', include: 'tags,authors', limit: 'all' });
}

// Editions — Ghost posts tagged #edition (price/size/stripe in internal tags).
export async function getEditions() {
  return api.posts.browse({ filter: 'tag:hash-edition', include: 'tags', limit: 'all' });
}

export async function getPost(slug) {
  return api.posts.read({ slug }, { include: 'tags,authors' });
}

// Helper: pull a value out of an internal tag like "#price-180" → "180".
export function tagValue(post, prefix) {
  const t = (post.tags || []).find((x) => x.name?.startsWith('#' + prefix));
  return t ? t.name.slice(prefix.length + 1) : null;
}

export default api;
