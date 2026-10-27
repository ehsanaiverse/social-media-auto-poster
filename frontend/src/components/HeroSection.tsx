import { motion } from 'framer-motion'
import { Zap, Shield, Share2, Layers } from 'lucide-react'

export default function HeroSection() {
  const features = [
    { icon: Zap, text: 'LangGraph Workflow', color: 'text-sky-400' },
    { icon: Shield, text: 'SQLite Storage', color: 'text-emerald-400' },
    { icon: Share2, text: 'LinkedIn Ingestion', color: 'text-blue-400' },
    { icon: Layers, text: 'SHA-256 Deduplication', color: 'text-teal-400' },
  ]

  return (
    <section className="relative pt-6 pb-10 flex flex-col items-center justify-center text-center">
      <div className="max-w-3xl mx-auto flex flex-col items-center">
        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
          className="mb-5"
        >
          <span className="inline-flex items-center gap-2 px-3.5 py-1 rounded-full bg-sky-500/10 border border-sky-500/20 text-sky-300 text-xs font-medium tracking-wide">
            <span className="w-1.5 h-1.5 rounded-full bg-sky-400 animate-pulse" />
            Folder-Driven Ingestion Active
          </span>
        </motion.div>

        <motion.h1
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1, duration: 0.4 }}
          className="text-4xl sm:text-5xl md:text-6xl font-display font-extrabold tracking-tight text-slate-100 leading-[1.15] mb-5 max-w-3xl"
        >
          Automate Your{' '}
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-sky-400 via-blue-400 to-cyan-300">
            Social Media
          </span>{' '}
          Publishing
        </motion.h1>

        <motion.p
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2, duration: 0.4 }}
          className="text-base sm:text-lg text-slate-400 mb-7 max-w-2xl leading-relaxed"
        >
          Drop paired media and captions into your platform folders. The LangGraph pipeline detects new content, validates integrity, prevents duplicates, and safely publishes.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3, duration: 0.4 }}
          className="flex flex-wrap items-center justify-center gap-2.5"
        >
          {features.map((feature, i) => (
            <div
              key={i}
              className="flex items-center gap-2 px-3.5 py-1.5 rounded-lg border border-slate-800 bg-slate-900/60 backdrop-blur-sm shadow-sm"
            >
              <feature.icon className={`w-3.5 h-3.5 ${feature.color}`} />
              <span className="text-xs font-medium text-slate-300">{feature.text}</span>
            </div>
          ))}
        </motion.div>
      </div>
    </section>
  )
}