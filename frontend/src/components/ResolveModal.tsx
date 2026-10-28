import React, { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { CheckCircle2, Loader2, Wrench, XCircle } from 'lucide-react';
import { useApi } from '../context/ApiContext';
import { cn } from '../utils/cn';
import { Modal, useLatest } from './ui/Modal';
import { ModeTag } from './ui/StatusBadge';

type Outcome = 'published' | 'failed';

export const ResolveModal: React.FC = () => {
  const { resolvingJob, setResolvingJob, resolveJob } = useApi();
  const job = useLatest(resolvingJob);
  const [outcome, setOutcome] = useState<Outcome>('published');
  const [postId, setPostId] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const isSimulated = job?.mode === 'simulated';

  // Start fresh for every job.
  useEffect(() => {
    setOutcome(job?.mode === 'simulated' ? 'failed' : 'published');
    setPostId('');
  }, [job?.job_id, job?.mode]);

  const blocked = outcome === 'published' && (isSimulated || !postId.trim());

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!job || !resolvingJob || blocked) return;
    setIsSubmitting(true);
    await resolveJob(job.job_id, outcome === 'published' ? { published_post_id: postId.trim() } : { failed: true });
    setIsSubmitting(false);
  };

  const close = () => setResolvingJob(null);

  const options: { id: Outcome; icon: typeof CheckCircle2; title: string; text: string; tone: string }[] = [
    { id: 'published', icon: CheckCircle2, title: 'Yes, I see it', text: 'We will mark it as posted and never post it again.', tone: 'emerald' },
    { id: 'failed', icon: XCircle, title: 'No, it is not there', text: 'We will mark it as not posted, so you can post it again.', tone: 'rose' }
  ];

  return (
    <Modal
      open={Boolean(resolvingJob)}
      onClose={close}
      className="sm:max-w-lg"
      icon={
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/30">
          <Wrench className="h-4 w-4" />
        </span>
      }
      title="Did this post go live?"
      subtitle={job?.stem}
    >
      {job && (
        <form onSubmit={submit} className="space-y-5 p-6">
          <div className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
            <span className="truncate font-mono text-sm text-slate-200">{job.stem}</span>
            <ModeTag mode={job.mode} />
          </div>

          <fieldset>
            <legend className="mb-2 text-sm text-slate-300">Look for it on your LinkedIn profile, then choose:</legend>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {options.map((o) => {
                const Icon = o.icon;
                const sel = outcome === o.id;
                const disabled = o.id === 'published' && isSimulated;
                return (
                  <label
                    key={o.id}
                    className={cn(
                      'relative flex cursor-pointer flex-col gap-1.5 rounded-xl border p-4 transition-colors',
                      disabled && 'cursor-not-allowed opacity-40',
                      sel
                        ? o.tone === 'emerald'
                          ? 'border-emerald-500/50 bg-emerald-500/[0.07]'
                          : 'border-rose-500/50 bg-rose-500/[0.07]'
                        : 'border-white/[0.08] bg-black/20 hover:border-white/15'
                    )}
                  >
                    <input type="radio" name="outcome" value={o.id} checked={sel} disabled={disabled} onChange={() => setOutcome(o.id)} className="sr-only" />
                    <span className="flex items-center gap-2 text-sm font-medium text-slate-100">
                      <Icon className={cn('h-4 w-4', o.tone === 'emerald' ? 'text-emerald-400' : 'text-rose-400')} aria-hidden="true" />
                      {o.title}
                    </span>
                    <span className="text-xs leading-relaxed text-slate-400">{o.text}</span>
                    {sel && <motion.span layoutId="resolve-sel" className="pointer-events-none absolute inset-0 rounded-xl ring-1 ring-inset ring-white/10" />}
                  </label>
                );
              })}
            </div>
            {isSimulated && <p className="mt-2 text-xs text-slate-500">This was a test, so it was never posted.</p>}
          </fieldset>

          {outcome === 'published' && !isSimulated && (
            <div>
              <label htmlFor="resolve-post-id" className="mb-1.5 block text-sm text-slate-300">
                LinkedIn post ID
                <span className="ml-1 text-xs text-slate-500">(from the post’s link)</span>
              </label>
              <input
                id="resolve-post-id"
                type="text"
                value={postId}
                onChange={(e) => setPostId(e.target.value)}
                placeholder="urn:li:share:…"
                autoComplete="off"
                spellCheck={false}
                className="field text-sm"
              />
            </div>
          )}

          <p className="text-xs leading-relaxed text-slate-500">
            Your answer is saved in the history.
          </p>


          <div className="flex justify-end gap-3 pt-1">
            <button type="button" onClick={close} className="btn btn-ghost min-h-10 text-xs">
              Cancel
            </button>
            <button type="submit" disabled={blocked || isSubmitting} className={cn('btn min-h-10 text-xs', outcome === 'failed' ? 'btn-ghost border-rose-500/40 text-rose-200' : 'btn-primary')}>
              {isSubmitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {outcome === 'published' ? 'Mark as posted' : 'Mark as not posted'}
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
};
