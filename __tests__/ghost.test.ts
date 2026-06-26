import { describe, it, expect, vi, beforeEach } from 'vitest';

// vi.hoisted runs before module evaluation — set env so ghostConfigured = true.
const { browseMock, readMock } = vi.hoisted(() => {
  process.env.GHOST_URL = 'https://cms.example.com';
  process.env.GHOST_CONTENT_KEY = 'abc123';
  return {
    browseMock: vi.fn(),
    readMock: vi.fn(),
  };
});

vi.mock('@tryghost/content-api', () => ({
  default: class MockGhostContentAPI {
    posts = { browse: browseMock, read: readMock };
  },
}));

import { getSurveys, getJournal, getEditions, getPost, tagValue } from '../ghost';

// ---- tagValue ----
// Tag format: #<prefix><value>  (no separator between prefix and value).
// tagValue(post, 'price') on tag { name: '#price180' } → '180'

describe('tagValue', () => {
  it('returns the value after the prefix', () => {
    const post = { tags: [{ name: '#price180' }, { name: '#tonebw' }] };
    expect(tagValue(post, 'price')).toBe('180');
    expect(tagValue(post, 'tone')).toBe('bw');
  });

  it('returns null when no matching tag exists', () => {
    const post = { tags: [{ name: '#price180' }] };
    expect(tagValue(post, 'size')).toBeNull();
  });

  it('returns null when tags array is empty', () => {
    expect(tagValue({ tags: [] }, 'price')).toBeNull();
  });

  it('returns null when tags is undefined', () => {
    expect(tagValue({}, 'price')).toBeNull();
  });

  it('handles tags with no name property', () => {
    const post = { tags: [{ name: undefined }, { name: '#no03' }] };
    expect(tagValue(post, 'no')).toBe('03');
  });

  it('handles multi-segment values', () => {
    const post = { tags: [{ name: '#deck:/decks/strange-light/' }] };
    expect(tagValue(post, 'deck')).toBe(':/decks/strange-light/');
  });

  it('returns empty string when value after prefix is empty', () => {
    const post = { tags: [{ name: '#price' }] };
    expect(tagValue(post, 'price')).toBe('');
  });

  it('handles prefix that is a substring of another tag', () => {
    const post = { tags: [{ name: '#no03' }, { name: '#note-extra' }] };
    expect(tagValue(post, 'no')).toBe('03');
  });
});

// ---- API wrapper functions ----

describe('getSurveys', () => {
  beforeEach(() => {
    browseMock.mockReset();
  });

  it('calls browse with the correct survey filter', async () => {
    const fakePosts = [{ slug: 'strange-light', title: 'Strange Light' }];
    browseMock.mockResolvedValue(fakePosts);

    const result = await getSurveys();
    expect(result).toEqual(fakePosts);
    expect(browseMock).toHaveBeenCalledWith({
      filter: 'tag:hash-survey',
      include: 'tags,authors',
      limit: 'all',
    });
  });

  it('propagates API errors with context', async () => {
    browseMock.mockRejectedValue(new Error('Network error'));
    await expect(getSurveys()).rejects.toThrow('[ghost] "getSurveys" request failed: Network error');
  });

  it('preserves the original error as cause', async () => {
    const original = new Error('Network error');
    browseMock.mockRejectedValue(original);
    await expect(getSurveys()).rejects.toHaveProperty('cause', original);
  });
});

describe('getJournal', () => {
  beforeEach(() => {
    browseMock.mockReset();
  });

  it('calls browse excluding survey and edition tags', async () => {
    const fakePosts = [{ slug: 'my-journal', title: 'My Journal Post' }];
    browseMock.mockResolvedValue(fakePosts);

    const result = await getJournal();
    expect(result).toEqual(fakePosts);
    expect(browseMock).toHaveBeenCalledWith({
      filter: 'tag:-hash-survey+tag:-hash-edition',
      include: 'tags,authors',
      limit: 'all',
    });
  });
});

describe('getEditions', () => {
  beforeEach(() => {
    browseMock.mockReset();
  });

  it('calls browse with the edition filter', async () => {
    const fakePosts = [{ slug: 'violet-hour', title: 'Violet Hour' }];
    browseMock.mockResolvedValue(fakePosts);

    const result = await getEditions();
    expect(result).toEqual(fakePosts);
    expect(browseMock).toHaveBeenCalledWith({
      filter: 'tag:hash-edition',
      include: 'tags',
      limit: 'all',
    });
  });
});

describe('getPost', () => {
  beforeEach(() => {
    readMock.mockReset();
  });

  it('reads a single post by slug', async () => {
    const fakePost = { slug: 'strange-light', title: 'Strange Light' };
    readMock.mockResolvedValue(fakePost);

    const result = await getPost('strange-light');
    expect(result).toEqual(fakePost);
    expect(readMock).toHaveBeenCalledWith(
      { slug: 'strange-light' },
      { include: 'tags,authors' },
    );
  });
});
