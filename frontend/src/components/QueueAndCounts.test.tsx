// Fixes 1 and 2: a scheduled post is not "ready" in the queue, and no "ready" count includes it.
import { screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { HeroSection } from './HeroSection';
import { RunnerConsole } from './RunnerConsole';
import { ScanQueueStation } from './ScanQueueStation';
import { mockApi, renderWithApi, scanOf, schedule, scheduledCandidate, schedulesOf, candidate } from '../test/api';

vi.mock('../services/apiService');
vi.mock('./3d/NetworkGlobe3D', () => ({ default: () => null }));

const later = schedule('later-post');
const edited = schedule('edited-post', { mode: 'real', content_state: 'changed', publish_at_local: '2026-11-22 18:30 Asia/Karachi' });
const gone = schedule('gone-post', { content_state: 'missing' });

beforeEach(() => {
  mockApi({
    scan: scanOf([candidate('ready-post'), scheduledCandidate(later), scheduledCandidate(edited)], { missing_schedules: [gone] }),
    schedules: schedulesOf([later, edited, gone])
  });
});

const rowOf = async (name: string) => (await screen.findByRole('heading', { name })).closest('li') as HTMLElement;

describe('the queue', () => {
  it('shows a scheduled post with its time and mode, never as ready', async () => {
    renderWithApi(<ScanQueueStation />);
    const row = await rowOf('later-post');
    expect(within(row).getByText('Scheduled for 2026-11-21 09:00 Asia/Karachi (test).')).toBeInTheDocument();
    expect(within(row).getByText('Scheduled', { selector: 'span' })).toBeInTheDocument();
    expect(within(row).queryByText(/Goes out the next time you publish/)).not.toBeInTheDocument();
    expect(within(row).queryByRole('button', { name: /Publish/ })).not.toBeInTheDocument();

    const ready = await rowOf('ready-post');
    expect(within(ready).getByText('Goes out the next time you publish.')).toBeInTheDocument();
    expect(within(ready).getByRole('button', { name: /Schedule/ })).toBeInTheDocument();
  });

  it('warns when a scheduled post changed or its files are gone', async () => {
    renderWithApi(<ScanQueueStation />);
    const row = await rowOf('edited-post');
    expect(within(row).getByText('Scheduled for 2026-11-22 18:30 Asia/Karachi (live).')).toBeInTheDocument();
    expect(within(row).getByText(/^CHANGED – will not be posted/)).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: /Re-confirm or cancel/ })).toBeInTheDocument();
    expect(within(await rowOf('later-post')).queryByText(/CHANGED/)).not.toBeInTheDocument();

    const notice = screen.getByText(/Scheduled, but its files are gone/).closest('div') as HTMLElement;
    expect(within(notice).getByText('gone-post: FILE MISSING, scheduled for 2026-11-21 09:00 Asia/Karachi (test)')).toBeInTheDocument();
  });

  it('counts only unscheduled posts as ready, and shows a Scheduled count', async () => {
    renderWithApi(<ScanQueueStation />);
    expect(await screen.findByRole('button', { name: /Publish 1 ready/ })).toBeEnabled();
    const scheduledTab = screen.getByRole('button', { name: /^Scheduled/, pressed: false });
    await waitFor(() => expect(scheduledTab).toHaveTextContent('Scheduled2'));
    expect(screen.getByRole('button', { name: /^Ready/, pressed: false })).toHaveTextContent('Ready1');
  });

  it('shows the file names the API sends (media_name, caption_name) as chips', async () => {
    // Names as GET /api/scan sends them: pairing ignores case, so they can differ from the post name.
    mockApi({ scan: scanOf([candidate('photo-post', { media_name: 'Photo-Post.PNG', caption_name: 'photo-post.txt' })]) });
    renderWithApi(<ScanQueueStation />);
    const row = await rowOf('photo-post');
    expect(within(row).getByText('Photo-Post.PNG')).toBeInTheDocument();
    expect(within(row).getByText('photo-post.txt')).toBeInTheDocument();
  });

  it('says nothing is ready when every post is scheduled', async () => {
    mockApi({ scan: scanOf([scheduledCandidate(later)]), schedules: schedulesOf([later]) });
    renderWithApi(<ScanQueueStation />);
    expect(await screen.findByRole('button', { name: 'Nothing ready' })).toBeDisabled();
  });
});

describe('Publish and Home', () => {
  it('posts only the unscheduled post and says the scheduled ones are left out', async () => {
    renderWithApi(<RunnerConsole />);
    expect(await screen.findByRole('button', { name: 'Post 1 to LinkedIn' })).toBeInTheDocument();
    expect(screen.getByText('2 scheduled posts not included: they go out at their own time.')).toBeInTheDocument();
  });

  it('shows Ready to post without scheduled posts, and a Scheduled card', async () => {
    renderWithApi(<HeroSection />);
    const card = (label: string) => screen.getByText(label).closest('[role="button"]') as HTMLElement;
    await waitFor(() => expect(card('Ready to post')).toHaveTextContent(/Ready to post\s*1/));
    await waitFor(() => expect(card('Scheduled')).toHaveTextContent(/Scheduled\s*3/));
    // Two of the three need re-confirming (changed, missing), so the card says so.
    expect(card('Scheduled')).toHaveTextContent('2 need re-confirming');
  });
});
