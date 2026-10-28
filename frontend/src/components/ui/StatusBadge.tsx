import React from 'react';
import { AlertTriangle, CheckCircle2, FlaskConical, Loader2, XCircle, type LucideIcon } from 'lucide-react';
import type { JobStatus } from '../../types/api';
import { cn } from '../../utils/cn';

// Plain-language names for the backend's statuses.
export const STATUS_LABEL: Record<JobStatus, string> = {
  PUBLISHED: 'Posted',
  SIMULATED: 'Test run',
  NEEDS_REVIEW: 'Check needed',
  FAILED: 'Not posted',
  PUBLISHING: 'Posting…'
};

const STYLES: Record<JobStatus, { icon: LucideIcon; className: string }> = {
  PUBLISHED: { icon: CheckCircle2, className: 'border-emerald-700/50 bg-emerald-950/70 text-emerald-300' },
  SIMULATED: { icon: FlaskConical, className: 'border-violet-700/50 bg-violet-950/70 text-violet-300' },
  NEEDS_REVIEW: { icon: AlertTriangle, className: 'border-amber-700/50 bg-amber-950/70 text-amber-300' },
  FAILED: { icon: XCircle, className: 'border-rose-700/50 bg-rose-950/70 text-rose-300' },
  PUBLISHING: { icon: Loader2, className: 'border-sky-700/50 bg-sky-950/70 text-sky-300' }
};

export const StatusBadge: React.FC<{ status: JobStatus; className?: string }> = ({ status, className }) => {
  const style = STYLES[status] ?? STYLES.FAILED;
  const Icon = style.icon;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-medium',
        style.className,
        className
      )}
    >
      {status === 'NEEDS_REVIEW' && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-60" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-amber-400" />
        </span>
      )}
      <Icon className={cn('h-3.5 w-3.5', status === 'PUBLISHING' && 'animate-spin')} aria-hidden="true" />
      <span>{STATUS_LABEL[status] ?? status}</span>
    </span>
  );
};

export const ModeTag: React.FC<{ mode: 'simulated' | 'real' }> = ({ mode }) => (
  <span
    className={cn(
      'inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium',
      mode === 'real' ? 'bg-amber-500/10 text-amber-300 ring-1 ring-amber-500/25' : 'bg-white/5 text-slate-400 ring-1 ring-white/10'
    )}
  >
    {mode === 'real' ? 'Live' : 'Test'}
  </span>
);
