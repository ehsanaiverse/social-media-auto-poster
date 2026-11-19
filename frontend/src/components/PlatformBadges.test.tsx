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

describe('"Drop it in the folder" lines and tiles', () => {
  const badges = () => [...document.querySelectorAll('svg g[data-badge]')].map((g) => g.getAttribute('data-badge'));

  it('follow the poster: Live solid green, Test amber, Soon dashed', async () => {
    const { CtaSection } = await import('./CtaSection');
    api.getPlatforms.mockResolvedValue([LINKEDIN_FALLBACK, { ...INSTAGRAM, real_available: false, confirmation_phrase: null }]);
    renderWithApi(<CtaSection />);
    await vi.waitFor(() => expect(badges()).toEqual(['live', 'test', 'soon', 'soon']));
    const [linkedin, instagram, facebook] = [...document.querySelectorAll('svg g[data-badge] path')];
    expect(linkedin.getAttribute('stroke-dasharray')).toBeNull();
    expect(instagram.getAttribute('stroke')).toBe('url(#cta-flow-test)');
    expect(facebook.getAttribute('stroke-dasharray')).toBe('4 6');
    expect(screen.getByText('Test')).toBeInTheDocument();
  });

  it('show Instagram Live when it is, and Soon when the API fails', async () => {
    const { CtaSection } = await import('./CtaSection');
    api.getPlatforms.mockResolvedValue([LINKEDIN_FALLBACK, INSTAGRAM]);
    const first = renderWithApi(<CtaSection />);
    await vi.waitFor(() => expect(badges()).toEqual(['live', 'live', 'soon', 'soon']));
    first.unmount();

    api.getPlatforms.mockRejectedValue({ status: 500, message: 'down' });
    renderWithApi(<CtaSection />);
    await vi.waitFor(() => expect(api.getPlatforms).toHaveBeenCalledTimes(2));
    expect(badges()).toEqual(['live', 'soon', 'soon', 'soon']);
  });
});
