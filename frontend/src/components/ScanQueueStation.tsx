import React, { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock,
  Copy,
  FileImage,
  FileText,
  FlaskConical,
  FolderInput,
  Play,
  RefreshCw,
  ScanLine,
  XCircle,
  type LucideIcon
} from 'lucide-react';
import { useApi } from '../context/ApiContext';
import { scrollToSection } from '../lib/sections';
import { countOf, type Candidate } from '../types/api';
import { cn } from '../utils/cn';
import { SectionHeading } from './ui/SectionHeading';
import { AnimatedNumber, EASE_OUT, Reveal } from './ui/motion';

type CandidateState = 'ready' | 'waiting' | 'invalid' | 'duplicate';
type Filter = 'all' | CandidateState;

const STATE: Record<CandidateState, { label: string; icon: LucideIcon; hex: string; text: string; tile: string; note: string }> = {
  ready: { label: 'Ready', icon: CheckCircle2, hex: '#34d399', text: 'text-emerald-300', tile: 'bg-emerald-500/10 ring-emerald-400/25', note: 'Goes out the next time you publish.' },
  waiting: { label: 'Waiting', icon: Clock, hex: '#38bdf8', text: 'text-sky-300', tile: 'bg-sky-500/10 ring-sky-400/25', note: "Still uploading. We'll pick it up automatically." },
  invalid: { label: 'Has a problem', icon: XCircle, hex: '#fb7185', text: 'text-rose-300', tile: 'bg-rose-500/10 ring-rose-400/25', note: "Won't be posted. Fix it and add it again." },
  duplicate: { label: 'Already done', icon: Copy, hex: '#a78bfa', text: 'text-violet-300', tile: 'bg-violet-500/10 ring-violet-400/25', note: 'Already posted, so it will be skipped.' }
};
const ORDER: CandidateState[] = ['ready', 'waiting', 'invalid', 'duplicate'];

const stateOf = (c: Candidate): CandidateState => (!c.stable ? 'waiting' : !c.valid ? 'invalid' : c.duplicate_of ? 'duplicate' : 'ready');

// A ring split into one coloured arc per state, drawn in when it scrolls into view.
const QueueRing: React.FC<{ counts: Record<CandidateState, number>; total: number }> = ({ counts, total }) => {
  const r = 54;
  const c = 2 * Math.PI * r;
  const gap = total > 1 ? 4 : 0;
  let offset = 0;
  return (
    <div className="relative mx-auto grid h-32 w-32 shrink-0 place-items-center">
      <svg viewBox="0 0 140 140" className="absolute inset-0 -rotate-90" aria-hidden="true">
        <circle cx="70" cy="70" r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="12" />
        {total > 0 &&
          ORDER.map((key, i) => {
            const len = (counts[key] / total) * c;
            if (len === 0) return null;
            const start = offset;
            offset += len;
            const visible = Math.max(len - gap, 1);
            return (
              <motion.circle
                key={key}
                cx="70"
                cy="70"
                r={r}
                fill="none"
                stroke={STATE[key].hex}
                strokeWidth="12"
                strokeLinecap={total > 1 ? 'round' : 'butt'}
                strokeDashoffset={-start}
                initial={{ strokeDasharray: `0 ${c}` }}
                whileInView={{ strokeDasharray: `${visible} ${c}` }}
                viewport={{ once: true }}
                transition={{ duration: 0.9, ease: EASE_OUT, delay: 0.15 + i * 0.12 }}
                style={{ filter: `drop-shadow(0 0 6px ${STATE[key].hex}88)` }}
              />
            );
          })}
      </svg>
      <div className="text-center">
        <AnimatedNumber value={counts.ready} className={cn('block text-4xl font-semibold leading-none', counts.ready ? 'text-slate-50' : 'text-slate-500')} />
        <span className="mt-1 block text-[10px] uppercase tracking-[0.14em] text-slate-500">ready</span>
      </div>
    </div>
  );
};

