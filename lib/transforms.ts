// Pure data-transformation helpers extracted from Astro page frontmatter
// so they can be unit-tested independently.

import { tagValue } from '../ghost';

// ---- safe wrapper ----

/** Run an async factory; return its result or `fallback` on any error. */
export async function safe<T>(fn: () => Promise<T>, fallback: T): Promise<T> {
  try {
    return await fn();
  } catch {
    return fallback;
  }
}

// ---- Ghost → SurveyCard props ----

export interface SurveyCardProps {
  href: string;
  no: string | null;
  tone: string;
  place: string;
  title: string;
  deck: string;
  image: string;
  alt: string;
}

/** Map a Ghost post (tagged #survey) to the props expected by SurveyCard. */
export function ghostPostToSurvey(post: {
  slug: string;
  title: string;
  custom_excerpt?: string;
  excerpt?: string;
  feature_image?: string;
  feature_image_alt?: string;
  tags?: { name?: string }[];
}): SurveyCardProps {
  return {
    href: `/vernaculars/${post.slug}`,
    no: tagValue(post, 'no'),
    tone: tagValue(post, 'tone') || 'color',
    place: (post.tags?.find((t) => t.name?.startsWith('#place'))?.name ?? '')
      .replace('#place-', '')
      .replace(/-/g, ' ')
      .toUpperCase(),
    title: post.title,
    deck: post.custom_excerpt ?? post.excerpt ?? '',
    image: post.feature_image ?? '',
    alt: post.feature_image_alt ?? post.title,
  };
}

// ---- Ghost → EditionCard props ----

export interface EditionCardProps {
  href: string;
  bw: boolean;
  image: string;
  title: string;
  sub: string;
  edition: string;
  price: string;
}

/** Map a Ghost post (tagged #edition) to the props expected by EditionCard. */
export function ghostPostToEdition(post: {
  slug: string;
  title: string;
  custom_excerpt?: string;
  feature_image?: string;
  tags?: { name?: string }[];
}): EditionCardProps {
  return {
    href: tagValue(post, 'stripe') || `/editions/${post.slug}`,
    bw: tagValue(post, 'tone') === 'bw',
    image: post.feature_image ?? '',
    title: post.title,
    sub: post.custom_excerpt ?? '',
    edition: `Edition of ${tagValue(post, 'ed')} · ${(tagValue(post, 'size') || '').replace('x', ' × ')} in`,
    price: `$${tagValue(post, 'price')}`,
  };
}

// ---- Deck URL extraction (used in [slug].astro) ----

/** Extract the presentation deck URL from a post's internal tags. */
export function extractDeckUrl(tags: { name?: string }[] | undefined): string | null {
  return (
    (tags?.find((t) => t.name?.startsWith('#deck'))?.name ?? '').replace('#deck:', '') || null
  );
}

// ---- Tone helpers (used in SurveyCard.astro) ----

/** CSS class suffix for a given tone value. */
export function toneClass(tone: string): string {
  return tone === 'bw' ? ' bw' : '';
}

/** Human-readable label for a given tone value. */
export function toneLabel(tone: string): string {
  if (tone === 'bw') return 'Black & White';
  if (tone === 'day') return 'Color · Day';
  return 'Color · Dusk';
}
