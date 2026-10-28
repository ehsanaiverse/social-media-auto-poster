import React from 'react';
import { ExternalLink, Wrench } from 'lucide-react';
import { useApi } from '../context/ApiContext';
import { Modal, useLatest } from './ui/Modal';
import { ModeTag, StatusBadge } from './ui/StatusBadge';

const NEXT_STEP: Record<string, string> = {
  PUBLISHED: 'This post is live on LinkedIn.',
  SIMULATED: 'This was only a test. Nothing was posted.',
  FAILED: 'This post was not published. Fix the problem below and add it again.',
  NEEDS_REVIEW: "We couldn't tell whether this went live. Please look on LinkedIn, then tell us what you found.",
  PUBLISHING: 'This post is being published right now.'
};

const Row: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="flex items-center justify-between gap-4 border-b border-white/[0.05] py-3 last:border-0">
    <span className="text-sm text-slate-500">{label}</span>
    <span className="min-w-0 truncate text-right text-sm text-slate-200">{children}</span>
  </div>
);

export const JobDetailsModal: React.FC = () => {
  const { selectedJob, closeJob, setResolvingJob } = useApi();
  const job = useLatest(selectedJob);
  const link =
    job?.post_id && job.post_id.startsWith('urn:li:')
      ? `https://www.linkedin.com/feed/update/${job.post_id}/`
      : null;

  return (
    <Modal
      open={Boolean(selectedJob)}
      onClose={closeJob}
      className="sm:max-w-lg"
      title={job?.stem}
      footer={
        <>
          {job?.status === 'NEEDS_REVIEW' && (
            <button
              type="button"
              onClick={() => {
                setResolvingJob(job);
                closeJob();
              }}
              className="btn btn-danger min-h-10 text-xs"
            >
              <Wrench className="h-3.5 w-3.5" /> Check now
            </button>
          )}
          <button type="button" onClick={closeJob} className="btn btn-ghost min-h-10 text-xs">
            Close
          </button>
        </>
      }
    >
      {job && (
        <div className="space-y-5 p-6">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={job.status} />
            <ModeTag mode={job.mode} />
          </div>
          <p className="text-sm leading-relaxed text-slate-300">{NEXT_STEP[job.status]}</p>

          <div className="surface-inset px-4">
            <Row label="Date">{new Date(job.created_at).toLocaleString()}</Row>
            {link && (
              <Row label="On LinkedIn">
                <a href={link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sky-300 hover:text-sky-200">
                  Open post <ExternalLink className="h-3.5 w-3.5" />
                </a>
              </Row>
            )}
            <Row label="Picture">{job.media_filename ?? '—'}</Row>
            <Row label="Files are in">{job.destination ? `${job.destination.split('/')[0]} folder` : 'queue folder'}</Row>
          </div>

          {job.errors && job.errors.length > 0 && (
            <div className="rounded-xl border border-rose-500/20 bg-rose-500/[0.05] p-4">
              <p className="mb-2 text-xs font-medium text-rose-200">Notes</p>
              <ul className="space-y-1.5 text-sm text-rose-100/90">
                {job.errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
};
