import React, { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { CheckCircle2, GitBranch, ImagePlus, Send, ShieldCheck, type LucideIcon } from 'lucide-react';
import { cn } from '../utils/cn';
import { SectionHeading } from './ui/SectionHeading';
import { EASE_OUT, Reveal } from './ui/motion';

interface Step {
  icon: LucideIcon;
  title: string;
  text: string;
}

const STEPS: Step[] = [
  { icon: ImagePlus, title: 'Add', text: 'Drop in a picture and write your caption.' },
  { icon: ShieldCheck, title: 'Check', text: 'We make sure the files are complete and not posted before.' },
  { icon: Send, title: 'Post', text: 'It goes out to your accounts, exactly once.' },
  { icon: CheckCircle2, title: 'Done', text: 'It is saved in your history, and the files are tidied away.' }
];

export const WorkflowGraph: React.FC = () => {
  // One step lights up at a time, looping, to show the flow.
  const [active, setActive] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setActive((a) => (a + 1) % STEPS.length), 2200);
    return () => clearInterval(id);
  }, []);

  return (
    <section id="workflow" className="relative py-16 lg:py-20">
      <div className="hr-fade absolute inset-x-0 top-0" />
      <div className="page-container">
        <SectionHeading index="04" icon={GitBranch} eyebrow="How it works" title="Four simple steps">
          You add the post. We take care of the rest.
        </SectionHeading>

        <Reveal className="relative">
          {/* Connector line with a moving light (desktop) */}
          <div className="absolute left-[12.5%] right-[12.5%] top-10 hidden h-px bg-white/[0.08] md:block" aria-hidden="true">
            <motion.div
              className="absolute inset-y-0 left-0 bg-gradient-to-r from-emerald-500 to-teal-300"
              animate={{ width: `${(active / (STEPS.length - 1)) * 100}%` }}
              transition={{ duration: 0.8, ease: EASE_OUT }}
            />
            <motion.div
              className="absolute -top-[3px] h-[7px] w-[7px] rounded-full bg-emerald-300 shadow-[0_0_14px_4px_rgba(52,211,153,0.6)]"
              animate={{ left: `${(active / (STEPS.length - 1)) * 100}%` }}
              transition={{ duration: 0.8, ease: EASE_OUT }}
              style={{ marginLeft: -3 }}
            />
          </div>

          <ol className="grid grid-cols-1 gap-5 sm:grid-cols-2 md:grid-cols-4">
            {STEPS.map((step, i) => {
              const Icon = step.icon;
              const on = i <= active;
              const current = i === active;
              return (
                <motion.li
                  key={step.title}
                  initial={{ opacity: 0, y: 20 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.5, ease: EASE_OUT, delay: i * 0.1 }}
                  className="flex flex-col items-center text-center"
                >
                  <motion.span
                    animate={current ? { scale: [1, 1.12, 1] } : { scale: 1 }}
                    transition={{ duration: 0.8 }}
                    className={cn(
                      'relative grid h-20 w-20 place-items-center rounded-3xl border transition-colors duration-500',
                      on
                        ? 'border-emerald-400/50 bg-emerald-500/15 text-emerald-200 shadow-[0_0_40px_-8px_rgba(16,185,129,0.7)]'
                        : 'border-white/[0.08] bg-[#0D1210] text-slate-500'
                    )}
                  >
                    {current && (
                      <motion.span
                        className="absolute inset-0 rounded-3xl border border-emerald-400/60"
                        initial={{ opacity: 0.8, scale: 1 }}
                        animate={{ opacity: 0, scale: 1.4 }}
                        transition={{ duration: 1.4, repeat: Infinity }}
                      />
                    )}
                    <Icon className="h-8 w-8" aria-hidden="true" />
                    <span className="absolute -right-2 -top-2 grid h-6 w-6 place-items-center rounded-full bg-[#0A0C0B] text-xs font-semibold text-slate-300 ring-1 ring-white/15">
                      {i + 1}
                    </span>
                  </motion.span>
                  <h3 className={cn('mt-5 text-lg font-semibold transition-colors', on ? 'text-slate-50' : 'text-slate-400')}>{step.title}</h3>
                  <p className="mt-1.5 max-w-[220px] text-sm leading-relaxed text-slate-400">{step.text}</p>
                </motion.li>
              );
            })}
          </ol>
        </Reveal>

        <Reveal delay={0.2} className="mx-auto mt-14 max-w-2xl">
          <div className="surface flex items-start gap-4 p-5">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-amber-500/10 text-amber-300 ring-1 ring-inset ring-amber-500/25">
              <ShieldCheck className="h-5 w-5" aria-hidden="true" />
            </span>
            <p className="text-sm leading-relaxed text-slate-300">
              <span className="font-semibold text-slate-100">Safe by default.</span> Nothing is posted twice. If we can't tell
              whether a post went through, we stop and ask you instead of trying again.
            </p>
          </div>
        </Reveal>
      </div>
    </section>
  );
};
