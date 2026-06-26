import { describe, it, expect, vi } from 'vitest';

vi.mock('@tryghost/content-api', () => ({
  default: class MockGhostContentAPI {
    posts = { browse: vi.fn(), read: vi.fn() };
  },
}));
vi.stubEnv('GHOST_URL', 'https://cms.example.com');
vi.stubEnv('GHOST_CONTENT_KEY', 'abc123');

import {
  safe,
  ghostPostToSurvey,
  ghostPostToEdition,
  extractDeckUrl,
  toneClass,
  toneLabel,
} from '../lib/transforms';

// ---- safe ----

describe('safe', () => {
  it('returns the resolved value on success', async () => {
    const result = await safe(() => Promise.resolve(42), 0);
    expect(result).toBe(42);
  });

  it('returns the fallback when the function rejects', async () => {
    const result = await safe(() => Promise.reject(new Error('boom')), []);
    expect(result).toEqual([]);
  });

  it('returns the fallback when the function throws synchronously', async () => {
    const result = await safe(() => { throw new Error('sync boom'); }, 'default');
    expect(result).toBe('default');
  });
});

// ---- ghostPostToSurvey ----
// Ghost internal tags use format #<prefix><value> (no dash separator).

describe('ghostPostToSurvey', () => {
  it('maps a Ghost post to SurveyCard props', () => {
    const post = {
      slug: 'strange-light',
      title: 'Strange Light',
      custom_excerpt: 'Seven frames of dusk',
      feature_image: '/img/dusk.jpg',
      feature_image_alt: 'A dusk scene',
      tags: [
        { name: '#no03' },
        { name: '#tonedusk' },
        { name: '#place-washington-dc-2026' },
      ],
    };
    const result = ghostPostToSurvey(post);
    expect(result).toEqual({
      href: '/vernaculars/strange-light',
      no: '03',
      tone: 'dusk',
      place: 'WASHINGTON DC 2026',
      title: 'Strange Light',
      deck: 'Seven frames of dusk',
      image: '/img/dusk.jpg',
      alt: 'A dusk scene',
    });
  });

  it('defaults tone to "color" when no #tone tag', () => {
    const post = {
      slug: 'nw-dc',
      title: 'NW DC',
      tags: [{ name: '#no01' }],
    };
    expect(ghostPostToSurvey(post).tone).toBe('color');
  });

  it('falls back to excerpt when custom_excerpt is missing', () => {
    const post = {
      slug: 'test',
      title: 'Test',
      excerpt: 'Fallback excerpt',
      tags: [],
    };
    expect(ghostPostToSurvey(post).deck).toBe('Fallback excerpt');
  });

  it('falls back to title for alt when feature_image_alt is missing', () => {
    const post = {
      slug: 'test',
      title: 'My Title',
      tags: [],
    };
    expect(ghostPostToSurvey(post).alt).toBe('My Title');
  });

  it('handles empty place tag gracefully', () => {
    const post = {
      slug: 'test',
      title: 'Test',
      tags: [],
    };
    expect(ghostPostToSurvey(post).place).toBe('');
  });

  it('handles undefined tags', () => {
    const post = { slug: 'test', title: 'Test' };
    const result = ghostPostToSurvey(post);
    expect(result.no).toBeNull();
    expect(result.tone).toBe('color');
    expect(result.place).toBe('');
  });
});

// ---- ghostPostToEdition ----

describe('ghostPostToEdition', () => {
  it('maps a Ghost edition post to EditionCard props', () => {
    const post = {
      slug: 'violet-hour',
      title: 'Violet Hour',
      custom_excerpt: 'Strange Light · No. 03',
      feature_image: '/img/violet.jpg',
      tags: [
        { name: '#tonecolor' },
        { name: '#ed25' },
        { name: '#size16x24' },
        { name: '#price180' },
      ],
    };
    const result = ghostPostToEdition(post);
    expect(result).toEqual({
      href: '/editions/violet-hour',
      bw: false,
      image: '/img/violet.jpg',
      title: 'Violet Hour',
      sub: 'Strange Light · No. 03',
      edition: 'Edition of 25 · 16 × 24 in',
      price: '$180',
    });
  });

  it('uses stripe tag for href when present', () => {
    const post = {
      slug: 'violet-hour',
      title: 'Violet Hour',
      tags: [{ name: '#stripehttps://buy.stripe.com/abc' }],
    };
    expect(ghostPostToEdition(post).href).toBe('https://buy.stripe.com/abc');
  });

  it('detects bw tone', () => {
    const post = {
      slug: 'first-light',
      title: 'First Light',
      tags: [{ name: '#tonebw' }],
    };
    expect(ghostPostToEdition(post).bw).toBe(true);
  });

  it('defaults bw to false for non-bw tone', () => {
    const post = {
      slug: 'test',
      title: 'Test',
      tags: [{ name: '#tonecolor' }],
    };
    expect(ghostPostToEdition(post).bw).toBe(false);
  });

  it('handles missing custom_excerpt', () => {
    const post = { slug: 'test', title: 'Test', tags: [] };
    expect(ghostPostToEdition(post).sub).toBe('');
  });
});

// ---- extractDeckUrl ----

describe('extractDeckUrl', () => {
  it('extracts a deck URL from internal tags', () => {
    const tags = [{ name: '#deck:/decks/strange-light/' }, { name: '#no03' }];
    expect(extractDeckUrl(tags)).toBe('/decks/strange-light/');
  });

  it('returns null when no deck tag exists', () => {
    const tags = [{ name: '#no03' }, { name: '#tonedusk' }];
    expect(extractDeckUrl(tags)).toBeNull();
  });

  it('returns null for undefined tags', () => {
    expect(extractDeckUrl(undefined)).toBeNull();
  });

  it('returns null for empty tags array', () => {
    expect(extractDeckUrl([])).toBeNull();
  });

  it('handles deck tag with empty value after colon', () => {
    const tags = [{ name: '#deck:' }];
    expect(extractDeckUrl(tags)).toBeNull();
  });
});

// ---- toneClass ----

describe('toneClass', () => {
  it('returns " bw" for bw tone', () => {
    expect(toneClass('bw')).toBe(' bw');
  });

  it('returns empty string for color tone', () => {
    expect(toneClass('color')).toBe('');
  });

  it('returns empty string for day tone', () => {
    expect(toneClass('day')).toBe('');
  });

  it('returns empty string for dusk tone', () => {
    expect(toneClass('dusk')).toBe('');
  });
});

// ---- toneLabel ----

describe('toneLabel', () => {
  it('returns "Black & White" for bw', () => {
    expect(toneLabel('bw')).toBe('Black & White');
  });

  it('returns "Color · Day" for day', () => {
    expect(toneLabel('day')).toBe('Color · Day');
  });

  it('returns "Color · Dusk" for dusk', () => {
    expect(toneLabel('dusk')).toBe('Color · Dusk');
  });

  it('returns "Color · Dusk" for any other value (default)', () => {
    expect(toneLabel('color')).toBe('Color · Dusk');
    expect(toneLabel('unknown')).toBe('Color · Dusk');
  });
});
