import React from 'react';
import { motion } from 'framer-motion';
import { ArrowRight, CheckCircle2, FileImage, FileText, FolderOpen, Play } from 'lucide-react';
import { PLATFORMS } from '../lib/platforms';
import { scrollToSection } from '../lib/sections';
import { cn } from '../utils/cn';
import { EASE_OUT } from './ui/motion';

const STEPS = ['Put a picture and its caption in your posts folder', 'We check it and make sure it is new', 'It goes live, once, and is saved in your history'];

// Positions in the 400 x 300 drawing; the HTML tiles sit on the same points.
const FOLDER = { x: 78, y: 150 };
const TARGETS = PLATFORMS.map((p, i) => ({ platform: p, x: 330, y: 45 + i * 70 }));

const pathTo = (y: number) => `M ${FOLDER.x + 40} ${FOLDER.y} C 210 ${FOLDER.y}, 220 ${y}, 296 ${y}`;

// Light travels from the folder to each platform; only the live one is solid.
const FlowVisual: React.FC = () => (
  <div className="relative mx-auto aspect-[4/3] w-full max-w-[520px]">
    <svg viewBox="0 0 400 300" className="absolute inset-0 h-full w-full" aria-hidden="true">
      <defs>
        <linearGradient id="cta-flow" x1="0" x2="1">
          <stop offset="0" stopColor="#34d399" stopOpacity="0.15" />
          <stop offset="1" stopColor="#34d399" stopOpacity="0.8" />
        </linearGradient>
        <filter id="cta-glow">
          <feGaussianBlur stdDeviation="2.5" />
        </filter>
      </defs>
      {TARGETS.map(({ platform, y }, i) => (
        <g key={platform.id}>
          <path
            d={pathTo(y)}
            fill="none"
            stroke={platform.live ? 'url(#cta-flow)' : 'rgba(148,163,184,0.18)'}
            strokeWidth={platform.live ? 2 : 1.5}
            strokeDasharray={platform.live ? undefined : '4 6'}
          />
          <circle r={platform.live ? 4 : 2.5} fill={platform.live ? '#6ee7b7' : '#64748b'} filter={platform.live ? 'url(#cta-glow)' : undefined}>
            <animateMotion dur={platform.live ? '2.4s' : '3.6s'} begin={`${i * 0.5}s`} repeatCount="indefinite" path={pathTo(y)} />
          </circle>
          {platform.live && (
            <circle r="2" fill="#ecfdf5">
              <animateMotion dur="2.4s" begin={`${i * 0.5}s`} repeatCount="indefinite" path={pathTo(y)} />
            </circle>
          )}
        </g>
      ))}
    </svg>

    {/* Folder */}
    <motion.div
      className="absolute -translate-x-1/2 -translate-y-1/2"
      style={{ left: `${(FOLDER.x / 400) * 100}%`, top: `${(FOLDER.y / 300) * 100}%` }}
      animate={{ y: [0, -6, 0] }}
      transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }}
    >
      <div className="relative grid h-20 w-20 place-items-center rounded-3xl border border-emerald-400/30 bg-emerald-500/10 text-emerald-300 shadow-[0_0_50px_-10px_rgba(16,185,129,0.8)] backdrop-blur sm:h-24 sm:w-24">
        <FolderOpen className="h-9 w-9 sm:h-10 sm:w-10" aria-hidden="true" />
        <span className="absolute -left-4 -top-4 flex items-center gap-1 rounded-lg border border-white/10 bg-[#0d1311] px-2 py-1 text-[10px] text-slate-300 shadow-lg">
          <FileImage className="h-3 w-3 text-sky-300" aria-hidden="true" /> launch.jpg
        </span>
        <span className="absolute -bottom-4 -left-2 flex items-center gap-1 rounded-lg border border-white/10 bg-[#0d1311] px-2 py-1 text-[10px] text-slate-300 shadow-lg">
          <FileText className="h-3 w-3 text-amber-300" aria-hidden="true" /> launch.txt
        </span>
      </div>
    </motion.div>

    {/* Platforms */}
    {TARGETS.map(({ platform: p, x, y }, i) => (
      <motion.div
        key={p.id}
        className="absolute -translate-x-1/2 -translate-y-1/2"
        style={{ left: `${(x / 400) * 100}%`, top: `${(y / 300) * 100}%` }}
        initial={{ opacity: 0, scale: 0.8 }}
        whileInView={{ opacity: 1, scale: 1 }}
        viewport={{ once: true }}
        transition={{ duration: 0.5, ease: EASE_OUT, delay: 0.3 + i * 0.1 }}
      >
        <div className="flex items-center gap-2">
          <span
            className={cn('grid h-11 w-11 place-items-center rounded-xl border text-white sm:h-12 sm:w-12', p.live ? 'border-white/20' : 'border-white/10 text-slate-400')}
            style={p.live ? { backgroundColor: p.color, boxShadow: `0 0 30px -4px ${p.color}` } : { backgroundColor: '#161d1a' }}
          >
            <p.Icon className="h-5 w-5" />
          </span>
          <span className={cn('hidden rounded-full px-2 py-0.5 text-[10px] font-semibold sm:inline', p.live ? 'bg-emerald-400 text-[#04110B]' : 'bg-white/10 text-slate-400')}>
            {p.live ? 'Live' : 'Soon'}
          </span>
        </div>
      </motion.div>
    ))}
  </div>
);

