// Fix 6: the Scheduled posts section, and the Schedule / Re-confirm dialogs.
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScanQueueStation } from './ScanQueueStation';
import { ScheduleModal } from './ScheduleModal';
import { ScheduledPosts } from './ScheduledPosts';
import { api, candidate, mockApi, renderWithApi, scanOf, schedule, schedulesOf, useFixedClock } from '../test/api';

vi.mock('../services/apiService');

const ok = schedule('later-post');
const changed = schedule('edited-post', { mode: 'real', content_state: 'changed', publish_at_local: '2026-11-22 18:30 Asia/Karachi' });
const invalid = schedule('broken-post', { status: 'INVALID', content_state: null, reason: 'content changed after scheduling' });
const done = schedule('sent-post', { status: 'DONE', content_state: null, job_id: 'job-1' });

beforeEach(() => {
  useFixedClock(); // 11:00 in Asia/Karachi
  mockApi({ scan: scanOf([candidate('ready-post')]), schedules: schedulesOf([ok, changed]) });
});

afterEach(() => vi.useRealTimers());

const renderPage = () =>
  renderWithApi(
    <>
      <ScanQueueStation />
      <ScheduledPosts />
      <ScheduleModal />
    </>
  );

const rowOf = async (name: string) => (await within(await screen.findByRole('list', { name: 'Scheduled posts' })).findByText(name)).closest('li') as HTMLElement;

// A dialog moves focus into itself on the next frame (ui/Modal). Wait for that, as a person would, before typing.
async function openedDialog(name: string) {
  const dialog = await screen.findByRole('dialog', { name });
  await waitFor(() => expect(dialog).toHaveFocus());
  return dialog;
}

describe('the scheduled posts list', () => {
  it('shows each schedule with its local time, mode, status and warnings', async () => {
    renderPage();
    const row = await rowOf('edited-post');
    expect(row).toHaveTextContent('2026-11-22 18:30 Asia/Karachi');
    expect(within(row).getByText('Live')).toBeInTheDocument();
    expect(within(row).getByText('Scheduled')).toBeInTheDocument();
    expect(within(row).getByText(/^CHANGED – will not be posted/)).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: /Re-confirm/ })).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: /Cancel/ })).toBeInTheDocument();

    const fine = await rowOf('later-post');
    expect(within(fine).getByText('Test')).toBeInTheDocument();
    expect(within(fine).queryByText(/CHANGED|FILE MISSING/)).not.toBeInTheDocument();
  });

  it('cancels exactly the schedule whose button was pressed', async () => {
    const u = userEvent.setup();
    api.cancelSchedule.mockResolvedValue({ schedule: { ...changed, status: 'CANCELLED' } });
    renderPage();
    await u.click(within(await rowOf('edited-post')).getByRole('button', { name: /Cancel/ }));
    await waitFor(() => expect(api.cancelSchedule).toHaveBeenCalledWith(changed.schedule_id));
    expect(api.cancelSchedule).toHaveBeenCalledTimes(1);
  });

  it('shows finished schedules under All: an invalid one can be re-confirmed, a done one cannot be changed', async () => {
    const u = userEvent.setup();
    api.getSchedules.mockImplementation(async (all = false) => schedulesOf(all ? [ok, changed, invalid, done] : [ok, changed]));
    renderPage();
    await rowOf('later-post');
    await u.click(screen.getByRole('tab', { name: 'All' }));
    const broken = await rowOf('broken-post');
    expect(within(broken).getByText("Won't be posted")).toBeInTheDocument();
    expect(broken).toHaveTextContent('content changed after scheduling');
    expect(within(broken).getByRole('button', { name: /Re-confirm/ })).toBeInTheDocument();
    expect(within(broken).queryByRole('button', { name: /Cancel/ })).not.toBeInTheDocument();
    const sent = await rowOf('sent-post');
    expect(within(sent).queryAllByRole('button')).toHaveLength(0);
  });
});

describe('the Re-confirm dialog', () => {
  it('keeps the old time, starts as test, and needs the phrase again for live', async () => {
    const u = userEvent.setup();
    api.reconfirmSchedule.mockResolvedValue({ schedule: { ...changed, schedule_id: 'new-id', content_state: 'ok' }, replaces: changed.schedule_id, warnings: [] });
    renderPage();
    await u.click(within(await rowOf('edited-post')).getByRole('button', { name: /Re-confirm/ }));

    const dialog = await openedDialog('Re-confirm this schedule');
    expect(within(dialog).getByText(/^CHANGED – will not be posted/)).toBeInTheDocument();
    expect(within(dialog).getByText(/This was a live schedule/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Date')).toHaveValue('2026-11-22');
    expect(within(dialog).getByLabelText('Time')).toHaveValue('18:30');
    expect(within(dialog).getByRole('radio', { name: /Test/ })).toBeChecked();

    await u.click(within(dialog).getByRole('radio', { name: /Live/ }));
    const submit = within(dialog).getByRole('button', { name: 'Re-confirm' });
    expect(submit).toBeDisabled();
    await u.type(within(dialog).getByLabelText(/To confirm, type/), 'publish to LinkedIn');
    await waitFor(() => expect(submit).toBeEnabled());
    await u.click(submit);

    await waitFor(() =>
      expect(api.reconfirmSchedule).toHaveBeenCalledWith(changed.schedule_id, {
        publish_at: '2026-11-22 18:30',
        real: true,
        confirm: 'publish to LinkedIn'
      })
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('stays open and shows the reason when the re-confirmation is refused', async () => {
    const u = userEvent.setup();
    api.reconfirmSchedule.mockRejectedValue({ status: 409, message: 'Another run is in progress; try again later.' });
    renderPage();
    await u.click(within(await rowOf('later-post')).getByRole('button', { name: /Re-confirm/ }));
    const dialog = await openedDialog('Re-confirm this schedule');
    await u.click(within(dialog).getByRole('button', { name: 'Re-confirm' }));
    expect(await screen.findByText('Another run is in progress; try again later.')).toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: 'Re-confirm this schedule' })).toBeInTheDocument();
  });
});

describe('the Schedule dialog from the queue', () => {
  it('schedules a ready post with the typed time, simulated unless live is chosen', async () => {
    const u = userEvent.setup();
    api.createSchedule.mockResolvedValue({ schedule: schedule('ready-post'), warnings: [] });
    renderPage();
    const row = (await screen.findByRole('heading', { name: 'ready-post' })).closest('li') as HTMLElement;
    await u.click(within(row).getByRole('button', { name: /Schedule/ }));

    const dialog = await openedDialog('Schedule this post');
    expect(within(dialog).getByText(/Asia\/Karachi time/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Date')).toHaveValue('2026-11-20'); // one hour ahead by default
    expect(within(dialog).getByLabelText('Time')).toHaveValue('12:00');
    fireEvent.change(within(dialog).getByLabelText('Date'), { target: { value: '2026-12-01' } });
    fireEvent.change(within(dialog).getByLabelText('Time'), { target: { value: '09:00' } });
    await u.click(within(dialog).getByRole('button', { name: 'Schedule test' }));

    await waitFor(() =>
      expect(api.createSchedule).toHaveBeenCalledWith({ name: 'ready-post', publish_at: '2026-12-01 09:00', real: false, confirm: null })
    );
  });
});
