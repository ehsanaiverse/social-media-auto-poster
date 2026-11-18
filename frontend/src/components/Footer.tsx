import React from 'react';
import { ArrowUp } from 'lucide-react';
import { SECTIONS, scrollToSection } from '../lib/sections';
import { BrandMark } from './Navbar';

const PROMISES = ['Test mode unless you choose to post', 'Never posts the same thing twice', 'Asks you when something is unclear', 'Keeps a record of every post'];

export const Footer: React.FC = () => (
  <footer className="relative border-t border-white/[0.06] bg-[#070908]/80 backdrop-blur">
    <div className="page-container py-14">
      <div className="grid grid-cols-1 gap-10 md:grid-cols-12">
        <div className="space-y-3 md:col-span-5">
          <div className="flex items-center gap-3">
            <BrandMark />
            <span className="font-semibold text-slate-100">AutoPoster</span>
          </div>
          <p className="max-w-sm text-sm leading-relaxed text-slate-500">Post to all your social accounts from one place. LinkedIn today; Instagram, Facebook and X coming soon.</p>
        </div>

        <nav aria-label="Footer" className="md:col-span-3">
          <p className="mb-3 text-xs font-medium uppercase tracking-wider text-slate-500">Go to</p>
          <ul className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
            {SECTIONS.map((s) => (
              <li key={s.id}>
                <a
                  href={`#${s.id}`}
                  onClick={(e) => {
                    e.preventDefault();
                    scrollToSection(s.id);
                  }}
                  className="text-slate-400 transition-colors hover:text-emerald-300"
                >
                  {s.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="md:col-span-4">
          <p className="mb-3 text-xs font-medium uppercase tracking-wider text-slate-500">Our promises</p>
          <ul className="space-y-1.5 text-sm text-slate-400">
            {PROMISES.map((r) => (
              <li key={r} className="flex items-start gap-2">
                <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-emerald-400" />
                {r}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="mt-12 flex flex-col items-center justify-between gap-4 border-t border-white/[0.05] pt-6 text-xs text-slate-500 sm:flex-row">
        <p>© 2026 AutoPoster</p>
        <button type="button" onClick={() => scrollToSection('overview')} className="btn btn-ghost min-h-10 text-xs">
          Back to top <ArrowUp className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  </footer>
);
