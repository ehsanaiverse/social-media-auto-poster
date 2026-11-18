import React, { useEffect, useRef } from 'react';
import { animate, motion, useInView, useMotionValue, useTransform, type Variants } from 'framer-motion';
import { cn } from '../../utils/cn';

export const EASE_OUT = [0.22, 1, 0.36, 1] as const;

export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 18, filter: 'blur(6px)' },
  show: { opacity: 1, y: 0, filter: 'blur(0px)', transition: { duration: 0.6, ease: EASE_OUT } }
};

export const stagger = (each = 0.07, delayChildren = 0): Variants => ({
  hidden: {},
  show: { transition: { staggerChildren: each, delayChildren } }
});

// Fades its children up once, when scrolled into view.
export const Reveal: React.FC<{ children: React.ReactNode; className?: string; delay?: number }> = ({
  children,
  className,
  delay = 0
}) => (
  <motion.div
    className={className}
    initial="hidden"
    whileInView="show"
    viewport={{ once: true, margin: '-80px' }}
    variants={{
      hidden: fadeUp.hidden,
      show: { ...(fadeUp.show as object), transition: { duration: 0.6, ease: EASE_OUT, delay } }
    }}
  >
    {children}
  </motion.div>
);

// A container whose motion.* children with `variants={fadeUp}` enter one by one.
export const Stagger: React.FC<{ children: React.ReactNode; className?: string; each?: number }> = ({
  children,
  className,
  each
}) => (
  <motion.div
    className={className}
    initial="hidden"
    whileInView="show"
    viewport={{ once: true, margin: '-60px' }}
    variants={stagger(each)}
  >
    {children}
  </motion.div>
);

// Counts up to `value` the first time it is visible, then follows changes.
export const AnimatedNumber: React.FC<{ value: number; className?: string }> = ({ value, className }) => {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const mv = useMotionValue(0);
  const rounded = useTransform(mv, (v) => Math.round(v).toLocaleString());

  useEffect(() => {
    if (!inView) return;
    const controls = animate(mv, value, { duration: 0.9, ease: EASE_OUT });
    return () => controls.stop();
  }, [inView, value, mv]);

  return (
    <motion.span ref={ref} className={cn('tabular-nums', className)}>
      {rounded}
    </motion.span>
  );
};

// A surface with a soft light that follows the pointer.
export const SpotlightCard: React.FC<React.HTMLAttributes<HTMLDivElement>> = ({ className, children, ...rest }) => {
  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty('--mx', `${e.clientX - r.left}px`);
    e.currentTarget.style.setProperty('--my', `${e.clientY - r.top}px`);
  };
  return (
    <div onPointerMove={onMove} className={cn('spotlight surface', className)} {...rest}>
      {children}
    </div>
  );
};
