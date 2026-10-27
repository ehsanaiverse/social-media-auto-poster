import { motion } from 'framer-motion'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'
import { Button } from './ui/button'
import { ChevronRight, Clock, CheckCircle, XCircle, AlertTriangle, Archive, Play } from 'lucide-react'

const statusConfig: Record<string, { color: string; bg: string; icon: any; label: string }> = {
  DISCOVERED: { color: 'text-sky-400', bg: 'bg-sky-500/10 border border-sky-500/20', icon: Clock, label: 'Discovered' },
  VALIDATED: { color: 'text-emerald-400', bg: 'bg-emerald-500/10 border border-emerald-500/20', icon: CheckCircle, label: 'Validated' },
  PUBLISHING: { color: 'text-amber-400', bg: 'bg-amber-500/10 border border-amber-500/20', icon: Play, label: 'Publishing' },
  PUBLISHED: { color: 'text-emerald-400', bg: 'bg-emerald-500/10 border border-emerald-500/20', icon: CheckCircle, label: 'Published' },
  SIMULATED: { color: 'text-blue-400', bg: 'bg-blue-500/10 border border-blue-500/20', icon: Play, label: 'Simulated' },
  FAILED: { color: 'text-rose-400', bg: 'bg-rose-500/10 border border-rose-500/20', icon: XCircle, label: 'Failed' },
  NEEDS_REVIEW: { color: 'text-amber-400', bg: 'bg-amber-500/10 border border-amber-500/20', icon: AlertTriangle, label: 'Needs Review' },
  DUPLICATE: { color: 'text-slate-400', bg: 'bg-slate-500/10 border border-slate-500/20', icon: CheckCircle, label: 'Duplicate' },
  WAITING: { color: 'text-sky-400', bg: 'bg-sky-500/10 border border-sky-500/20', icon: Clock, label: 'Waiting' },
  ERROR: { color: 'text-rose-400', bg: 'bg-rose-500/10 border border-rose-500/20', icon: XCircle, label: 'Error' },
}

export function StatusBadge({ status, size = 'sm' }: { status: string; size?: 'sm' | 'lg' }) {
  const config = statusConfig[status] || statusConfig.ERROR
  const Icon = config.icon

  return (
    <span className={`
      inline-flex items-center gap-1.5 rounded-full font-medium
      ${config.bg} ${config.color}
      ${size === 'lg' ? 'px-3 py-1.5 text-sm' : 'px-2 py-0.5 text-xs'}
    `}>
      <Icon className={`${size === 'lg' ? 'w-4 h-4' : 'w-3 h-3'} ${status === 'PUBLISHING' ? 'animate-spin' : ''}`} />
      {config.label}
    </span>
  )
}

export function StatusStats({ jobs }: { jobs: any[] }) {
  const stats = jobs.reduce((acc, job) => {
    const status = job.status
    if (!acc[status]) acc[status] = 0
    acc[status]++
    return acc
  }, {} as Record<string, number>)

  const total = jobs.length

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {Object.entries(statusConfig).map(([status, config]) => {
        if (!stats[status]) return null
        const Icon = config.icon
        return (
          <motion.div
            key={status}
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            className="glass rounded-xl p-4 flex items-center gap-3"
          >
            <div className={`w-10 h-10 rounded-lg ${config.bg} flex items-center justify-center`}>
              <Icon className={`w-5 h-5 ${config.color}`} />
            </div>
            <div>
              <p className="text-2xl font-bold text-slate-100">{stats[status]}</p>
              <p className="text-xs text-slate-400">{config.label}</p>
            </div>
          </motion.div>
        )
      })}
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        className="glass rounded-xl p-4 flex items-center gap-3"
      >
        <div className="w-10 h-10 rounded-lg bg-sky-500/10 flex items-center justify-center">
          <Archive className="w-5 h-5 text-sky-400" />
        </div>
        <div>
          <p className="text-2xl font-bold text-slate-100">{total}</p>
          <p className="text-xs text-slate-400">Total Jobs</p>
        </div>
      </motion.div>
    </div>
  )
}