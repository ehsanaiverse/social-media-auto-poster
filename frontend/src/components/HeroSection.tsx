import React, { Suspense, lazy } from 'react';
import { motion } from 'framer-motion';
import { AlertTriangle, ArrowRight, CheckCircle2, Clock, Inbox, Play, type LucideIcon } from 'lucide-react';
import { useApi } from '../context/ApiContext';
import { PLATFORMS } from '../lib/platforms';
import { scrollToSection } from '../lib/sections';
import { countOf, isReady } from '../types/api';
import { cn } from '../utils/cn';
import { StatCard, type Tone } from './ui/StatCard';
import { EASE_OUT, fadeUp, stagger } from './ui/motion';

const NetworkGlobe3D = lazy(() => import('./3d/NetworkGlobe3D'));

interface Metric {
  target: string;
  icon: LucideIcon;
  label: string;
  value: number;
  tone: Tone;
  hint: string;
}

// The platform logos circle the globe; each badge counter-rotates so it stays upright.
const PlatformOrbit: React.FC = () => (
  <div className="orbit-spin pointer-events-none absolute inset-[6%]" aria-hidden="true">
    <div className="absolute inset-0 rounded-full border border-dashed border-white/[0.07]" />
    {PLATFORMS.map((p, i) => {
      const angle = (i / PLATFORMS.length) * Math.PI * 2 - Math.PI / 2;
      return (
        <div
          key={p.id}
          className="absolute"
          style={{ left: `${50 + Math.cos(angle) * 50}%`, top: `${50 + Math.sin(angle) * 50}%`, transform: 'translate(-50%, -50%)' }}
        >
          <div className="orbit-counter">
            <div
              className={cn(
                'relative grid h-14 w-14 place-items-center rounded-2xl border backdrop-blur-md sm:h-16 sm:w-16',
                p.live ? 'border-white/20 bg-white/[0.08]' : 'border-white/10 bg-black/40'
              )}
              style={p.live ? { boxShadow: `0 0 40px -6px ${p.color}` } : undefined}
            >
              <p.Icon className={cn('h-7 w-7', p.live ? 'text-white' : 'text-slate-400')} />
              <span
                className={cn(
                  'absolute -bottom-2.5 whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-semibold',
                  p.live ? 'bg-emerald-400 text-[#04110B]' : 'bg-white/10 text-slate-300'
                )}
              >
                {p.live ? 'Live' : 'Soon'}
              </span>
            </div>
          </div>
        </div>
      );
    })}
  </div>
);

