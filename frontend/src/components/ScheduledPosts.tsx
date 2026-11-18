import React, { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertTriangle, CalendarClock, RefreshCw, RotateCcw, XCircle } from 'lucide-react';
import { useApi, type ScheduleView } from '../context/ApiContext';
import { contentWarning } from '../lib/schedules';
import type { ScheduleItem } from '../types/api';
import { cn } from '../utils/cn';
import { SectionHeading } from './ui/SectionHeading';
import { ModeTag, ScheduleBadge } from './ui/StatusBadge';
import { EASE_OUT, Reveal } from './ui/motion';

const VIEWS: { id: ScheduleView; label: string }[] = [
  { id: 'active', label: 'Waiting' },
  { id: 'all', label: 'All' }
];

const ScheduleRow: React.FC<{ schedule: ScheduleItem; index: number }> = ({ schedule: s, index }) => {
  const { cancelSchedule, setScheduleDialog, connection } = useApi();
  const [busy, setBusy] = useState(false);
  const warning = s.status === 'SCHEDULED' ? contentWarning(s.content_state) : null;
  const canCancel = s.status === 'SCHEDULED';
  const canReconfirm = s.status === 'SCHEDULED' || s.status === 'INVALID';
  const offline = connection !== 'online';

  const cancel = async () => {
    setBusy(true);
    await cancelSchedule(s);
    setBusy(false);
  };

  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.35, ease: EASE_OUT, delay: Math.min(index, 8) * 0.04 }}
      className="flex flex-wrap items-center justify-between gap-3 px-5 py-4"
    >
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-teal-500/10 text-teal-300 ring-1 ring-inset ring-teal-400/25">
        <CalendarClock className="h-4 w-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium text-slate-100">{s.name}</p>
        <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
          {s.publish_at_local} <ModeTag mode={s.mode} />
        </p>
        {s.reason && s.status !== 'SCHEDULED' && <p className="mt-1 text-xs text-slate-500">{s.reason}</p>}
        {warning && (
          <p className="mt-2 flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2 text-xs text-amber-100" role="status">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-300" aria-hidden="true" />
            {warning}
          </p>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {canReconfirm && (
          <button
            type="button"
            onClick={() => setScheduleDialog({ kind: 'reconfirm', schedule: s })}
            disabled={offline}
            className={cn('btn min-h-9 text-xs', warning || s.status === 'INVALID' ? 'btn-primary' : 'btn-ghost')}
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" /> Re-confirm
          </button>
        )}
        {canCancel && (
          <button type="button" onClick={() => void cancel()} disabled={busy || offline} className="btn btn-ghost min-h-9 border-rose-500/40 text-xs text-rose-200">
            <XCircle className="h-3.5 w-3.5" aria-hidden="true" /> Cancel
          </button>
        )}
        <ScheduleBadge status={s.status} />
      </div>
    </motion.li>
  );
};

export const ScheduledPosts: React.FC = () => {
  const { schedules, allSchedules, scheduleView, setScheduleView, refreshSchedules, isLoadingSchedules, connection, scheduleTimezone } = useApi();
  const list = scheduleView === 'all' ? allSchedules : schedules;

  return (
    <section id="scheduled" className="relative py-16 lg:py-20">
      <div className="hr-fade absolute inset-x-0 top-0" />
      <div className="page-container">
        <SectionHeading index="02b" icon={CalendarClock} eyebrow="Scheduled" title="Posts waiting for their time">
          Each one goes out at or after its time ({scheduleTimezone}) through a scheduled run, as long as your PC is on. Publishing from this page
          never sends them early.
        </SectionHeading>

        <Reveal className="mb-5 flex items-center justify-between gap-3">
          <div className="segmented" role="tablist" aria-label="Which schedules">
            {VIEWS.map((v) => (
              <button
                key={v.id}
                type="button"
                role="tab"
                aria-selected={scheduleView === v.id}
                onClick={() => setScheduleView(v.id)}
                className={cn('relative min-h-9 whitespace-nowrap rounded-lg px-3.5 text-xs font-medium transition-colors', scheduleView === v.id ? 'text-slate-50' : 'text-slate-400 hover:text-slate-200')}
              >
                {scheduleView === v.id && <motion.span layoutId="schedule-view" className="absolute inset-0 rounded-lg bg-white/10 ring-1 ring-inset ring-white/10" transition={{ type: 'spring', stiffness: 500, damping: 38 }} />}
                <span className="relative">{v.label}</span>
              </button>
            ))}
          </div>
          <button type="button" onClick={() => void refreshSchedules()} disabled={isLoadingSchedules || connection !== 'online'} aria-label="Refresh scheduled posts" className="btn btn-ghost w-11 px-0">
            <RefreshCw className={cn('h-4 w-4', isLoadingSchedules && 'animate-spin text-emerald-400')} />
          </button>
        </Reveal>

        <Reveal className="surface overflow-hidden">
          {list.length === 0 ? (
            <p className="px-6 py-16 text-center text-sm text-slate-500">
              {connection !== 'online'
                ? 'Not connected. Start your poster to see your scheduled posts.'
                : isLoadingSchedules
                  ? 'Loading…'
                  : scheduleView === 'active'
                    ? 'Nothing scheduled. Choose “Schedule” on a ready post in your queue, or when you create one.'
                    : 'No schedules yet.'}
            </p>
          ) : (
            <ul className="divide-y divide-white/[0.05]" aria-label="Scheduled posts">
              <AnimatePresence initial={false}>
                {list.map((s, i) => (
                  <ScheduleRow key={s.schedule_id} schedule={s} index={i} />
                ))}
              </AnimatePresence>
            </ul>
          )}
        </Reveal>
      </div>
    </section>
  );
};
