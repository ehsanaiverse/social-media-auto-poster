import React, { useEffect, useState } from 'react';
import confetti from 'canvas-confetti';
import { AnimatePresence, motion } from 'framer-motion';
import { CheckCircle2, ExternalLink, Loader2, Lock, Play, Rocket, ShieldAlert, Sparkles } from 'lucide-react';
import { useApi } from '../context/ApiContext';
import { CONFIRM_PHRASE } from '../lib/postRules';
import { isReady, type RunJobResult } from '../types/api';
import { cn } from '../utils/cn';
import { SectionHeading } from './ui/SectionHeading';
import { STATUS_LABEL, StatusBadge } from './ui/StatusBadge';
import { EASE_OUT, Reveal } from './ui/motion';

const WORKING_STEPS = ['Checking your files…', 'Making sure nothing is posted twice…', 'Publishing…', 'Saving the results…'];

const TOKEN_TEXT: Record<string, string> = {
  valid: 'LinkedIn is connected.',
  expiring: 'LinkedIn is connected, but the connection expires soon.',
  expired: 'LinkedIn connection has expired. Reconnect it first.',
  missing: 'LinkedIn is not connected yet.',
  unknown: 'LinkedIn connection status is unknown.'
};

// A link to the post on LinkedIn, when the ID is a LinkedIn URN.
const postUrl = (id?: string | null) =>
  id && id.startsWith('urn:li:') ? `https://www.linkedin.com/feed/update/${id}/` : null;

const JobLine: React.FC<{ job: RunJobResult; index: number }> = ({ job, index }) => {
  const url = postUrl(job.post_id);
  return (
    <motion.li
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.35, ease: EASE_OUT, delay: index * 0.08 }}
      className="surface-inset flex flex-wrap items-center justify-between gap-2 px-4 py-3"
    >
      <span className="min-w-0 truncate text-sm font-medium text-slate-200">{job.stem}</span>
      <span className="flex items-center gap-2">
        {url && (
          <a href={url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-sky-300 hover:text-sky-200">
            View <ExternalLink className="h-3 w-3" />
          </a>
        )}
        <StatusBadge status={job.status} />
      </span>
    </motion.li>
  );
};