export const HeroSection: React.FC = () => {
  const { scanData, jobs, isRunning } = useApi();

  const candidates = scanData?.candidates ?? [];
  const ready = candidates.filter(isReady).length;
  const waiting = candidates.filter((c) => !c.stable).length + countOf(scanData?.pending);
  const posted = jobs.filter((j) => j.status === 'PUBLISHED').length;
  const needsReview = jobs.filter((j) => j.status === 'NEEDS_REVIEW').length;

  const metrics: Metric[] = [
    { target: 'scan', icon: Inbox, label: 'Ready to post', value: ready, tone: 'emerald', hint: 'Waiting in your queue' },
    { target: 'scan', icon: Clock, label: 'Waiting', value: waiting, tone: 'sky', hint: 'Still uploading' },
    { target: 'jobs', icon: CheckCircle2, label: 'Posted', value: posted, tone: 'violet', hint: 'Live on your accounts' },
    { target: 'jobs', icon: AlertTriangle, label: 'Need your check', value: needsReview, tone: needsReview ? 'amber' : 'slate', hint: needsReview ? 'Tap to review' : 'All clear' }
  ];
  const total = metrics.reduce((sum, m) => sum + m.value, 0);

  return (
    <section id="overview" className="relative overflow-hidden pb-6 pt-28 lg:pb-8 lg:pt-32">
      <div className="relative z-10 page-container">
        <div className="grid grid-cols-1 items-center gap-12 lg:grid-cols-2 lg:gap-8">
          {/* Text */}
          <motion.div className="text-center lg:text-left" initial="hidden" animate="show" variants={stagger(0.09, 0.1)}>
            <motion.span
              variants={fadeUp}
              className="inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3.5 py-1.5 text-xs font-medium text-emerald-200"
            >
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              Social media, on autopilot
            </motion.span>

            <h1 className="mt-7 text-balance text-5xl font-semibold leading-[1.02] tracking-tight text-slate-50 sm:text-6xl xl:text-7xl">
              <motion.span
                className="block"
                initial={{ opacity: 0, y: 30 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.8, ease: EASE_OUT, delay: 0.15 }}
              >
                One post.
              </motion.span>
              <motion.span
                className="text-gradient block"
                initial={{ opacity: 0, y: 30, filter: 'blur(10px)' }}
                animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
                transition={{ duration: 0.9, ease: EASE_OUT, delay: 0.35 }}
              >
                Every platform.
              </motion.span>
            </h1>

            <motion.p variants={fadeUp} className="mx-auto mt-6 max-w-lg text-pretty text-base leading-relaxed text-slate-400 sm:text-lg lg:mx-0">
              Create your post once. AutoPoster checks it, publishes it to your social accounts, and makes sure it never goes out twice.
            </motion.p>

            {/* Platforms */}
            <motion.ul variants={fadeUp} className="mt-7 flex flex-wrap justify-center gap-2 lg:justify-start" aria-label="Platforms">
              {PLATFORMS.map((p) => (
                <li
                  key={p.id}
                  className={cn(
                    'flex items-center gap-2 rounded-full border py-1.5 pl-2 pr-3 text-sm',
                    p.live ? 'border-emerald-500/30 bg-emerald-500/[0.08] text-slate-100' : 'border-white/[0.08] bg-white/[0.03] text-slate-400'
                  )}
                >
                  <span className="grid h-6 w-6 place-items-center rounded-full bg-white/[0.08]">
                    <p.Icon className="h-3.5 w-3.5" />
                  </span>
                  {p.name}
                  <span className={cn('text-[11px]', p.live ? 'text-emerald-300' : 'text-slate-500')}>{p.live ? '· Live' : '· Soon'}</span>
                </li>
              ))}
            </motion.ul>

            <motion.div variants={fadeUp} className="mt-9 flex flex-wrap justify-center gap-3 lg:justify-start">
              <button type="button" onClick={() => scrollToSection('intake')} className="btn btn-primary group px-6">
                Create a post
                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
              </button>
              <button type="button" onClick={() => scrollToSection('runner')} className="btn btn-ghost px-6">
                <Play className="h-4 w-4 text-emerald-400" aria-hidden="true" />
                Publish queue
              </button>
            </motion.div>
          </motion.div>

          {/* Globe with orbiting platforms */}
          <motion.div
            initial={{ opacity: 0, scale: 0.92 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 1.1, ease: EASE_OUT, delay: 0.3 }}
            className="relative mx-auto aspect-square w-full max-w-[540px]"
          >
            <div className="absolute inset-[12%] rounded-full bg-emerald-500/[0.10] blur-3xl" aria-hidden="true" />
            <div className="absolute inset-[10%]">
              <Suspense fallback={null}>
                <NetworkGlobe3D isProcessing={isRunning} className="absolute inset-0" />
              </Suspense>
            </div>
            <PlatformOrbit />
          </motion.div>
        </div>

        {/* Stats */}
        <div className="mt-10 grid grid-cols-2 gap-3 sm:gap-4 lg:mt-12 lg:grid-cols-4">
          {metrics.map((m, i) => (
            <StatCard key={m.label} index={i} label={m.label} value={m.value} icon={m.icon} tone={m.tone} hint={m.hint} total={total} onClick={() => scrollToSection(m.target)} />
          ))}
        </div>
      </div>
    </section>
  );
};
