// Fix 3: "When: Now / Schedule" on the new post form, and fix 6's caption limit (3,000 blocks in every mode).
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IntakeStudio } from './IntakeStudio';
import { api, mockApi, renderWithApi, schedule, useFixedClock } from '../test/api';

vi.mock('../services/apiService');

const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0, 0x10, 0x4a, 0x46]);
const user = () => userEvent.setup();

beforeEach(() => {
  useFixedClock(); // 11:00 in Asia/Karachi
  mockApi();
  api.addPost.mockResolvedValue({ name: 'launch', files: { image: 'launch.jpg', caption: 'launch.txt' }, duplicate_of: null });
});

afterEach(() => vi.useRealTimers());

async function fillPost(u: ReturnType<typeof user>, caption = 'A caption.') {
  renderWithApi(<IntakeStudio />);
  await screen.findByRole('button', { name: 'Add to queue' });
  await u.type(screen.getByLabelText('Post name'), 'launch');
  await u.upload(document.querySelector('input[type="file"]') as HTMLInputElement, new File([JPEG], 'launch.jpg', { type: 'image/jpeg' }));
  fireEvent.change(screen.getByLabelText('Caption'), { target: { value: caption } });
}

const setTime = (date: string, time: string) => {
  fireEvent.change(screen.getByLabelText('Date'), { target: { value: date } });
  fireEvent.change(screen.getByLabelText('Time'), { target: { value: time } });
};

describe('When: Now', () => {
  it('adds the post without a schedule', async () => {
    const u = user();
    await fillPost(u);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Add to queue' })).toBeEnabled());
    await u.click(screen.getByRole('button', { name: 'Add to queue' }));
    await waitFor(() => expect(api.addPost).toHaveBeenCalledTimes(1));
    expect(api.createSchedule).not.toHaveBeenCalled();
  });
});

describe('When: Schedule', () => {
  it('adds the post and then schedules it, with the time exactly as typed', async () => {
    const u = user();
    api.createSchedule.mockResolvedValue({ schedule: schedule('launch'), warnings: [] });
    await fillPost(u);
    await u.click(screen.getByRole('radio', { name: 'Schedule' }));
    setTime('2026-11-21', '09:30');
    await u.click(screen.getByRole('button', { name: 'Add and schedule test' }));

    await waitFor(() => expect(api.createSchedule).toHaveBeenCalledTimes(1));
    expect(api.addPost).toHaveBeenCalledTimes(1);
    expect(api.addPost.mock.invocationCallOrder[0]).toBeLessThan(api.createSchedule.mock.invocationCallOrder[0]);
    expect(api.createSchedule).toHaveBeenCalledWith({ name: 'launch', publish_at: '2026-11-21 09:30', real: false, confirm: null });
    expect(screen.queryByText(/is not scheduled/)).not.toBeInTheDocument();
  });

  it('needs the typed phrase before scheduling it live', async () => {
    const u = user();
    api.createSchedule.mockResolvedValue({ schedule: schedule('launch', { mode: 'real' }), warnings: [] });
    await fillPost(u);
    await u.click(screen.getByRole('radio', { name: 'Schedule' }));
    await u.click(screen.getByRole('radio', { name: /Live/ }));
    const submit = screen.getByRole('button', { name: 'Add and schedule live' });
    expect(submit).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('To post it live, type publish to LinkedIn.');

    await u.type(screen.getByLabelText(/To confirm, type/), 'publish to LinkedIn');
    await waitFor(() => expect(submit).toBeEnabled());
    await u.click(submit);
    await waitFor(() =>
      expect(api.createSchedule).toHaveBeenCalledWith(expect.objectContaining({ real: true, confirm: 'publish to LinkedIn' }))
    );
  });

  it('refuses a time less than 5 minutes ahead before sending anything', async () => {
    const u = user();
    await fillPost(u);
    await u.click(screen.getByRole('radio', { name: 'Schedule' }));
    setTime('2026-11-20', '11:03');
    expect(screen.getByRole('button', { name: 'Add and schedule test' })).toBeDisabled();
    expect(screen.getByText(/at least 5 minutes from now/)).toBeInTheDocument();
  });

  it('keeps the post in the queue, unscheduled, and says why, when the schedule is refused', async () => {
    const u = user();
    api.createSchedule.mockRejectedValue({ status: 400, message: "'launch' is already handled by job 1a2b3c4d (SIMULATED, 2026-11-19)." });
    await fillPost(u);
    await u.click(screen.getByRole('radio', { name: 'Schedule' }));
    setTime('2026-11-21', '09:30');
    await u.click(screen.getByRole('button', { name: 'Add and schedule test' }));

    const alert = await screen.findByText(/was added, but it is not scheduled/);
    const box = alert.closest('[role="alert"]') as HTMLElement;
    expect(box).toHaveTextContent("'launch' was added, but it is not scheduled: 'launch' is already handled by job 1a2b3c4d");
    expect(box).toHaveTextContent('It is in your queue as an ordinary post, so it goes out the next time you publish.');
    expect(api.addPost).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Added 'launch', but not scheduled")).toBeInTheDocument(); // the toast
  });
});

describe('caption limit', () => {
  it('blocks a caption over 3,000 characters in every mode', async () => {
    const u = user();
    await fillPost(u, 'a'.repeat(3001));
    expect(screen.getByText(/longer than 3,000 characters/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add to queue' })).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Caption'), { target: { value: 'a'.repeat(3000) } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Add to queue' })).toBeEnabled());
  });
});
