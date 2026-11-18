// Test helpers: data shaped like the API's answers, default answers for every call the page makes on load,
// and rendering inside the real ApiProvider. Each test file mocks '../services/apiService' (vi.mock), so no
// request ever leaves the test.
import React from 'react';
import { render } from '@testing-library/react';
import { vi } from 'vitest';
import { ApiProvider } from '../context/ApiContext';
import { ToastContainer } from '../components/ToastContainer';
import { apiService } from '../services/apiService';
import type { Candidate, ScanResponse, ScheduleItem, ScheduleListResponse } from '../types/api';

export const api = vi.mocked(apiService);

// 2026-11-20 11:00 in Asia/Karachi. Tests fake only Date, so timers and animations run as usual.
export const NOW = new Date('2026-11-20T06:00:00Z');

export function useFixedClock() {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
}

export const candidate = (name: string, extra: Partial<Candidate> = {}): Candidate => ({
  name,
  stable: true,
  valid: true,
  errors: [],
  duplicate_of: null,
  only_simulated: false,
  schedule: null,
  ...extra
});

export const schedule = (name: string, extra: Partial<ScheduleItem> = {}): ScheduleItem => ({
  schedule_id: `${name}-0000-4000-8000-000000000000`,
  name,
  mode: 'simulated',
  status: 'SCHEDULED',
  publish_at: '2026-11-21T04:00:00+00:00',
  publish_at_local: '2026-11-21 09:00 Asia/Karachi',
  content_state: 'ok',
  reason: null,
  job_id: null,
  created_at: '2026-11-19T10:00:00+00:00',
  ...extra
});

// A candidate whose post has this schedule (as GET /api/scan reports it).
export const scheduledCandidate = (s: ScheduleItem): Candidate =>
  candidate(s.name, {
    schedule: {
      schedule_id: s.schedule_id,
      mode: s.mode,
      publish_at: s.publish_at,
      publish_at_local: s.publish_at_local,
      content_state: s.content_state
    }
  });

export const scanOf = (candidates: Candidate[], extra: Partial<ScanResponse> = {}): ScanResponse => ({
  candidates,
  pending: [],
  ambiguous: [],
  warnings: [],
  missing_schedules: [],
  ...extra
});

export const schedulesOf = (schedules: ScheduleItem[]): ScheduleListResponse => ({
  timezone: 'Asia/Karachi',
  available: true,
  schedules
});

// The answers the page gets when it loads: online, this queue, these schedules, no jobs, no LinkedIn token.
export function mockApi({ scan = scanOf([]), schedules = schedulesOf([]) }: { scan?: ScanResponse; schedules?: ScheduleListResponse } = {}) {
  api.ping.mockResolvedValue(true);
  api.scan.mockResolvedValue(scan);
  api.getSchedules.mockResolvedValue(schedules);
  api.getJobs.mockResolvedValue([]);
  api.getLinkedInStatus.mockResolvedValue({ token_set: false, state: 'missing', expires_on: null, days_left: null });
}

export const renderWithApi = (ui: React.ReactNode) =>
  render(
    <ApiProvider>
      {ui}
      <ToastContainer />
    </ApiProvider>
  );
