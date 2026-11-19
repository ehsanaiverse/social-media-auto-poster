import React, { useEffect, useState } from 'react';
import { AlertTriangle, CalendarClock, Loader2 } from 'lucide-react';
import { useApi, type ScheduleDialog } from '../context/ApiContext';
import { contentWarning, newChoice, scheduleFields, scheduleProblem, type ScheduleChoice } from '../lib/schedules';
import { ScheduleFields } from './ScheduleFields';
import { Modal, useLatest } from './ui/Modal';
import { ModeTag } from './ui/StatusBadge';

const keyOf = (d: ScheduleDialog | null) => (d ? (d.kind === 'create' ? `create:${d.name}` : `reconfirm:${d.schedule.schedule_id}`) : '');

// Schedule a post from the queue, or re-confirm a schedule (a new one for the files as they are now).
export const ScheduleModal: React.FC = () => {
  const { scheduleDialog, setScheduleDialog, scheduleTimezone, createSchedule, reconfirmSchedule, activePlatform } = useApi();
  const dialog = useLatest(scheduleDialog);
  const [choice, setChoice] = useState<ScheduleChoice>(() => newChoice(scheduleTimezone));
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Start fresh for every dialog. A re-confirmation keeps the old time but never the old mode: live is chosen again.
  const key = keyOf(dialog);
  useEffect(() => {
    if (!dialog) return;
    setChoice(newChoice(scheduleTimezone, dialog.kind === 'reconfirm' ? dialog.schedule.publish_at_local : undefined));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const reconfirm = dialog?.kind === 'reconfirm' ? dialog.schedule : null;
  const name = dialog ? (dialog.kind === 'create' ? dialog.name : dialog.schedule.name) : '';
  const problem = scheduleProblem(choice, scheduleTimezone, undefined, activePlatform.confirmation_phrase ?? undefined);
  const warning = reconfirm ? contentWarning(reconfirm.content_state) : null;

  const close = () => setScheduleDialog(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!dialog || !scheduleDialog || problem) return;
    setIsSubmitting(true);
    if (dialog.kind === 'create') await createSchedule(dialog.name, scheduleFields(choice));
    else await reconfirmSchedule(dialog.schedule, scheduleFields(choice));
    setIsSubmitting(false);
  };

  return (
    <Modal
      open={Boolean(scheduleDialog)}
      onClose={close}
      className="sm:max-w-lg"
      icon={
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-teal-500/15 text-teal-300 ring-1 ring-inset ring-teal-500/30">
          <CalendarClock className="h-4 w-4" />
        </span>
      }
      title={reconfirm ? 'Re-confirm this schedule' : 'Schedule this post'}
      subtitle={name}
    >
      {dialog && (
        <form onSubmit={submit} noValidate className="space-y-5 p-6">
          <div className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
            <span className="truncate font-mono text-sm text-slate-200">{name}</span>
            {reconfirm && <ModeTag mode={reconfirm.mode} />}
          </div>

          {reconfirm && (
            <div className="space-y-2 text-sm leading-relaxed text-slate-300">
              {warning && (
                <p className="flex items-start gap-2 rounded-xl border border-amber-500/25 bg-amber-500/[0.06] p-3 text-amber-100">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden="true" />
                  {warning}
                </p>
              )}
              <p>Re-confirming replaces this schedule with a new one for the picture and caption as they are now.</p>
              {reconfirm.mode === 'real' && (
                <p className="text-xs text-amber-300">This was a live schedule. Live is never carried over: choose Live and type the phrase again to keep it live.</p>
              )}
            </div>
          )}

          <ScheduleFields idPrefix="schedule-dialog" choice={choice} onChange={setChoice} timeZone={scheduleTimezone} problem={problem} />

          <div className="flex justify-end gap-3 pt-1">
            <button type="button" onClick={close} className="btn btn-ghost min-h-10 text-xs">
              Cancel
            </button>
            <button type="submit" disabled={Boolean(problem) || isSubmitting} className="btn btn-primary min-h-10 text-xs">
              {isSubmitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {reconfirm ? 'Re-confirm' : choice.real ? 'Schedule live' : 'Schedule test'}
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
};
