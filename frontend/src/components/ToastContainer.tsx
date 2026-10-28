import React from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react';
import { useApi, type ToastMessage } from '../context/ApiContext';
import { cn } from '../utils/cn';

const STYLE: Record<ToastMessage['type'], { icon: typeof Info; tone: string; bar: string }> = {
  success: { icon: CheckCircle2, tone: 'text-emerald-400', bar: 'bg-emerald-400' },
  warning: { icon: AlertTriangle, tone: 'text-amber-400', bar: 'bg-amber-400' },
  error: { icon: XCircle, tone: 'text-rose-400', bar: 'bg-rose-400' },
  info: { icon: Info, tone: 'text-sky-300', bar: 'bg-sky-300' }
};

export const ToastContainer: React.FC = () => {
  const { toasts, removeToast } = useApi();

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-[80] flex flex-col items-center gap-2.5 p-4 sm:items-end sm:p-6" aria-live="polite" aria-relevant="additions">
      <AnimatePresence initial={false}>
        {toasts.map((t) => {
          const s = STYLE[t.type];
          const Icon = s.icon;
          return (
            <motion.div
              key={t.id}
              layout
              role={t.type === 'error' ? 'alert' : 'status'}
              initial={{ opacity: 0, y: 24, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1, transition: { type: 'spring', stiffness: 420, damping: 32 } }}
              exit={{ opacity: 0, x: 40, transition: { duration: 0.18 } }}
              className="pointer-events-auto relative flex w-full max-w-sm items-start gap-3 overflow-hidden rounded-xl border border-white/[0.08] bg-[#0E1311]/95 p-4 shadow-[0_20px_50px_-15px_rgba(0,0,0,0.9)] backdrop-blur-xl"
            >
              <span className={cn('absolute inset-y-0 left-0 w-0.5', s.bar)} />
              <Icon className={cn('mt-0.5 h-5 w-5 shrink-0', s.tone)} aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-slate-100">{t.title}</p>
                {t.message && <p className="mt-1 break-words text-xs leading-relaxed text-slate-400">{t.message}</p>}
              </div>
              <button
                type="button"
                onClick={() => removeToast(t.id)}
                aria-label="Dismiss"
                className="-m-1 grid h-8 w-8 shrink-0 place-items-center rounded-lg text-slate-500 hover:bg-white/5 hover:text-white"
              >
                <X className="h-4 w-4" />
              </button>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
};