export const RunnerConsole: React.FC = () => {
  const { executeRun, isRunning, lastRunReport, linkedInStatus, scanData, connection } = useApi();
  const [publishSimulated, setPublishSimulated] = useState(false);
  const [phrase, setPhrase] = useState('');
  const [step, setStep] = useState(0);

  const ready = scanData?.candidates.filter(isReady).length ?? 0;
  const tokenState = linkedInStatus?.state ?? 'unknown';
  const tokenUsable = tokenState === 'valid' || tokenState === 'expiring';
  const phraseOk = phrase.trim() === CONFIRM_PHRASE;
  const canRun = connection === 'online' && !isRunning && phraseOk;

  useEffect(() => {
    if (!isRunning) return;
    setStep(0);
    const id = setInterval(() => setStep((s) => Math.min(s + 1, WORKING_STEPS.length - 1)), 700);
    return () => clearInterval(id);
  }, [isRunning]);

  const handleRun = async () => {
    const report = await executeRun({ real: true, publish_simulated: publishSimulated, confirm: phrase.trim() });
    if (!report) return;
    setPhrase('');
    if (report.jobs.some((j) => j.status === 'PUBLISHED')) {
      confetti({ particleCount: 110, spread: 80, origin: { y: 0.65 }, colors: ['#34d399', '#5eead4', '#a7f3d0', '#fbbf24'] });
    }
  };

  const summary = lastRunReport
    ? (Object.entries(
        lastRunReport.jobs.reduce<Record<string, number>>((acc, j) => ((acc[j.status] = (acc[j.status] ?? 0) + 1), acc), {})
      ) as [keyof typeof STATUS_LABEL, number][])
    : [];

  return (
    <section id="runner" className="relative py-16 lg:py-20">
      <div className="hr-fade absolute inset-x-0 top-0" />
      <div className="page-container">
        <SectionHeading index="03" icon={Play} eyebrow="Step 3" title="Publish">
          Post everything in your queue to LinkedIn. Every post goes out only once.
        </SectionHeading>

        <div className="grid grid-cols-1 items-start gap-8 lg:grid-cols-12">
          <Reveal className="lg:col-span-5">
            <div className="surface space-y-5 p-6">
              <div className="space-y-4">
                <div className="rounded-xl border border-emerald-500/25 bg-emerald-500/[0.06] p-4 text-sm leading-relaxed text-slate-200">
                  <p className="mb-1 flex items-center gap-2 font-semibold text-emerald-200">
                    <ShieldAlert className="h-4 w-4" aria-hidden="true" />
                    This posts to your LinkedIn account
                  </p>
                  If something looks wrong, we stop before posting. If we can't tell whether a post went through, we ask you.
                </div>

                <p className={cn('flex items-center gap-2 text-sm', tokenUsable ? 'text-emerald-300' : 'text-rose-300')}>
                  <span className={cn('h-2 w-2 rounded-full', tokenUsable ? 'bg-emerald-400' : 'bg-rose-400')} />
                  {TOKEN_TEXT[tokenState]}
                </p>

                <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-white/[0.06] bg-black/20 p-3 text-sm text-slate-300">
                  <input type="checkbox" checked={publishSimulated} onChange={(e) => setPublishSimulated(e.target.checked)} className="mt-0.5 h-4 w-4 accent-emerald-500" />
                  <span>
                    Also post items I only tested
                    <span className="block text-xs text-slate-500">Without this, tested items are not posted.</span>
                  </span>
                </label>

                <div>
                  <label htmlFor="confirm-phrase" className="mb-1.5 block text-sm text-slate-300">
                    To confirm, type <span className="font-medium text-emerald-300">{CONFIRM_PHRASE}</span>
                  </label>
                  <input
                    id="confirm-phrase"
                    type="text"
                    value={phrase}
                    onChange={(e) => setPhrase(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    className={cn('field text-sm', phraseOk && 'border-emerald-400/70')}
                  />
                </div>
              </div>

              <motion.button type="button" onClick={handleRun} disabled={!canRun} whileTap={canRun ? { scale: 0.98 } : undefined} className="btn btn-primary w-full">
                {isRunning ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Play className="h-4 w-4" aria-hidden="true" />}
                {isRunning ? 'Working…' : `Post ${ready} to LinkedIn`}
              </motion.button>

              {connection !== 'online' && (
                <p className="flex items-center justify-center gap-2 text-xs text-slate-400">
                  <Lock className="h-3.5 w-3.5" aria-hidden="true" /> Connect to your poster to publish.
                </p>
              )}
            </div>
          </Reveal>

          <Reveal delay={0.1} className="lg:col-span-7">
            <div className="surface min-h-[340px] overflow-hidden p-6" aria-live="polite">
              <AnimatePresence mode="wait">
                {isRunning ? (
                  <motion.div key="working" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="flex h-full min-h-[290px] flex-col items-center justify-center gap-6">
                    <div className="relative grid h-20 w-20 place-items-center">
                      <motion.span className="absolute inset-0 rounded-full border-2 border-emerald-400/20 border-t-emerald-400" animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: 'linear' }} />
                      <Sparkles className="h-7 w-7 text-emerald-300" />
                    </div>
                    <ol className="space-y-2">
                      {WORKING_STEPS.map((s, i) => (
                        <motion.li
                          key={s}
                          animate={{ opacity: i <= step ? 1 : 0.3 }}
                          className="flex items-center gap-2.5 text-sm text-slate-300"
                        >
                          {i < step ? (
                            <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                          ) : i === step ? (
                            <Loader2 className="h-4 w-4 animate-spin text-emerald-300" />
                          ) : (
                            <span className="h-4 w-4 rounded-full border border-white/15" />
                          )}
                          {s}
                        </motion.li>
                      ))}
                    </ol>
                  </motion.div>
                ) : lastRunReport ? (
                  <motion.div key="result" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.4, ease: EASE_OUT }} className="space-y-5">
                    <div>
                      <h3 className="text-lg font-semibold text-slate-50">
                        {lastRunReport.jobs.length === 0
                          ? 'Nothing new to publish'
                          : lastRunReport.mode === 'real'
                            ? 'Publishing finished'
                            : 'Test run finished'}
                      </h3>
                      <p className="mt-1 text-sm text-slate-400">
                        {lastRunReport.mode === 'simulated'
                          ? 'This was a test. Nothing was posted.'
                          : 'Your posts are live.'}
                      </p>
                    </div>
                    {summary.length > 0 && (
                      <div className="flex flex-wrap gap-2">
                        {summary.map(([status, n]) => (
                          <span key={status} className="rounded-full bg-white/[0.04] px-3 py-1 text-xs text-slate-300 ring-1 ring-inset ring-white/10">
                            <span className="font-semibold text-slate-50">{n}</span> {STATUS_LABEL[status].toLowerCase()}
                          </span>
                        ))}
                      </div>
                    )}
                    <ul className="space-y-2">
                      {lastRunReport.jobs.map((j, i) => (
                        <JobLine key={j.job_id} job={j} index={i} />
                      ))}
                    </ul>
                    {lastRunReport.jobs.some((j) => j.status === 'FAILED' || j.status === 'NEEDS_REVIEW') && (
                      <p className="text-xs text-slate-500">See History for what went wrong and what to do next.</p>
                    )}
                  </motion.div>
                ) : (
                  <motion.div key="idle" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="flex min-h-[290px] flex-col items-center justify-center text-center">
                    <motion.span
                      animate={{ y: [0, -8, 0] }}
                      transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
                      className="grid h-16 w-16 place-items-center rounded-2xl bg-emerald-500/10 text-emerald-300 ring-1 ring-inset ring-emerald-500/25"
                    >
                      <Rocket className="h-7 w-7" />
                    </motion.span>
                    <p className="mt-5 font-medium text-slate-200">Ready when you are</p>
                    <p className="mt-1 max-w-xs text-sm text-slate-500">Results will show up here after you publish.</p>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
};
