import { describe, expect, it } from 'vitest';
import { NOW } from '../test/api';
import { contentWarning, localNow, newChoice, scheduleFields, scheduleProblem, splitLocal } from './schedules';

const ZONE = 'Asia/Karachi';
const at = (date: string, time: string, real = false, phrase = '') => ({ date, time, real, phrase });

describe('times are read in the poster zone and sent as typed', () => {
  it('formats now in the poster zone, not the browser zone', () => {
    expect(localNow(ZONE, 0, NOW)).toBe('2026-11-20 11:00');
    expect(localNow('UTC', 0, NOW)).toBe('2026-11-20 06:00');
    expect(localNow(ZONE, 60, NOW)).toBe('2026-11-20 12:00');
  });

  it('splits a local time with or without its zone', () => {
    expect(splitLocal('2026-12-01 09:00 Asia/Karachi')).toEqual({ date: '2026-12-01', time: '09:00' });
    expect(splitLocal('2026-12-01 09:00')).toEqual({ date: '2026-12-01', time: '09:00' });
  });

  it('sends the time exactly as typed, real only with the phrase', () => {
    expect(scheduleFields(at('2026-12-01', '09:00'))).toEqual({ publish_at: '2026-12-01 09:00', real: false, confirm: null });
    expect(scheduleFields(at('2026-12-01', '09:00', true, ' publish to LinkedIn '))).toEqual({
      publish_at: '2026-12-01 09:00',
      real: true,
      confirm: 'publish to LinkedIn'
    });
  });

  it('starts a new choice as a test one hour ahead, or at the old time for a re-confirmation', () => {
    expect(newChoice(ZONE)).toMatchObject({ real: false, phrase: '' });
    expect(newChoice(ZONE, '2026-12-01 09:00 Asia/Karachi')).toEqual(at('2026-12-01', '09:00'));
  });
});

describe('scheduleProblem gives instant feedback (the backend decides)', () => {
  it('needs both a date and a time', () => {
    expect(scheduleProblem(at('', '09:00'), ZONE, NOW)).toBe('Choose a date and a time.');
    expect(scheduleProblem(at('2026-12-01', ''), ZONE, NOW)).toBe('Choose a date and a time.');
  });

  it('needs at least 5 minutes ahead and at most a year', () => {
    expect(scheduleProblem(at('2026-11-20', '11:04'), ZONE, NOW)).toMatch(/at least 5 minutes/);
    expect(scheduleProblem(at('2026-11-20', '11:05'), ZONE, NOW)).toBeNull();
    expect(scheduleProblem(at('2027-11-21', '11:00'), ZONE, NOW)).toMatch(/within the next year/);
  });

  it('needs the exact phrase for live', () => {
    expect(scheduleProblem(at('2026-12-01', '09:00', true, ''), ZONE, NOW)).toMatch(/type publish to LinkedIn/);
    expect(scheduleProblem(at('2026-12-01', '09:00', true, 'Publish to linkedin'), ZONE, NOW)).toMatch(/type publish to LinkedIn/);
    expect(scheduleProblem(at('2026-12-01', '09:00', true, 'publish to LinkedIn'), ZONE, NOW)).toBeNull();
  });
});

describe('contentWarning', () => {
  it('warns about changed and missing files only', () => {
    expect(contentWarning('ok')).toBeNull();
    expect(contentWarning(null)).toBeNull();
    expect(contentWarning('changed')).toMatch(/^CHANGED – will not be posted/);
    expect(contentWarning('missing')).toMatch(/^FILE MISSING – will not be posted/);
  });
});
