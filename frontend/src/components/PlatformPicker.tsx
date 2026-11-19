import React from 'react';
import { useApi } from '../context/ApiContext';
import { platformBadge, platformById } from '../lib/platforms';
import { cn } from '../utils/cn';

// One platform choice shared by the queue, the scheduled posts, publishing and the history.
// Shown only when the poster offers more than one platform (GET /api/platforms).
export const PlatformPicker: React.FC = () => {
  const { platforms, platform, setPlatform } = useApi();
  if (platforms.length < 2) return null;

  return (
    <div className="mb-5 flex flex-wrap items-center gap-2" role="radiogroup" aria-label="Platform">
      {platforms.map((p) => {
        const { Icon } = platformById(p.id);
        const selected = p.id === platform;
        return (
          <button
            key={p.id}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => setPlatform(p.id)}
            className={cn(
              'inline-flex min-h-9 items-center gap-1.5 rounded-lg px-3 text-xs font-medium ring-1 ring-inset transition-colors',
              selected ? 'bg-emerald-500/15 text-emerald-200 ring-emerald-500/40' : 'bg-white/[0.03] text-slate-300 ring-white/10 hover:bg-white/[0.06]'
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            {p.name}
            {platformBadge(platformById(p.id).id, platforms) === 'test' && <span className="text-[10px] uppercase tracking-wide text-slate-400">test only</span>}
          </button>
        );
      })}
    </div>
  );
};