export const CtaSection: React.FC = () => (
  <section className="relative py-16 lg:py-20" aria-labelledby="cta-title">
    <div className="page-container">
      <motion.div
        initial={{ opacity: 0, y: 30 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: '-60px' }}
        transition={{ duration: 0.8, ease: EASE_OUT }}
        className="ring-glow relative isolate overflow-hidden rounded-3xl border border-white/[0.08] bg-gradient-to-br from-emerald-950/50 via-[#0d1311] to-[#0a0f0d]"
      >
        <div className="pointer-events-none absolute -left-24 top-0 -z-10 h-80 w-80 rounded-full bg-emerald-500/15 blur-[100px]" aria-hidden="true" />
        <div className="pointer-events-none absolute -bottom-24 right-0 -z-10 h-72 w-72 rounded-full bg-teal-400/10 blur-[100px]" aria-hidden="true" />
        <div className="grid-pattern pointer-events-none absolute inset-0 -z-10 opacity-50" aria-hidden="true" />

        <div className="grid grid-cols-1 items-center gap-10 p-6 sm:p-10 lg:grid-cols-2 lg:gap-6 lg:p-14">
          {/* Text */}
          <div className="text-center lg:text-left">
            <span className="inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3.5 py-1.5 text-xs font-medium text-emerald-200">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
              Ready when you are
            </span>
            <h2 id="cta-title" className="mt-6 text-balance text-3xl font-semibold leading-tight tracking-tight text-slate-50 sm:text-5xl">
              <span className="block">Drop it in the folder.</span>
              <span className="text-gradient block">We'll post it.</span>
            </h2>
            <p className="mx-auto mt-5 max-w-md text-pretty text-base leading-relaxed text-slate-400 lg:mx-0">
              No dashboards to fill in. Save a picture and a caption, and AutoPoster does the rest.
            </p>

            <ol className="mx-auto mt-7 max-w-md space-y-3 text-left lg:mx-0">
              {STEPS.map((step, i) => (
                <motion.li
                  key={step}
                  initial={{ opacity: 0, x: -10 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.45, ease: EASE_OUT, delay: 0.2 + i * 0.1 }}
                  className="flex items-start gap-3 text-sm text-slate-300"
                >
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" aria-hidden="true" />
                  {step}
                </motion.li>
              ))}
            </ol>

            <div className="mt-9 flex flex-wrap justify-center gap-3 lg:justify-start">
              <button type="button" onClick={() => scrollToSection('intake')} className="btn btn-primary group px-6">
                Create a post
                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
              </button>
              <button type="button" onClick={() => scrollToSection('runner')} className="btn btn-ghost px-6">
                <Play className="h-4 w-4 text-emerald-400" aria-hidden="true" />
                Publish queue
              </button>
            </div>
          </div>

          {/* Visual */}
          <FlowVisual />
        </div>
      </motion.div>
    </div>
  </section>
);
