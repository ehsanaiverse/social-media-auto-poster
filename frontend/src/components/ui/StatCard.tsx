import React from 'react';
import { motion } from 'framer-motion';
import type { LucideIcon } from 'lucide-react';
import { cn } from '../../utils/cn';
import { AnimatedNumber, EASE_OUT } from './motion';

export type Tone = 'emerald' | 'sky' | 'violet' | 'amber' | 'rose' | 'slate';

const TONES: Record<Tone, { hex: string; text: string; tile: string }> = {
  emerald: { hex: '#34d399', text: 'text-emerald-300', tile: 'bg-emerald-500/10 ring-emerald-400/25' },
  sky: { hex: '#38bdf8', text: 'text-sky-300', tile: 'bg-sky-500/10 ring-sky-400/25' },
  violet: { hex: '#a78bfa', text: 'text-violet-300', tile: 'bg-violet-500/10 ring-violet-400/25' },
  amber: { hex: '#fbbf24', text: 'text-amber-300', tile: 'bg-amber-500/10 ring-amber-400/25' },
  rose: { hex: '#fb7185', text: 'text-rose-300', tile: 'bg-rose-500/10 ring-rose-400/25' },
  slate: { hex: '#94a3b8', text: 'text-slate-300', tile: 'bg-white/[0.05] ring-white/10' }
};

interface StatCardProps {
  label: string;
  value: number;
  icon: LucideIcon;
  tone: Tone;
  hint?: string;
  // When given, a bar shows this value's share of the total.
  total?: number;
  onClick?: () => void;
  index?: number;
}

// The number card used on Home and in the queue: label, big count, hint, and a share bar.
export const StatCard: React.FC<StatCardProps> = ({ label, value, icon: Icon, tone, hint, total, onClick, index = 0 }) => {
  const t = TONES[tone];
  const share = total ? Math.min(100, (value / total) * 100) : 0;
  const interactive = Boolean(onClick);

  return (
    <motion.div
      initial={{ opacity: 0, y: 18 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-40px' }}
      transition={{ duration: 0.55, ease: EASE_OUT, delay: index * 0.08 }}
      whileHover={{ y: -4 }}
      role={interactive ? 'button' : undefined}
      tabIndex={interactive ? 0 : undefined}
      onClick={onClick}
      onKeyDown={interactive ? (e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onClick?.()) : undefined}
      style={{ '--tone': t.hex } as React.CSSProperties}
      className={cn(
        'group relative isolate flex h-full flex-col overflow-hidden rounded-2xl border border-white/[0.07] bg-gradient-to-b from-white/[0.04] to-white/[0.01] p-5 transition-[border-color,box-shadow] duration-300',
        'hover:border-[color-mix(in_srgb,var(--tone)_40%,transparent)] hover:shadow-[0_20px_50px_-25px_var(--tone)]',
        interactive && 'cursor-pointer'
      )}
    >
      {/* Corner glow and top accent */}
      <span
        className="pointer-events-none absolute -right-12 -top-12 -z-10 h-36 w-36 rounded-full opacity-[0.12] blur-2xl transition-opacity duration-500 group-hover:opacity-25"
        style={{ backgroundColor: t.hex }}
        aria-hidden="true"
      />
      <span
        className="pointer-events-none absolute inset-x-6 top-0 h-px opacity-60"
        style={{ background: `linear-gradient(90deg, transparent, ${t.hex}, transparent)` }}
        aria-hidden="true"
      />

      <div className="flex items-start justify-between gap-3">
        <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-slate-400">{label}</span>
        <span className={cn('grid h-9 w-9 shrink-0 place-items-center rounded-xl ring-1 ring-inset transition-transform duration-300 group-hover:scale-110 group-hover:-rotate-6', t.tile, t.text)}>
          <Icon className="h-[18px] w-[18px]" aria-hidden="true" />
        </span>
      </div>

      <AnimatedNumber value={value} className={cn('mt-3 block text-4xl font-semibold leading-none tracking-tight', value === 0 ? 'text-slate-500' : 'text-slate-50')} />
      {hint && <p className="mt-2 text-xs text-slate-500">{hint}</p>}

      {total !== undefined && (
        <div className="mt-auto pt-4">
          <div className="h-1 overflow-hidden rounded-full bg-white/[0.06]">
            <motion.div
              className="h-full rounded-full"
              style={{ backgroundColor: t.hex, boxShadow: `0 0 10px ${t.hex}` }}
              initial={{ width: 0 }}
              whileInView={{ width: `${share}%` }}
              viewport={{ once: true }}
              transition={{ duration: 1, ease: EASE_OUT, delay: 0.2 + index * 0.08 }}
            />
          </div>
        </div>
      )}
    </motion.div>
  );
};
