import { motion } from 'framer-motion'
import { ChevronDown, Share2 } from 'lucide-react'
import { useState } from 'react'
import { Button } from './ui/button'

interface HeaderProps {
  onRun?: () => void
  scanning?: boolean
  running?: boolean
}

export default function Header({ onRun, scanning, running }: HeaderProps) {
  const [isMenuOpen, setIsMenuOpen] = useState(false)

  return (
    <header className="glass-strong sticky top-0 z-50 border-b border-slate-800/80">
      <div className="container mx-auto px-4 py-3.5">
        <div className="flex items-center justify-between">
          <motion.div
            initial={{ opacity: 0, x: -15 }}
            animate={{ opacity: 1, x: 0 }}
            className="flex items-center gap-3"
          >
            <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-sky-500 to-blue-600 flex items-center justify-center shadow-sm border border-sky-400/20">
              <Share2 className="w-4 h-4 text-white" />
            </div>
            <div>
              <h1 className="text-base font-display font-bold text-slate-100 tracking-tight">
                AutoPoster
              </h1>
              <p className="text-[11px] text-slate-400 font-medium -mt-0.5">
                Social Media Automation
              </p>
            </div>
          </motion.div>

          <nav className="hidden md:flex items-center gap-6">
            {['Dashboard', 'Jobs', 'Settings'].map((item) => (
              <button
                key={item}
                className="text-xs font-medium text-slate-400 hover:text-slate-100 transition-colors"
              >
                {item}
              </button>
            ))}
          </nav>

          <div className="flex items-center gap-3">
            <Button
              onClick={onRun}
              disabled={running || scanning}
              variant={running || scanning ? 'outline' : 'default'}
              size="sm"
              className="text-xs font-semibold"
            >
              {running ? 'Running...' : scanning ? 'Scanning...' : 'Run Now'}
            </Button>

            <button
              className="md:hidden p-2 text-slate-400 hover:text-white"
              onClick={() => setIsMenuOpen(!isMenuOpen)}
            >
              <ChevronDown
                className={`w-5 h-5 transition-transform ${isMenuOpen ? 'rotate-180' : ''}`}
              />
            </button>
          </div>
        </div>

        {isMenuOpen && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="md:hidden mt-3 pb-3 border-t border-slate-800"
          >
            {['Dashboard', 'Jobs', 'Settings'].map((item) => (
              <button
                key={item}
                className="block w-full text-left py-2 text-xs font-medium text-slate-400 hover:text-white"
              >
                {item}
              </button>
            ))}
          </motion.div>
        )}
      </div>
    </header>
  )
}