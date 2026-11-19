// Step F: Instagram in the dashboard, test only (D49). Rules and the phrase come from GET /api/platforms.
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IntakeStudio } from './IntakeStudio';
import { RunnerConsole } from './RunnerConsole';
import { ScanQueueStation } from './ScanQueueStation';
import { api, mockApi, renderWithApi, scanOf, useFixedClock } from '../test/api';
import { LINKEDIN_FALLBACK, captionError, imageErrors } from '../lib/postRules';
import type { PlatformInfo } from '../types/api';

vi.mock('../services/apiService');

const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0, 0x10, 0x4a, 0x46]);
const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const INSTAGRAM: PlatformInfo = {
  id: 'instagram',
  name: 'Instagram',
  real_available: false,
  confirmation_phrase: null,
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
const user = () => userEvent.setup();

beforeEach(() => {
  useFixedClock();
  mockApi();
  api.getPlatforms.mockResolvedValue([LINKEDIN_FALLBACK, INSTAGRAM]);
  api.checkPost.mockResolvedValue({ platform: 'Instagram', ok: true, errors: [] });
  api.addPost.mockResolvedValue({ name: 'launch', files: { image: 'launch.jpg', caption: 'launch.txt' }, duplicate_of: null });
  api.run.mockResolvedValue({ mode: 'simulated', exit_code: 0, jobs: [] } as never);
});

afterEach(() => vi.useRealTimers());

async function chooseInstagram(u: ReturnType<typeof user>) {
  renderWithApi(<IntakeStudio />);
  const box = await screen.findByRole('checkbox', { name: /Instagram/ });
  await waitFor(() => expect(box).toBeEnabled());
  await u.click(box);
  await waitFor(() => expect(box).toBeChecked());
}

describe('New post', () => {
  it('adds an Instagram post with the platform, after the backend check', async () => {
    const u = user();
    await chooseInstagram(u);
    expect(screen.getByText(/Instagram is test only for now/)).toBeInTheDocument();
    await u.type(screen.getByLabelText('Post name'), 'launch');
    await u.upload(document.querySelector('input[type="file"]') as HTMLInputElement, new File([JPEG], 'launch.jpg', { type: 'image/jpeg' }));
    fireEvent.change(screen.getByLabelText('Caption'), { target: { value: 'Hello' } });

    await waitFor(() => expect(api.checkPost).toHaveBeenCalledWith(expect.objectContaining({ caption: 'Hello', platform: 'instagram' })));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Add to queue' })).toBeEnabled());
    await u.click(screen.getByRole('button', { name: 'Add to queue' }));
    await waitFor(() => expect(api.addPost).toHaveBeenCalledWith(expect.objectContaining({ name: 'launch', platform: 'instagram' })));
  });

  it("shows the backend's refusal and does not add the post", async () => {
    const u = user();
    api.checkPost.mockResolvedValue({ platform: 'Instagram', ok: false, errors: ['the picture uses CMYK colours; save it as an RGB JPEG'] });
    await chooseInstagram(u);
    await u.type(screen.getByLabelText('Post name'), 'launch');
    await u.upload(document.querySelector('input[type="file"]') as HTMLInputElement, new File([JPEG], 'launch.jpg', { type: 'image/jpeg' }));
    fireEvent.change(screen.getByLabelText('Caption'), { target: { value: 'Hello' } });

    expect(await screen.findByText(/uses CMYK colours/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add to queue' })).toBeDisabled();
  });

  it('never offers Live for an Instagram schedule', async () => {
    const u = user();
    await chooseInstagram(u);
    await u.click(screen.getByRole('radio', { name: 'Schedule' }));
    expect(screen.getByRole('radio', { name: /Test/ })).toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: /Live/ })).not.toBeInTheDocument();
  });
});

describe('Publish and the queue', () => {
  it('runs Instagram as a test, with no confirmation phrase', async () => {
    const u = user();
    renderWithApi(
      <>
        <ScanQueueStation />
        <RunnerConsole />
      </>
    );
    await u.click(await screen.findByRole('radio', { name: /Instagram/ }));
    const button = await screen.findByRole('button', { name: 'Test 0 on Instagram' });
    expect(screen.queryByLabelText(/To confirm, type/)).not.toBeInTheDocument();
    await waitFor(() => expect(api.scan).toHaveBeenLastCalledWith(false, 'instagram'));
    await u.click(button);
    await waitFor(() => expect(api.run).toHaveBeenCalledWith({ real: false, publish_simulated: false, confirm: null, platform: 'instagram' }));
  });

  it('shows no platform picker when the poster offers LinkedIn only', async () => {
    api.getPlatforms.mockResolvedValue([LINKEDIN_FALLBACK]);
    api.scan.mockResolvedValue(scanOf([]));
    renderWithApi(<ScanQueueStation />);
    await waitFor(() => expect(api.scan).toHaveBeenCalledWith(true));
    expect(screen.queryByRole('radiogroup', { name: 'Platform' })).not.toBeInTheDocument();
  });
});

describe("Each platform's rules", () => {
  it('refuses a PNG and a long caption for Instagram only', async () => {
    const png = new File([PNG], 'a.png', { type: 'image/png' });
    expect(await imageErrors(png)).toEqual([]);
    expect(await imageErrors(png, INSTAGRAM)).toEqual(['Instagram accepts JPG pictures only.']);
    expect(captionError('a'.repeat(2201))).toBeNull();
    expect(captionError('a'.repeat(2201), INSTAGRAM)).toMatch(/longer than 2,200 characters, which Instagram/);
    expect(captionError('#a '.repeat(31), INSTAGRAM)).toMatch(/more than 30 hashtags/);
  });
});
