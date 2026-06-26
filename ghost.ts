// Ghost headless Content API client.
// Reads posts at BUILD time; the static site is rebuilt via a Ghost publish webhook.
import GhostContentAPI from '@tryghost/content-api';

// ---------- Environment validation ----------
const ghostUrl = import.meta.env.GHOST_URL;
const ghostKey = import.meta.env.GHOST_CONTENT_KEY;

/** True when Ghost credentials are configured (build can fetch live content). */
export const ghostConfigured = Boolean(ghostUrl && ghostKey);

if (!ghostConfigured) {
  console.warn(
    '[ghost] GHOST_URL or GHOST_CONTENT_KEY is not set — API calls will fail. ' +
    'The site will build with static fallback content only.'
  );
}

const api = ghostConfigured
  ? new GhostContentAPI({ url: ghostUrl, key: ghostKey, version: 'v5.0' })
  : null;

// ---------- Typed fetch helpers ----------

/**
 * Wraps a Ghost Content API call with:
 *  - a guard against missing configuration (throws immediately with a clear message)
 *  - structured error context so callers know which operation failed and why
 */
async function ghostFetch<T>(label: string, fn: (client: typeof api) => Promise<T>): Promise<T> {
  if (!api) {
    throw new Error(
      `[ghost] Cannot fetch "${label}": Ghost is not configured. ` +
      'Set GHOST_URL and GHOST_CONTENT_KEY environment variables.'
    );
  }
  try {
    return await fn(api);
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[ghost] Failed to fetch "${label}": ${message}`);
    throw new Error(`[ghost] "${label}" request failed: ${message}`, { cause: err });
  }
}

// ---------- Public API ----------

// Surveys — Ghost posts tagged #survey (internal tag). Newest first.
export async function getSurveys() {
  return ghostFetch('getSurveys', (client) =>
    client.posts.browse({ filter: 'tag:hash-survey', include: 'tags,authors', limit: 'all' })
  );
}

// Journal — everything that is NOT a survey or an edition.
export async function getJournal() {
  return ghostFetch('getJournal', (client) =>
    client.posts.browse({ filter: 'tag:-hash-survey+tag:-hash-edition', include: 'tags,authors', limit: 'all' })
  );
}

// Editions — Ghost posts tagged #edition (price/size/stripe in internal tags).
export async function getEditions() {
  return ghostFetch('getEditions', (client) =>
    client.posts.browse({ filter: 'tag:hash-edition', include: 'tags', limit: 'all' })
  );
}

export async function getPost(slug: string) {
  if (!slug) {
    throw new Error('[ghost] getPost called without a slug');
  }
  return ghostFetch(`getPost(${slug})`, (client) =>
    client.posts.read({ slug }, { include: 'tags,authors' })
  );
}

// Helper: pull a value out of an internal tag like "#price-180" → "180".
export function tagValue(post: { tags?: { name?: string }[] }, prefix: string): string | null {
  const t = (post.tags || []).find((x) => x.name?.startsWith('#' + prefix));
  return t ? t.name!.slice(prefix.length + 1) : null;
}

export default api;
