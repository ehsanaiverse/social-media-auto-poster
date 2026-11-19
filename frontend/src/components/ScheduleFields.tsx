import React from 'react';
import { FlaskConical, Rocket } from 'lucide-react';
import { useApi } from '../context/ApiContext';
import { type ScheduleChoice } from '../lib/schedules';
import { cn } from '../utils/cn';

interface ScheduleFieldsProps {
  idPrefix: string;
  choice: ScheduleChoice;
  onChange: (choice: ScheduleChoice) => void;
  timeZone: string;
  problem?: string | null; // Shown under the fields once the user has tried to send.
}

// Date and time in the poster's time zone (sent exactly as typed), Test or Live, and the typed phrase for Live.
// The radio cards follow ResolveModal; the phrase box follows the Publish section.
export const ScheduleFields: React.FC<ScheduleFieldsProps> = ({ idPrefix, choice, onChange, timeZone, problem }) => {
  const set = (patch: Partial<ScheduleChoice>) => onChange({ ...choice, ...patch });
  const { activePlatform } = useApi();
  const phrase = activePlatform.confirmation_phrase ?? '';
  const phraseOk = choice.phrase.trim() === phrase;
  const allModes = [
    { real: false, icon: FlaskConical, title: 'Test', text: 'A practice run at that time. Nothing is posted.', tone: 'emerald' },
    { real: true, icon: Rocket, title: 'Live', text: `Posted to your ${activePlatform.name} at that time.`, tone: 'amber' }
  ] as const;
  // Instagram is test only (D49): Live is not offered at all.
  const modes = activePlatform.real_available ? allModes : allModes.filter((m) => !m.real);

  return (
    <div className="space-y-4">
      <fieldset>
        <legend className="mb-2 text-sm text-slate-300">
          Date and time <span className="text-xs text-slate-500">({timeZone} time)</span>
        </legend>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label htmlFor={`${idPrefix}-date`} className="sr-only">
              Date
            </label>
            <input
              id={`${idPrefix}-date`}
              type="date"
              value={choice.date}
              onChange={(e) => set({ date: e.target.value })}
              className="field text-sm"
            />
          </div>
          <div>
            <label htmlFor={`${idPrefix}-time`} className="sr-only">
              Time
            </label>
            <input
              id={`${idPrefix}-time`}
              type="time"
              value={choice.time}
              onChange={(e) => set({ time: e.target.value })}
              className="field text-sm"
            />
          </div>
        </div>
        <p className="mt-1.5 text-xs text-slate-500">
          A scheduled run posts it at or after this time, as long as your PC is on. At least 5 minutes from now.
        </p>
      </fieldset>

      <fieldset>
        <legend className="mb-2 text-sm text-slate-300">Test or live</legend>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {modes.map((m) => {
            const Icon = m.icon;
            const sel = choice.real === m.real;
            return (
              <label
                key={m.title}
                className={cn(
                  'relative flex cursor-pointer flex-col gap-1.5 rounded-xl border p-4 transition-colors',
                  sel
                    ? m.tone === 'emerald'
                      ? 'border-emerald-500/50 bg-emerald-500/[0.07]'
                      : 'border-amber-500/50 bg-amber-500/[0.07]'
                    : 'border-white/[0.08] bg-black/20 hover:border-white/15'
                )}
              >
                <input
                  type="radio"
                  name={`${idPrefix}-mode`}
                  value={m.title}
                  checked={sel}
                  onChange={() => set({ real: m.real, phrase: m.real ? choice.phrase : '' })}
                  className="sr-only"
                />
                <span className="flex items-center gap-2 text-sm font-medium text-slate-100">
                  <Icon className={cn('h-4 w-4', m.tone === 'emerald' ? 'text-emerald-400' : 'text-amber-400')} aria-hidden="true" />
                  {m.title}
                </span>
                <span className="text-xs leading-relaxed text-slate-400">{m.text}</span>
              </label>
            );
          })}
        </div>
      </fieldset>

      {choice.real && (
        <div>
          <label htmlFor={`${idPrefix}-phrase`} className="mb-1.5 block text-sm text-slate-300">
            To confirm, type <span className="font-medium text-emerald-300">{phrase}</span>
          </label>
          <input
            id={`${idPrefix}-phrase`}
            type="text"
            value={choice.phrase}
            onChange={(e) => set({ phrase: e.target.value })}
            autoComplete="off"
            spellCheck={false}
            className={cn('field text-sm', phraseOk && 'border-emerald-400/70')}
          />
          <p className="mt-1.5 text-xs text-slate-500">Scheduling it live is your confirmation. It is posted only if the picture and caption stay exactly as they are now.</p>
        </div>
      )}

      {problem && (
        <p className="text-xs text-amber-300" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
};
