import React from 'react';

export type PlatformId = 'linkedin' | 'instagram' | 'facebook' | 'x';

export interface Platform {
  id: PlatformId;
  name: string;
  color: string;
  Icon: React.FC<{ className?: string }>;
}

// Simple brand marks drawn as SVG, so no icon package is needed.
const LinkedInIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden="true">
    <path d="M4.98 3.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5ZM3 9.75h4V21H3V9.75Zm6.5 0h3.8v1.6h.06c.53-1 1.84-2.05 3.78-2.05 4.04 0 4.78 2.6 4.78 6V21h-4v-5.1c0-1.22-.02-2.78-1.7-2.78-1.7 0-1.96 1.33-1.96 2.7V21h-4V9.75Z" />
  </svg>
);

const InstagramIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <rect x="3" y="3" width="18" height="18" rx="5" />
    <circle cx="12" cy="12" r="4" />
    <circle cx="17.4" cy="6.6" r="1" fill="currentColor" stroke="none" />
  </svg>
);

const FacebookIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden="true">
    <path d="M13.5 21v-7.5h2.6l.4-3h-3V8.6c0-.87.25-1.46 1.5-1.46h1.6V4.46A21 21 0 0 0 14.27 4.3c-2.3 0-3.87 1.4-3.87 3.98v2.22H7.8v3h2.6V21h3.1Z" />
  </svg>
);

const XIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden="true">
    <path d="M17.75 3h3.07l-6.7 7.66L22 21h-6.17l-4.83-6.32L5.47 21H2.4l7.17-8.2L2 3h6.33l4.37 5.78L17.75 3Zm-1.08 16.17h1.7L7.4 4.74H5.58l11.09 14.43Z" />
  </svg>
);

export const PLATFORMS: Platform[] = [
  { id: 'linkedin', name: 'LinkedIn', color: '#0A66C2', Icon: LinkedInIcon },
  { id: 'instagram', name: 'Instagram', color: '#E1306C', Icon: InstagramIcon },
  { id: 'facebook', name: 'Facebook', color: '#1877F2', Icon: FacebookIcon },
  { id: 'x', name: 'X', color: '#E7E9EA', Icon: XIcon }
];

// The Home badge of a platform, from what the poster offers (GET /api/platforms): registered and able to publish
// for real → Live; registered but simulated only → Test; not registered → Soon. Until the API answers (or if it
// fails), the page has LinkedIn alone, so this gives the static values: LinkedIn Live, the others Soon.
export type PlatformBadge = 'live' | 'test' | 'soon';
export const BADGE_LABEL: Record<PlatformBadge, string> = { live: 'Live', test: 'Test', soon: 'Soon' };

export function platformBadge(id: PlatformId, offered: { id: string; real_available: boolean }[]): PlatformBadge {
  const found = offered.find((p) => p.id === id);
  return found ? (found.real_available ? 'live' : 'test') : 'soon';
}

export const platformById = (id?: string | null): Platform =>
  PLATFORMS.find((p) => p.id === id?.toLowerCase()) ?? PLATFORMS[0];
