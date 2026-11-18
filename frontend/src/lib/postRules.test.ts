import { describe, expect, it } from 'vitest';
import { MAX_CAPTION_CHARS, captionError, captionLength, detectImageType, postNameError } from './postRules';

describe('captionError: the 3,000-character limit blocks in every mode (D45)', () => {
  it('accepts exactly 3,000 characters', () => {
    expect(captionError('a'.repeat(MAX_CAPTION_CHARS))).toBeNull();
  });

  it('refuses 3,001 characters', () => {
    expect(captionError('a'.repeat(MAX_CAPTION_CHARS + 1))).toMatch(/longer than 3,000 characters/);
  });

  it('counts an emoji as two, as LinkedIn and the backend do', () => {
    expect(captionLength('🎉')).toBe(2);
    expect(captionError('🎉'.repeat(1500))).toBeNull(); // 3,000 units
    expect(captionError('🎉'.repeat(1500) + 'a')).toMatch(/longer than 3,000/);
  });

  it('refuses an empty or blank caption', () => {
    expect(captionError('')).toBe('Please write a caption.');
    expect(captionError('  \n ')).toBe('Please write a caption.');
  });
});

describe('postNameError and detectImageType', () => {
  it('refuses forbidden characters and hidden names', () => {
    expect(postNameError('launch')).toBeNull();
    expect(postNameError('a/b')).toMatch(/don't use these characters/);
    expect(postNameError('.hidden')).toMatch(/can't start/);
  });

  it('reads JPEG and PNG signatures', () => {
    expect(detectImageType(new Uint8Array([0xff, 0xd8, 0xff, 0xe0]))).toBe('jpeg');
    expect(detectImageType(new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))).toBe('png');
    expect(detectImageType(new Uint8Array([0x47, 0x49, 0x46]))).toBeNull();
  });
});
