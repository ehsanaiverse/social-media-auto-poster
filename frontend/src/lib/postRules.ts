// Instant feedback while a post is written. The rules come from the backend (GET /api/platforms), and the
// backend checks everything again (POST /api/posts/check while typing, POST /api/posts when adding).
import type { PlatformInfo } from '../types/api';

// backend/src/social_media_auto_poster/ingestion/intake.py: check_post_name.
const FORBIDDEN_STEM_CHARS = '<>:"/\\|?*';

export function postNameError(raw: string): string | null {
  const name = raw.trim();
  if (!name) return 'Please give your post a name.';
  if ([...name].some((ch) => FORBIDDEN_STEM_CHARS.includes(ch))) {
    return `Please don't use these characters: ${[...FORBIDDEN_STEM_CHARS].join(' ')}`;
  }
  if (name.startsWith('.') || name.startsWith('~$')) {
    return "The name can't start with '.' or '~$'.";
  }
  return null;
}

// LinkedIn as GET /api/platforms describes it. Used only until the API has answered (or if it cannot be
// reached), so the page never shows a post as fine that LinkedIn would refuse.
export const LINKEDIN_FALLBACK: PlatformInfo = {
  id: 'linkedin',
  name: 'LinkedIn',
  real_available: true,
  confirmation_phrase: 'publish to LinkedIn',
  rules: {
    extensions: ['.jpeg', '.jpg', '.png'],
    max_image_bytes: 10 * 1024 * 1024,
    max_caption_bytes: 64 * 1024,
    caption_max_chars: 3000,
    caption_max_hashtags: null,
    caption_max_mentions: null,
    aspect: null
  }
};

export const MAX_CAPTION_BYTES = LINKEDIN_FALLBACK.rules.max_caption_bytes;
export const MAX_CAPTION_CHARS = LINKEDIN_FALLBACK.rules.caption_max_chars;
export const MAX_IMAGE_BYTES = LINKEDIN_FALLBACK.rules.max_image_bytes;
// The phrase typed to confirm a real LinkedIn publish; each platform's own comes from GET /api/platforms.
export const CONFIRM_PHRASE = LINKEDIN_FALLBACK.confirmation_phrase as string;

export const extensionOf = (filename: string): string =>
  filename.includes('.') ? filename.split('.').pop()!.toLowerCase() : '';

export function detectImageType(bytes: Uint8Array): 'jpeg' | 'png' | null {
  if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return 'jpeg';
  const png = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  if (bytes.length >= 8 && png.every((b, i) => bytes[i] === b)) return 'png';
  return null;
}

const typeOf = (ext: string): 'jpeg' | 'png' | null => (ext === 'png' ? 'png' : ext === 'jpg' || ext === 'jpeg' ? 'jpeg' : null);

// 10 MiB is "10 MB" as the backend says it; Instagram's 8,000,000 bytes is "8 MB".
const sizeText = (bytes: number): string =>
  bytes % (1024 * 1024) === 0 ? `${bytes / (1024 * 1024)} MB` : `${Math.round(bytes / 1_000_000)} MB`;

export const isReel = (filename?: string | null): boolean =>
  Boolean(filename && (filename.toLowerCase().endsWith('.mp4') || filename.toLowerCase().endsWith('.mov')));

// Every problem with the image or video under this platform's rules, as the backend's validation would list them.
export async function imageErrors(file: File, platform: PlatformInfo = LINKEDIN_FALLBACK): Promise<string[]> {
  const ext = extensionOf(file.name);
  if (isReel(file.name)) {
    if (platform.id === 'linkedin') {
      return ['video not supported on LinkedIn'];
    }
    if (!platform.rules.extensions.includes(`.${ext}`)) {
      return [`${platform.name} does not accept video.`];
    }
    if (file.size === 0) return ['This video file is empty.'];
    // The backend's D51 limit is decimal 300 MB (300,000,000 bytes), not 300 MiB.
    const maxVideoBytes = platform.rules.max_video_bytes ?? 300_000_000;
    if (file.size > maxVideoBytes) return [`This video is larger than ${sizeText(maxVideoBytes)}.`];
    return [];
  }

  const expected = platform.rules.extensions.includes(`.${ext}`) ? typeOf(ext) : null;
  if (!expected) {
    return [platform.rules.extensions.includes('.png') ? 'Only JPG and PNG pictures are supported.' : `${platform.name} accepts JPG pictures only.`];
  }
  if (file.size === 0) return ['This picture file is empty.'];
  const errors: string[] = [];
  if (file.size > platform.rules.max_image_bytes) errors.push(`This picture is larger than ${sizeText(platform.rules.max_image_bytes)}.`);
  const head = new Uint8Array(await file.slice(0, 8).arrayBuffer());
  const detected = detectImageType(head);
  if (detected === null) errors.push(`This file isn't a ${platform.rules.extensions.includes('.png') ? 'JPG or PNG' : 'JPG'} picture.`);
  else if (detected !== expected) errors.push(`This picture is really a ${detected.toUpperCase()} but its name ends in .${ext}. Rename it and try again.`);
  return errors;
}

// Length in UTF-16 units, as LinkedIn and the backend count it (D39). It is never less than the code-point
// count, so it also covers Instagram's limit on either count.
export const captionLength = (caption: string): number => caption.length;

export const utf8Bytes = (text: string): number => new TextEncoder().encode(text).length;

// As platforms/instagram/rules.py counts them: '#' before a word character, '@' before a name character.
const countHashtags = (text: string) => (text.match(/#(?=[\p{L}\p{N}_])/gu) ?? []).length;
const countMentions = (text: string) => (text.match(/@(?=[A-Za-z0-9._])/g) ?? []).length;

// What stops a caption from being added under this platform's rules; null when it is fine.
export function captionError(caption: string, platform: PlatformInfo = LINKEDIN_FALLBACK): string | null {
  const { rules, name } = platform;
  if (!caption.trim()) return 'Please write a caption.';
  if (captionLength(caption) > rules.caption_max_chars) {
    return `The caption is longer than ${rules.caption_max_chars.toLocaleString('en-US')} characters, which ${name} does not allow.`;
  }
  if (rules.caption_max_hashtags !== null && countHashtags(caption) > rules.caption_max_hashtags) {
    return `The caption has more than ${rules.caption_max_hashtags} hashtags, which ${name} does not allow.`;
  }
  if (rules.caption_max_mentions !== null && countMentions(caption) > rules.caption_max_mentions) {
    return `The caption mentions more than ${rules.caption_max_mentions} accounts, which ${name} does not allow.`;
  }
  if (utf8Bytes(caption) > rules.max_caption_bytes) return 'The caption is too long.';
  return null;
}
