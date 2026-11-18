import React from 'react';
import { motion } from 'framer-motion';
import { Clock, RefreshCw, ShieldCheck, UserRound } from 'lucide-react';
import { useApi } from '../context/ApiContext';
import type { LinkedInTokenState } from '../types/api';
import { PLATFORMS, type Platform } from '../lib/platforms';
import { cn } from '../utils/cn';
import { SectionHeading } from './ui/SectionHeading';
import { AnimatedNumber, EASE_OUT } from './ui/motion';

const CONNECTION_DAYS = 60;

const STATE_COPY: Record<LinkedInTokenState, { label: string; text: string; hex: string }> = {
  valid: { label: 'Connected', text: 'Ready to post.', hex: '#34d399' },
  expiring: { label: 'Expires soon', text: 'Reconnect in the next few days.', hex: '#fbbf24' },
  expired: { label: 'Expired', text: 'Reconnect to post again.', hex: '#fb7185' },
  missing: { label: 'Not connected', text: 'Connect your account to post.', hex: '#fb7185' },
  unknown: { label: 'Connected', text: "Expiry date isn't known.", hex: '#94a3b8' }
};

const OFFLINE = { label: 'Not connected', text: 'Start your poster to see this account.', hex: '#64748b' };

// The shared frame of every account card: brand tile, status pill, glow.
const CardShell: React.FC<{ platform: Platform; status: string; hex: string; index: number; dim?: boolean; children: React.ReactNode }> = ({
  platform,
  status,
  hex,
  index,
  dim,
  children
}) => (
  <motion.article
    initial={{ opacity: 0, y: 20 }}
    whileInView={{ opacity: 1, y: 0 }}
    viewport={{ once: true, margin: '-40px' }}
    transition={{ duration: 0.55, ease: EASE_OUT, delay: index * 0.08 }}
    whileHover={{ y: -4 }}
    style={{ '--tone': hex } as React.CSSProperties}
    className="group relative isolate flex h-full flex-col overflow-hidden rounded-2xl border border-white/[0.07] bg-gradient-to-b from-white/[0.04] to-white/[0.01] p-5 transition-[border-color,box-shadow] duration-300 hover:border-[color-mix(in_srgb,var(--tone)_40%,transparent)] hover:shadow-[0_20px_50px_-25px_var(--tone)]"
  >
    <span
      className="pointer-events-none absolute -right-12 -top-12 -z-10 h-36 w-36 rounded-full opacity-[0.14] blur-2xl transition-opacity duration-500 group-hover:opacity-30"
      style={{ backgroundColor: dim ? '#475569' : platform.color }}
      aria-hidden="true"
    />
    <span className="pointer-events-none absolute inset-x-6 top-0 h-px opacity-60" style={{ background: `linear-gradient(90deg, transparent, ${hex}, transparent)` }} aria-hidden="true" />

    <div className="flex items-start justify-between gap-3">
      <span
        className={cn('grid h-11 w-11 place-items-center rounded-xl text-white ring-1 ring-inset ring-white/15 transition-transform duration-300 group-hover:scale-105', dim && 'text-slate-300')}
        style={{ backgroundColor: dim ? 'rgba(255,255,255,0.05)' : platform.color }}
      >
        <platform.Icon className="h-5 w-5" />
      </span>
      <span className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-black/30 px-2.5 py-1 text-[11px] font-medium text-slate-300">
        <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: hex }} />
        {status}
      </span>
    </div>
    <h3 className="mt-4 text-lg font-semibold text-slate-50">{platform.name}</h3>
    {children}
  </motion.article>
);

const LinkedInCard: React.FC<{ platform: Platform }> = ({ platform }) => {
  const { linkedInStatus, isCheckingLinkedIn, refreshLinkedInStatus, connection } = useApi();
  const offline = connection !== 'online';
  const copy = offline ? OFFLINE : STATE_COPY[linkedInStatus?.state ?? 'unknown'];
  const days = offline ? null : linkedInStatus?.days_left ?? null;
  const fraction = days === null ? 0 : Math.max(0, Math.min(1, days / CONNECTION_DAYS));

  return (
    <CardShell platform={platform} status={copy.label} hex={copy.hex} index={0}>
      <p className="mt-1 flex items-center gap-1.5 text-sm text-slate-400">
        {linkedInStatus?.member && !offline ? (
          <>
            <UserRound className="h-3.5 w-3.5" aria-hidden="true" /> Posting as <span className="truncate text-slate-200">{linkedInStatus.member.name}</span>
          </>
        ) : (
          copy.text
        )}
      </p>

      <div className="mt-5">
        <div className="flex items-baseline gap-2">
          {days === null ? (
            <span className="text-4xl font-semibold leading-none text-slate-500">—</span>
          ) : (
            <AnimatedNumber value={days} className="text-4xl font-semibold leading-none tracking-tight text-slate-50" />
          )}
          <span className="text-xs text-slate-500">days left</span>
        </div>
        <div className="mt-3 h-1 overflow-hidden rounded-full bg-white/[0.06]">
          <motion.div
            className="h-full rounded-full"
            style={{ backgroundColor: copy.hex, boxShadow: `0 0 10px ${copy.hex}` }}
            initial={{ width: 0 }}
            whileInView={{ width: `${fraction * 100}%` }}
            viewport={{ once: true }}
            transition={{ duration: 1.1, ease: EASE_OUT, delay: 0.2 }}
          />
        </div>
        <p className="mt-2 flex items-center gap-1.5 text-xs text-slate-500">
          <Clock className="h-3 w-3" aria-hidden="true" />
          {linkedInStatus?.expires_on && !offline ? `Expires ${new Date(linkedInStatus.expires_on).toLocaleDateString()}` : 'Lasts 60 days'}
        </p>
      </div>

      <button
        type="button"
        onClick={() => void refreshLinkedInStatus(true)}
        disabled={isCheckingLinkedIn || offline}
        className="btn btn-primary mt-auto w-full text-sm"
        style={{ marginTop: '1.25rem' }}
      >
        <RefreshCw className={cn('h-4 w-4', isCheckingLinkedIn && 'animate-spin')} aria-hidden="true" />
        {isCheckingLinkedIn ? 'Checking…' : 'Check connection'}
      </button>
    </CardShell>
  );
};

const ComingSoonCard: React.FC<{ platform: Platform; index: number }> = ({ platform, index }) => (
  <CardShell platform={platform} status="Coming soon" hex="#64748b" index={index} dim>
    <p className="mt-1 text-sm text-slate-400">Not available yet.</p>
    <div className="mt-5 rounded-xl border border-dashed border-white/10 bg-white/[0.02] p-4 text-xs leading-relaxed text-slate-500">
      We're adding {platform.name}. Your posts will go there too once it's ready.
    </div>
    <button type="button" disabled className="btn btn-ghost mt-auto w-full text-sm" style={{ marginTop: '1.25rem' }}>
      Coming soon
    </button>
  </CardShell>
);

export const LinkedInSentinel: React.FC = () => (
  <section id="linkedin" className="relative py-16 lg:py-20">
    <div className="hr-fade absolute inset-x-0 top-0" />
    <div className="page-container">
      <SectionHeading index="06" icon={ShieldCheck} eyebrow="Accounts" title="Your connected accounts">
        Connect each platform once. LinkedIn is ready today, and more are on the way.
      </SectionHeading>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {PLATFORMS.map((p, i) => (p.live ? <LinkedInCard key={p.id} platform={p} /> : <ComingSoonCard key={p.id} platform={p} index={i} />))}
      </div>
    </div>
  </section>
);
