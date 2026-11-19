// Home badges from GET /api/platforms: Live (real), Test (simulated only), Soon (not offered). One helper.
import { screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { HeroSection } from './HeroSection';
import { api, mockApi, renderWithApi } from '../test/api';
import { platformBadge } from '../lib/platforms';
import { LINKEDIN_FALLBACK } from '../lib/postRules';

vi.mock('../services/apiService');
vi.mock('./3d/NetworkGlobe3D', () => ({ default: () => null }));

const INSTAGRAM = { ...LINKEDIN_FALLBACK, id: 'instagram', name: 'Instagram' };
const chip = (name: string) => within(screen.getByRole('list', { name: 'Platforms' })).getByText(name).closest('li')!;

beforeEach(() => mockApi());

describe('platformBadge', () => {
  it('is Live, Test or Soon from what the poster offers', () => {
    const offered = [{ id: 'linkedin', real_available: true }, { id: 'instagram', real_available: false }];
    expect(platformBadge('linkedin', offered)).toBe('live');
    expect(platformBadge('instagram', offered)).toBe('test');
    expect(platformBadge('facebook', offered)).toBe('soon');
    expect(platformBadge('x', offered)).toBe('soon');
  });
});

describe('Home chips', () => {
  it('show Instagram as Live when the poster can publish to it', async () => {
    api.getPlatforms.mockResolvedValue([LINKEDIN_FALLBACK, INSTAGRAM]);
    renderWithApi(<HeroSection />);
    expect(await within(chip('Instagram')).findByText('· Live')).toBeInTheDocument();
    expect(within(chip('LinkedIn')).getByText('· Live')).toBeInTheDocument();
    expect(within(chip('Facebook')).getByText('· Soon')).toBeInTheDocument();
    expect(within(chip('X')).getByText('· Soon')).toBeInTheDocument();
  });

  it('show Test for a simulated-only platform', async () => {
    api.getPlatforms.mockResolvedValue([LINKEDIN_FALLBACK, { ...INSTAGRAM, real_available: false, confirmation_phrase: null }]);
    renderWithApi(<HeroSection />);
    expect(await within(chip('Instagram')).findByText('· Test')).toBeInTheDocument();
  });

  it('fall back to the static values when GET /api/platforms fails', async () => {
    api.getPlatforms.mockRejectedValue({ status: 500, message: 'down' });
    renderWithApi(<HeroSection />);
    await vi.waitFor(() => expect(api.getPlatforms).toHaveBeenCalled());
    expect(within(chip('LinkedIn')).getByText('· Live')).toBeInTheDocument();
    expect(within(chip('Instagram')).getByText('· Soon')).toBeInTheDocument();
  });
});
