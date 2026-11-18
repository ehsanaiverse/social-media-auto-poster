import React, { useEffect, useState } from 'react';
import { AnimatePresence, motion, useMotionValueEvent, useScroll, useSpring } from 'framer-motion';
import { Menu, X } from 'lucide-react';
import { useApi } from '../context/ApiContext';
import { useScrollSpy } from '../hooks/useScrollSpy';
import { SECTIONS, scrollToSection } from '../lib/sections';
import { cn } from '../utils/cn';
import { EASE_OUT } from './ui/motion';

const SECTION_IDS = SECTIONS.map((s) => s.id);

export const BrandMark: React.FC<{ className?: string }> = ({ className }) => (
  <span
    className={cn(
      'relative grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-emerald-400 to-teal-700 shadow-[0_0_24px_-6px_rgba(16,185,129,0.7)] ring-1 ring-inset ring-white/20',
      className
    )}
    aria-hidden="true"
  >
    <svg viewBox="0 0 24 24" className="h-5 w-5 text-[#04110B]" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 17 10 7l4 6 2-3 4 7" />
    </svg>
  </span>
);

// Online or Offline. While offline the page checks again on its own every few seconds.
export const ConnectionPill: React.FC<{ className?: string }> = ({ className }) => {
  const { connection } = useApi();
  const online = connection === 'online';
  return (
    <div
      className={cn('flex min-h-10 items-center gap-2.5 rounded-xl border border-white/[0.08] bg-white/[0.03] px-3.5 text-xs font-medium text-slate-300', className)}
      role="status"
    >
      <span className="relative flex h-2 w-2">
        {online && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />}
        <span className={cn('relative inline-flex h-2 w-2 rounded-full', online ? 'bg-emerald-400' : 'bg-rose-400')} />
      </span>
      {online ? 'Online' : 'Offline'}
    </div>
  );
};