const PostRow: React.FC<{ candidate: Candidate; index: number }> = ({ candidate: c, index }) => {
  const key = stateOf(c);
  const s = STATE[key];
  const Icon = s.icon;
  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0, transition: { duration: 0.4, ease: EASE_OUT, delay: Math.min(index, 8) * 0.04 } }}
      exit={{ opacity: 0, x: -12, transition: { duration: 0.18 } }}
      className="group relative overflow-hidden rounded-2xl border border-white/[0.07] bg-gradient-to-r from-white/[0.035] to-white/[0.01] transition-colors hover:border-white/[0.14]"
    >
      <span className="absolute inset-y-0 left-0 w-[3px]" style={{ backgroundColor: s.hex, boxShadow: `0 0 12px ${s.hex}` }} aria-hidden="true" />
      <div className="flex flex-col gap-4 p-4 pl-5 sm:flex-row sm:items-center">
        <span className={cn('grid h-11 w-11 shrink-0 place-items-center rounded-xl ring-1 ring-inset transition-transform duration-300 group-hover:scale-105', s.tile, s.text)}>
          <Icon className={cn('h-5 w-5', key === 'waiting' && 'animate-pulse')} aria-hidden="true" />
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="truncate text-[15px] font-semibold text-slate-100" title={c.name}>
              {c.name}
            </h4>
            {c.only_simulated && (
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-300 ring-1 ring-inset ring-amber-400/25">
                <FlaskConical className="h-3 w-3" aria-hidden="true" /> Only tested
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-slate-400">{s.note}</p>
          {(c.image_filename || c.caption_filename) && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {c.image_filename && (
                <span className="inline-flex items-center gap-1 rounded-md bg-white/[0.04] px-2 py-0.5 text-[11px] text-slate-400 ring-1 ring-inset ring-white/[0.06]">
                  <FileImage className="h-3 w-3" aria-hidden="true" />
                  {c.image_filename}
                  {c.file_size_bytes ? <span className="text-slate-600">· {(c.file_size_bytes / 1024).toFixed(0)} KB</span> : null}
                </span>
              )}
              {c.caption_filename && (
                <span className="inline-flex items-center gap-1 rounded-md bg-white/[0.04] px-2 py-0.5 text-[11px] text-slate-400 ring-1 ring-inset ring-white/[0.06]">
                  <FileText className="h-3 w-3" aria-hidden="true" />
                  {c.caption_filename}
                </span>
              )}
            </div>
          )}
          {c.errors.length > 0 && (
            <ul className="mt-2.5 space-y-1 rounded-lg border border-rose-500/20 bg-rose-500/[0.06] px-3 py-2 text-xs text-rose-200">
              {c.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2 sm:flex-col sm:items-end">
          <span className={cn('inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium ring-1 ring-inset', s.tile, s.text)}>
            <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: s.hex }} />
            {s.label}
          </span>
          {key === 'ready' && (
            <button
              type="button"
              onClick={() => scrollToSection('runner')}
              className="inline-flex min-h-8 items-center gap-1 rounded-lg px-2 text-xs font-medium text-emerald-300 transition-colors hover:bg-emerald-500/10 hover:text-emerald-200"
            >
              Publish <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
            </button>
          )}
        </div>
      </div>
    </motion.li>
  );
};

