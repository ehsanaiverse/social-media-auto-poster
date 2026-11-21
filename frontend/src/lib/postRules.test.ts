import { describe, expect, it } from 'vitest';
import {
  LINKEDIN_FALLBACK,
  MAX_CAPTION_CHARS,
  captionError,
  captionLength,
  detectImageType,
  imageErrors,
  isReel,
  postNameError,
} from './postRules';

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

describe('isReel and video rules (D51)', () => {
  it('identifies MP4 and MOV as reels', () => {
    expect(isReel('video.mp4')).toBe(true);
    expect(isReel('video.mov')).toBe(true);
    expect(isReel('video.MP4')).toBe(true);
    expect(isReel('photo.jpg')).toBe(false);
    expect(isReel(null)).toBe(false);
  });

  it('rejects video on LinkedIn', async () => {
    const file = new File(['dummy'], 'video.mp4', { type: 'video/mp4' });
    const errors = await imageErrors(file, LINKEDIN_FALLBACK);
    expect(errors).toContain('video not supported on LinkedIn');
  });

  const instagramPlatform = {
    id: 'instagram',
    name: 'Instagram',
    real_available: true,
    confirmation_phrase: 'publish to Instagram',
    rules: {
      extensions: ['.jpeg', '.jpg', '.mp4', '.mov'],
      max_image_bytes: 8000000,
      max_video_bytes: 300_000_000,
      max_caption_bytes: 64 * 1024,
      caption_max_chars: 2200,
      caption_max_hashtags: 30,
      caption_max_mentions: 20,
      aspect: { min: '4:5', max: '1.91:1' },
    },
  };

  // imageErrors reads only file.size for a reel, so a defined size keeps these boundary tests tiny.
  const reelOfSize = (size: number): File => {
    const file = new File(['dummy'], 'video.mp4', { type: 'video/mp4' });
    Object.defineProperty(file, 'size', { value: size });
    return file;
  };

  it('accepts video on Instagram under 300 MB', async () => {
    const errors = await imageErrors(reelOfSize(1024), instagramPlatform);
    expect(errors).toEqual([]);
  });

  it('accepts exactly 300,000,000 bytes (decimal 300 MB, not 300 MiB)', async () => {
    const errors = await imageErrors(reelOfSize(300_000_000), instagramPlatform);
    expect(errors).toEqual([]);
  });

  it('refuses 300,000,001 bytes with the 300 MB message', async () => {
    const errors = await imageErrors(reelOfSize(300_000_001), instagramPlatform);
    expect(errors).toEqual(['This video is larger than 300 MB.']);
  });
});
