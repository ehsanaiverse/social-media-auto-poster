import React, { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ChevronRight, History, RefreshCw, Search, Wrench } from 'lucide-react';
import { useApi } from '../context/ApiContext';
import type { JobSummary } from '../types/api';
import { platformById } from '../lib/platforms';
import { cn } from '../utils/cn';
import { SectionHeading } from './ui/SectionHeading';
import { ModeTag, STATUS_LABEL, StatusBadge } from './ui/StatusBadge';
import { EASE_OUT, Reveal } from './ui/motion';

const FILTERS = ['ALL', 'PUBLISHED', 'NEEDS_REVIEW', 'FAILED', 'SIMULATED'] as const;

const PlatformDot: React.FC<{ platform?: string }> = ({ platform }) => {
  const p = platformById(platform);
  return (
    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl text-white ring-1 ring-inset ring-white/10" style={{ backgroundColor: `${p.color}26` }} title={p.name}>
      <p.Icon className="h-4 w-4" />
    </span>
  );
};

const formatTime = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

export const JobsLedger: React.FC = () => {
  const { jobs, isLoadingJobs, jobStatusFilter, setJobStatusFilter, refreshJobs, openJob, setResolvingJob, connection } = useApi();
  const [query, setQuery] = useState('');

  const q = query.trim().toLowerCase();
  const visible = q ? jobs.filter((j) => j.stem.toLowerCase().includes(q)) : jobs;

  const ResolveButton: React.FC<{ job: JobSummary }> = ({ job }) =>
    job.status === 'NEEDS_REVIEW' ? (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setResolvingJob(job);
        }}
        className="inline-flex min-h-9 items-center gap-1.5 rounded-lg bg-amber-500/15 px-3 text-xs font-medium text-amber-200 ring-1 ring-inset ring-amber-500/30 transition-colors hover:bg-amber-500/25"
      >
        <Wrench className="h-3.5 w-3.5" aria-hidden="true" /> Check now
      </button>
    ) : null;

  return (
    <section id="jobs" className="relative py-16 lg:py-20">
      <div className="hr-fade absolute inset-x-0 top-0" />
      <div className="page-container">
        <SectionHeading index="05" icon={History} eyebrow="History" title="Everything you've published">
          Newest first. Tap a post to see its details.
        </SectionHeading>

        <Reveal className="mb-5 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="-mx-4 overflow-x-auto px-4 lg:mx-0 lg:px-0">
            <div className="segmented" role="tablist" aria-label="Filter">
              {FILTERS.map((f) => (
                <button
                  key={f}
                  type="button"
                  role="tab"
                  aria-selected={jobStatusFilter === f}
                  onClick={() => setJobStatusFilter(f)}
                  className={cn('relative min-h-9 whitespace-nowrap rounded-lg px-3.5 text-xs font-medium transition-colors', jobStatusFilter === f ? 'text-slate-50' : 'text-slate-400 hover:text-slate-200')}
                >
                  {jobStatusFilter === f && <motion.span layoutId="job-filter" className="absolute inset-0 rounded-lg bg-white/10 ring-1 ring-inset ring-white/10" transition={{ type: 'spring', stiffness: 500, damping: 38 }} />}
                  <span className="relative">{f === 'ALL' ? 'All' : STATUS_LABEL[f]}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="flex gap-2">
            <div className="relative flex-1 lg:w-72" role="search">
              <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" aria-hidden="true" />
              <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by name" aria-label="Search by name" className="field min-h-11 pl-10 text-sm" />
            </div>
            <button type="button" onClick={() => void refreshJobs()} disabled={isLoadingJobs || connection !== 'online'} aria-label="Refresh" className="btn btn-ghost w-11 px-0">
              <RefreshCw className={cn('h-4 w-4', isLoadingJobs && 'animate-spin text-emerald-400')} />
            </button>
          </div>
        </Reveal>

        <Reveal className="surface overflow-hidden">
          {visible.length === 0 ? (
            <p className="px-6 py-16 text-center text-sm text-slate-500">{connection !== 'online' ? 'Not connected. Start your poster to see your history.' : isLoadingJobs ? 'Loading…' : 'Nothing here yet.'}</p>
          ) : (
            <ul className="divide-y divide-white/[0.05]">
              <AnimatePresence initial={false}>
                {visible.map((job, i) => (
                  <motion.li
                    key={job.job_id}
                    layout
                    initial={{ opacity: 0, y: 8 }}
                    whileInView={{ opacity: 1, y: 0 }}
                    viewport={{ once: true }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.35, ease: EASE_OUT, delay: Math.min(i, 8) * 0.04 }}
                    onClick={() => void openJob(job.job_id)}
                    className="group flex cursor-pointer flex-wrap items-center justify-between gap-3 px-5 py-4 transition-colors hover:bg-white/[0.03]"
                  >
                    <PlatformDot platform={(job as { platform?: string }).platform} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium text-slate-100">{job.stem}</p>
                      <p className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                        {formatTime(job.created_at)} <ModeTag mode={job.mode} />
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <ResolveButton job={job} />
                      <StatusBadge status={job.status} />
                      <ChevronRight className="h-4 w-4 text-slate-600 transition-transform group-hover:translate-x-0.5 group-hover:text-slate-300" aria-hidden="true" />
                    </div>
                  </motion.li>
                ))}
              </AnimatePresence>
            </ul>
          )}
        </Reveal>
      </div>
    </section>
  );
};
