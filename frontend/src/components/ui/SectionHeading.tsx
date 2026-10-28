import React from 'react';
import { motion } from 'framer-motion';
import type { LucideIcon } from 'lucide-react';
import { fadeUp, stagger } from './motion';

interface Props {
  index?: string;
  icon: LucideIcon;
  eyebrow: string;
  title: React.ReactNode;
  children?: React.ReactNode;
  actions?: React.ReactNode;
}

export const SectionHeading: React.FC<Props> = ({ icon: Icon, eyebrow, title, children, actions }) => (
  <motion.div
    className="mx-auto mb-10 max-w-2xl text-center lg:mb-12"
    initial="hidden"
    whileInView="show"
    viewport={{ once: true, margin: '-80px' }}
    variants={stagger(0.08)}
  >
    <motion.span variants={fadeUp} className="overline-chip">
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      <span>{eyebrow}</span>
    </motion.span>
    <motion.h2
      variants={fadeUp}
      className="mt-5 text-balance text-3xl font-semibold tracking-tight text-slate-50 sm:text-4xl"
    >
      {title}
    </motion.h2>
    {children && (
      <motion.p variants={fadeUp} className="mt-4 text-pretty text-sm leading-relaxed text-slate-400 sm:text-base">
        {children}
      </motion.p>
    )}
    {actions && (
      <motion.div variants={fadeUp} className="mt-7 flex flex-wrap items-center justify-center gap-2.5">
        {actions}
      </motion.div>
    )}
  </motion.div>
);

export const Code: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <code className="whitespace-nowrap rounded border border-emerald-900/50 bg-emerald-950/50 px-1.5 py-0.5 font-mono text-[0.85em] text-emerald-300">
    {children}
  </code>
);
