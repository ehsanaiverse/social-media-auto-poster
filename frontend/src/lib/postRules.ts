// Client-side copies of the backend's intake rules, for instant feedback only.
// The backend checks everything again; these never replace its checks.

// backend/scripts/app.py: check_stem (to move into ingestion/intake.py, api-plan refactor 1).
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

export const MAX_CAPTION_BYTES = 64 * 1024; // ingestion/validation.py read cap, not LinkedIn's limit
export const MAX_REAL_CAPTION_CHARS = 3000; // real mode only (D36)
export const MAX_IMAGE_BYTES = 10 * 1024 * 1024; // proposal in api-plan open question 3

// ingestion/validation.py: EXPECTED_TYPE and the file-signature check.
const EXPECTED_TYPE: Record<string, 'jpeg' | 'png'> = { jpg: 'jpeg', jpeg: 'jpeg', png: 'png' };

export const extensionOf = (filename: string): string =>
  filename.includes('.') ? filename.split('.').pop()!.toLowerCase() : '';

export function detectImageType(bytes: Uint8Array): 'jpeg' | 'png' | null {
  if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return 'jpeg';
  const png = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  if (bytes.length >= 8 && png.every((b, i) => bytes[i] === b)) return 'png';
  return null;
}

// Every problem with the image, as the backend's validation would list them.
export async function imageErrors(file: File): Promise<string[]> {
  const ext = extensionOf(file.name);
  const expected = EXPECTED_TYPE[ext];
  if (!expected) return ['Only JPG and PNG pictures are supported.'];
  if (file.size === 0) return ['This picture file is empty.'];
  const errors: string[] = [];
  if (file.size > MAX_IMAGE_BYTES) errors.push('This picture is larger than 10 MB.');
  const head = new Uint8Array(await file.slice(0, 8).arrayBuffer());
  const detected = detectImageType(head);
  if (detected === null) errors.push("This file isn't a JPG or PNG picture.");
  else if (detected !== expected) errors.push(`This picture is really a ${detected.toUpperCase()} but its name ends in .${ext}. Rename it and try again.`);
  return errors;
}

// LinkedIn counts caption length in UTF-16 units (D39), which is what .length gives.
export const captionLength = (caption: string): number => caption.length;

export const utf8Bytes = (text: string): number => new TextEncoder().encode(text).length;

// The phrase typed to confirm a real publish (api-plan open question 2).
export const CONFIRM_PHRASE = 'publish to LinkedIn';