export const ScanQueueStation: React.FC = () => {
  const { scanData, isScanning, refreshScan, connection } = useApi();
  const [filter, setFilter] = useState<Filter>('all');
  const offline = connection !== 'online';

  const candidates = scanData?.candidates ?? [];
  const counts = candidates.reduce<Record<CandidateState, number>>((acc, c) => ((acc[stateOf(c)] += 1), acc), {
    ready: 0,
    waiting: 0,
    invalid: 0,
    duplicate: 0
  });
  const visible = filter === 'all' ? candidates : candidates.filter((c) => stateOf(c) === filter);
  const pending = Array.isArray(scanData?.pending) ? (scanData.pending as unknown[]).map(String) : [];
  const ambiguous = countOf(scanData?.ambiguous);
  const onlySimulated = candidates.filter((c) => c.only_simulated);

  return (
    <section id="scan" className="relative py-16 lg:py-20">
      <div className="hr-fade absolute inset-x-0 top-0" />
      <div className="page-container">
        <SectionHeading index="02" icon={ScanLine} eyebrow="Step 2" title="Your queue">
          Everything in your posts folder, and what will happen to it. Looking here never changes anything.
        </SectionHeading>

        {/* Overview bar: ring, counts (which also filter the list), actions */}
        <Reveal>
          <div className="relative isolate overflow-hidden rounded-3xl border border-white/[0.07] bg-gradient-to-br from-white/[0.045] to-white/[0.01] p-5 sm:p-6">
            <span className="pointer-events-none absolute -left-16 -top-16 -z-10 h-48 w-48 rounded-full bg-emerald-500/15 blur-3xl" aria-hidden="true" />
            <div className="flex flex-col items-center gap-6 lg:flex-row">
              <QueueRing counts={counts} total={candidates.length} />

              <div className="w-full min-w-0 flex-1">
                <p className="text-center text-sm text-slate-400 lg:text-left">
                  {offline ? 'Not connected. Start your poster to see your queue.' : `${candidates.length} post${candidates.length === 1 ? '' : 's'} in your folder`}
                </p>
                <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {ORDER.map((key) => {
                    const s = STATE[key];
                    const active = filter === key;
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => setFilter(active ? 'all' : key)}
                        aria-pressed={active}
                        className={cn(
                          'relative rounded-2xl border px-3.5 py-3 text-left transition-colors',
                          active ? 'border-white/20 bg-white/[0.07]' : 'border-white/[0.06] bg-black/20 hover:border-white/15'
                        )}
                      >
                        <span className="flex items-center gap-2 text-xs text-slate-400">
                          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: s.hex, boxShadow: `0 0 8px ${s.hex}` }} />
                          {s.label}
                        </span>
                        <AnimatedNumber value={counts[key]} className={cn('mt-1.5 block text-2xl font-semibold leading-none', counts[key] ? 'text-slate-50' : 'text-slate-600')} />
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="flex w-full flex-col gap-2 sm:flex-row lg:w-52 lg:flex-col">
                <button type="button" onClick={() => scrollToSection('runner')} disabled={counts.ready === 0} className="btn btn-primary w-full">
                  <Play className="h-4 w-4" aria-hidden="true" />
                  {counts.ready ? `Publish ${counts.ready} ready` : 'Nothing ready'}
                </button>
                <button type="button" onClick={() => void refreshScan()} disabled={isScanning || offline} className="btn btn-ghost w-full text-sm">
                  <RefreshCw className={cn('h-4 w-4', isScanning && 'animate-spin text-emerald-400')} aria-hidden="true" />
                  {isScanning ? 'Refreshing…' : 'Refresh'}
                </button>
              </div>
            </div>
          </div>
        </Reveal>

        {/* Posts */}
        <Reveal delay={0.08} className="mt-5">
          {filter !== 'all' && (
            <div className="mb-3 flex items-center gap-2 text-sm text-slate-400">
              Showing only <span className="font-medium text-slate-200">{STATE[filter].label.toLowerCase()}</span>
              <button type="button" onClick={() => setFilter('all')} className="rounded-lg px-2 py-1 text-emerald-300 hover:bg-emerald-500/10">
                Show all
              </button>
            </div>
          )}
            {/* Notices */}
            <div className="space-y-2.5">
              {onlySimulated.length > 0 && (
                <div className="flex items-start gap-3 rounded-2xl border border-amber-500/25 bg-amber-500/[0.06] p-4 text-sm text-amber-100">
                  <FlaskConical className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden="true" />
                  <p>
                    <strong className="font-semibold">Only tested so far:</strong> {onlySimulated.map((c) => c.name).join(', ')}. Tick “Also post items I only
                    tested” when you publish to include them.
                  </p>
                </div>
              )}
              {(scanData?.warnings.length ?? 0) > 0 && (
                <div className="flex items-start gap-3 rounded-2xl border border-amber-500/25 bg-amber-500/[0.06] p-4 text-sm text-amber-100">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden="true" />
                  <ul className="space-y-1">
                    {scanData!.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                </div>
              )}
              {(pending.length > 0 || ambiguous > 0) && (
                <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-2xl border border-white/[0.06] bg-white/[0.02] px-4 py-3 text-xs text-slate-400">
                  {pending.length > 0 && (
                    <span>
                      <span className="text-slate-300">Missing a picture or caption:</span> {pending.join(', ')}
                    </span>
                  )}
                  {ambiguous > 0 && (
                    <span>
                      <span className="text-slate-300">Same name used twice:</span> {ambiguous}. Keep one picture and one caption per name.
                    </span>
                  )}
                </div>
              )}
            </div>

            {visible.length === 0 ? (
              <div className="mt-2.5 flex flex-col items-center rounded-2xl border border-dashed border-white/10 bg-white/[0.015] px-8 py-16 text-center">
                <motion.span
                  animate={{ y: [0, -6, 0] }}
                  transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
                  className="grid h-14 w-14 place-items-center rounded-2xl bg-white/[0.04] text-slate-400 ring-1 ring-inset ring-white/10"
                >
                  <FolderInput className="h-6 w-6" aria-hidden="true" />
                </motion.span>
                <h3 className="mt-4 font-medium text-slate-200">
                  {offline ? 'Not connected' : filter === 'all' ? 'Your queue is empty' : `Nothing marked “${STATE[filter].label.toLowerCase()}”`}
                </h3>
                <p className="mt-1 max-w-sm text-sm text-slate-500">
                  {offline
                    ? 'Start your poster and your queue will show up here.'
                    : filter === 'all'
                      ? 'Drop a picture and caption into your posts folder, or create one above.'
                      : 'Pick another tab to see the rest of your queue.'}
                </p>
                {filter === 'all' && !offline && (
                  <button type="button" onClick={() => scrollToSection('intake')} className="btn btn-ghost mt-5 text-xs">
                    Create a post <ArrowRight className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            ) : (
              <motion.ul layout className="mt-2.5 space-y-2.5">
                <AnimatePresence mode="popLayout" initial={false}>
                  {visible.map((c, i) => (
                    <PostRow key={c.name} candidate={c} index={i} />
                  ))}
                </AnimatePresence>
              </motion.ul>
            )}
        </Reveal>
      </div>
    </section>
  );
};
