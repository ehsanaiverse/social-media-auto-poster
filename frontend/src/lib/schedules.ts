// Scheduled posts (D46): wording and time helpers shared by the queue, the forms and the list.
// The backend checks every rule again; these only give instant feedback.

import type { ContentState, JobMode, ScheduleStatus } from '../types/api';
import { CONFIRM_PHRASE } from './postRules';

// The poster's time zone until the API says otherwise (AUTOPOSTER_TIMEZONE's default).
export const DEFAULT_TIMEZONE = 'Asia/Karachi';

// The page calls simulated posts "test" and real ones "live" (see ModeTag).
export const modeWord = (mode: JobMode): string => (mode === 'real' ? 'live' : 'test');

export const SCHEDULE_STATUS_LABEL: Record<ScheduleStatus, string> = {
  SCHEDULED: 'Scheduled',
  DONE: 'Done',
  INVALID: "Won't be posted",
  CANCELLED: 'Cancelled',
  MISSED: 'Missed'
};

// The same warnings the CLI's `schedules` and `scan` print, in the page's words.
export const CONTENT_WARNING: Record<Exclude<ContentState, 'ok'>, string> = {
  changed: 'CHANGED – will not be posted. The picture or caption changed after you scheduled it; re-confirm to post it as it is now.',
  missing: 'FILE MISSING – will not be posted. Put the file back and re-confirm, or cancel the schedule.',
  unreadable: "Couldn't read the files to check them. Try again in a moment."
};

export const contentWarning = (state: ContentState | null | undefined): string | null =>
  state && state !== 'ok' ? CONTENT_WARNING[state] : null;

// "2026-12-01 09:00" in `timeZone`, `addMinutes` from now. Formatting only: what the user types is never converted.
export function localNow(timeZone: string, addMinutes = 0, now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23'
  }).formatToParts(new Date(now.getTime() + addMinutes * 60_000));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`;
}

// "2026-12-01 09:00 Asia/Karachi" (or without the zone) -> its date and time fields.
export function splitLocal(local: string): { date: string; time: string } {
  const [date = '', time = ''] = local.trim().split(/\s+/);
  return { date, time };
}

export interface ScheduleChoice {
  date: string; // YYYY-MM-DD, from <input type="date">
  time: string; // HH:MM, from <input type="time">
  real: boolean;
  phrase: string;
}

export const MIN_LEAD_MINUTES = 5;
const MAX_AHEAD_MINUTES = 365 * 24 * 60;

// Why this choice cannot be sent yet, or null. Times are compared as "YYYY-MM-DD HH:MM" text in the same zone.
export function scheduleProblem(choice: ScheduleChoice, timeZone: string, now: Date = new Date()): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(choice.date) || !/^\d{2}:\d{2}$/.test(choice.time)) return 'Choose a date and a time.';
  const at = `${choice.date} ${choice.time}`;
  if (at < localNow(timeZone, MIN_LEAD_MINUTES, now)) return `Choose a time at least ${MIN_LEAD_MINUTES} minutes from now.`;
  if (at > localNow(timeZone, MAX_AHEAD_MINUTES, now)) return 'Choose a time within the next year.';
  if (choice.real && choice.phrase.trim() !== CONFIRM_PHRASE) return `To post it live, type ${CONFIRM_PHRASE}.`;
  return null;
}

// The request fields for a choice: the time exactly as typed, real only with the phrase.
export const scheduleFields = (choice: ScheduleChoice) => ({
  publish_at: `${choice.date} ${choice.time}`,
  real: choice.real,
  confirm: choice.real ? choice.phrase.trim() : null
});

// A fresh choice: test mode, `minutesAhead` from now in the poster's zone (or the given local time).
export function newChoice(timeZone: string, local?: string, minutesAhead = 60): ScheduleChoice {
  const { date, time } = splitLocal(local ?? localNow(timeZone, minutesAhead));
  return { date, time, real: false, phrase: '' };
}