export const Navbar: React.FC = () => {
  const active = useScrollSpy(SECTION_IDS);
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  const { scrollY, scrollYProgress } = useScroll();
  const progress = useSpring(scrollYProgress, { stiffness: 200, damping: 40, restDelta: 0.001 });
  useMotionValueEvent(scrollY, 'change', (y) => setScrolled(y > 12));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setMenuOpen(false);
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  // The mobile menu closes when the screen grows past it.
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 1024px)');
    const onChange = () => mq.matches && setMenuOpen(false);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  const go = (id: string) => {
    scrollToSection(id);
    setMenuOpen(false);
  };

  return (
    <header className="fixed inset-x-0 top-0 z-50">
      <motion.div
        initial={{ y: -24, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.6, ease: EASE_OUT }}
        className={cn('mx-auto transition-[max-width,padding] duration-500', scrolled ? 'max-w-6xl px-4 pt-3' : 'max-w-7xl px-0 pt-0')}
      >
        <nav
          aria-label="Main"
          className={cn(
            'relative flex h-16 items-center justify-between gap-3 px-4 transition-[background-color,border-color,border-radius,box-shadow] duration-500 sm:px-6',
            scrolled || menuOpen
              ? 'rounded-2xl border border-white/[0.08] bg-[#0B0F0D]/75 shadow-[0_20px_60px_-25px_rgba(0,0,0,0.9)] backdrop-blur-xl backdrop-saturate-150'
              : 'border border-transparent bg-transparent'
          )}
        >
          <button type="button" onClick={() => go('overview')} className="flex min-h-11 items-center gap-3 rounded-xl pr-2" aria-label="AutoPoster, back to top">
            <BrandMark />
            <span className="text-left leading-tight">
              <span className="block text-[15px] font-semibold tracking-tight text-slate-50">AutoPoster</span>
              <span className="hidden font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500 sm:block">Social auto poster</span>
            </span>
          </button>

          <ul className="hidden items-center gap-0.5 lg:flex">
            {SECTIONS.map((s) => {
              const isActive = active === s.id;
              return (
                <li key={s.id}>
                  <a
                    href={`#${s.id}`}
                    onClick={(e) => {
                      e.preventDefault();
                      go(s.id);
                    }}
                    aria-current={isActive ? 'location' : undefined}
                    className={cn('relative block rounded-lg px-3 py-2 text-[13px] font-medium transition-colors xl:px-3.5', isActive ? 'text-slate-50' : 'text-slate-400 hover:text-slate-200')}
                  >
                    {isActive && (
                      <motion.span layoutId="nav-active" className="absolute inset-0 rounded-lg bg-white/[0.07] ring-1 ring-inset ring-white/10" transition={{ type: 'spring', stiffness: 420, damping: 36 }}>
                        <span className="absolute inset-x-3 -bottom-px h-px bg-gradient-to-r from-transparent via-emerald-400 to-transparent" />
                      </motion.span>
                    )}
                    <span className="relative">{s.label}</span>
                  </a>
                </li>
              );
            })}
          </ul>

          <div className="flex items-center gap-2">
            <ConnectionPill className="hidden sm:flex" />
            <button
              type="button"
              onClick={() => setMenuOpen((o) => !o)}
              aria-expanded={menuOpen}
              aria-controls="mobile-menu"
              aria-label={menuOpen ? 'Close menu' : 'Open menu'}
              className="grid h-11 w-11 place-items-center rounded-xl border border-white/[0.08] bg-white/[0.03] text-slate-200 lg:hidden"
            >
              <AnimatePresence mode="wait" initial={false}>
                <motion.span key={menuOpen ? 'x' : 'menu'} initial={{ rotate: -90, opacity: 0 }} animate={{ rotate: 0, opacity: 1 }} exit={{ rotate: 90, opacity: 0 }} transition={{ duration: 0.15 }}>
                  {menuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
                </motion.span>
              </AnimatePresence>
            </button>
          </div>

          <motion.span
            aria-hidden="true"
            style={{ scaleX: progress }}
            className={cn('absolute inset-x-4 bottom-0 h-px origin-left bg-gradient-to-r from-emerald-500 via-teal-300 to-emerald-500 transition-opacity duration-500', scrolled ? 'opacity-80' : 'opacity-0')}
          />
        </nav>

        <AnimatePresence>
          {menuOpen && (
            <motion.div
              id="mobile-menu"
              initial={{ opacity: 0, y: -8, height: 0 }}
              animate={{ opacity: 1, y: 0, height: 'auto', transition: { duration: 0.3, ease: EASE_OUT } }}
              exit={{ opacity: 0, y: -8, height: 0, transition: { duration: 0.18 } }}
              className="mx-3 mt-2 overflow-hidden rounded-2xl border border-white/[0.08] bg-[#0B0F0D]/95 shadow-2xl backdrop-blur-xl lg:hidden"
            >
              <motion.ul
                className="grid grid-cols-2 gap-1.5 p-3"
                initial="hidden"
                animate="show"
                variants={{ hidden: {}, show: { transition: { staggerChildren: 0.03, delayChildren: 0.05 } } }}
              >
                {SECTIONS.map((s) => {
                  const Icon = s.icon;
                  const isActive = active === s.id;
                  return (
                    <motion.li key={s.id} variants={{ hidden: { opacity: 0, y: 6 }, show: { opacity: 1, y: 0 } }}>
                      <a
                        href={`#${s.id}`}
                        onClick={(e) => {
                          e.preventDefault();
                          go(s.id);
                        }}
                        aria-current={isActive ? 'location' : undefined}
                        className={cn(
                          'flex min-h-12 items-center gap-2.5 rounded-xl px-3.5 text-sm font-medium transition-colors',
                          isActive ? 'bg-emerald-500/10 text-emerald-200 ring-1 ring-inset ring-emerald-500/25' : 'text-slate-300 hover:bg-white/[0.04]'
                        )}
                      >
                        <Icon className={cn('h-4 w-4', isActive ? 'text-emerald-300' : 'text-slate-500')} aria-hidden="true" />
                        {s.label}
                      </a>
                    </motion.li>
                  );
                })}
              </motion.ul>
              <div className="border-t border-white/[0.06] p-3">
                <ConnectionPill className="w-full justify-between" />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </header>
  );
};
