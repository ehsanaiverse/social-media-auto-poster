// Real Instagram in the dashboard (D48): Live with Instagram's own phrase, and the Accounts card.
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IntakeStudio } from './IntakeStudio';
import { LinkedInSentinel } from './LinkedInSentinel';
import { RunnerConsole } from './RunnerConsole';
import { ScanQueueStation } from './ScanQueueStation';
import { api, mockApi, renderWithApi, useFixedClock } from '../test/api';
import { LINKEDIN_FALLBACK } from '../lib/postRules';
import type { InstagramStatusResponse, PlatformInfo } from '../types/api';

vi.mock('../services/apiService');

const INSTAGRAM: PlatformInfo = {
  id: 'instagram',
  name: 'Instagram',
  real_available: true,
  confirmation_phrase: 'publish to Instagram',
  rules: {
    extensions: ['.jpeg', '.jpg'],
    max_image_bytes: 8_000_000,
    max_caption_bytes: 64 * 1024,
    caption_max_chars: 2200,
    caption_max_hashtags: 30,
    caption_max_mentions: 20,
    aspect: { min: '4:5', max: '1.91:1' }
  }
};
const SET_UP: InstagramStatusResponse = {
  configured: true,
  token_type: 'System User, no expiry',
  checked: false,
  ok: null,
  username: null,
  account_id: null,
  quota: null,
  media_host_ok: null,
  problems: []
};
const CHECKED: InstagramStatusResponse = { ...SET_UP, checked: true, ok: true, username: 'autoposter.test', account_id: '1784', quota: { used: 3, total: 50 }, media_host_ok: true };

beforeEach(() => {
  useFixedClock();
  mockApi();
  api.getPlatforms.mockResolvedValue([LINKEDIN_FALLBACK, INSTAGRAM]);
  api.getInstagramStatus.mockImplementation(async (check = false) => (check ? CHECKED : SET_UP));
  api.run.mockResolvedValue({ mode: 'real', exit_code: 0, jobs: [] } as never);
});

afterEach(() => vi.useRealTimers());

describe('Publish to Instagram for real', () => {
  it("needs Instagram's own phrase; LinkedIn's does not work", async () => {
    const u = userEvent.setup();
    renderWithApi(
      <>
        <ScanQueueStation />
        <RunnerConsole />
      </>
    );
    await u.click(await screen.findByRole('radio', { name: /Instagram/ }));
    const button = await screen.findByRole('button', { name: 'Post 0 to Instagram' });
    expect(screen.getByText('This posts to your Instagram account')).toBeInTheDocument();
    expect(await screen.findByText('Instagram is set up (System User, no expiry token).')).toBeInTheDocument();

    const box = screen.getByLabelText(/To confirm, type/);
    fireEvent.change(box, { target: { value: 'publish to LinkedIn' } });
    expect(button).toBeDisabled();
    fireEvent.change(box, { target: { value: 'publish to Instagram' } });
    expect(button).toBeEnabled();
    await u.click(button);
    await waitFor(() =>
      expect(api.run).toHaveBeenCalledWith({ real: true, publish_simulated: false, confirm: 'publish to Instagram', platform: 'instagram' })
    );
  });

  it('offers Live for an Instagram schedule, with its phrase', async () => {
    const u = userEvent.setup();
    renderWithApi(<IntakeStudio />);
    const box = await screen.findByRole('checkbox', { name: /Instagram/ });
    await waitFor(() => expect(box).toBeEnabled());
    await u.click(box);
    await u.click(screen.getByRole('radio', { name: 'Schedule' }));
    await u.click(screen.getByRole('radio', { name: /Live/ }));
    expect(screen.getByText('publish to Instagram')).toBeInTheDocument();
  });
});

describe('Accounts', () => {
  it('shows the Instagram card and checks it read-only on request', async () => {
    const u = userEvent.setup();
    renderWithApi(<LinkedInSentinel />);
    expect(await screen.findByText('Set up')).toBeInTheDocument();
    expect(screen.getByText('System User, no expiry')).toBeInTheDocument();

    const buttons = screen.getAllByRole('button', { name: 'Check connection' });
    await u.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(api.getInstagramStatus).toHaveBeenCalledWith(true));
    expect(await screen.findByText('autoposter.test')).toBeInTheDocument();
    expect(screen.getByText('3 of 50')).toBeInTheDocument();
    expect(screen.getByText('OK')).toBeInTheDocument();
  });

  it('keeps Instagram as "Coming soon" when the poster does not offer it', async () => {
    api.getPlatforms.mockResolvedValue([LINKEDIN_FALLBACK]);
    renderWithApi(<LinkedInSentinel />);
    await waitFor(() => expect(api.getPlatforms).toHaveBeenCalled());
    expect(screen.queryByText('System User, no expiry')).not.toBeInTheDocument();
  });
});
