import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { api, type Job, type ScanResult } from '../api/client'
import { StatusBadge, StatusStats } from '../components/StatusBadge'
import HeroSection from '../components/HeroSection'
import { Button } from '../components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card'
import { Loader2, Play, RefreshCw, Archive, AlertTriangle } from 'lucide-react'
import toast from 'react-hot-toast'

export default function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [scanResult, setScanResult] = useState<ScanResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [scanning, setScanning] = useState(false)

  const fetchData = async () => {
    try {
      const [jobsRes, scanRes] = await Promise.all([
        api.listJobs(undefined, 50),
        api.scan(),
      ])
      setJobs(jobsRes)
      setScanResult(scanRes)
    } catch (error) {
      toast.error('Failed to fetch data')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  const handleRun = async () => {
    setRunning(true)
    try {
      const result = await api.run()
      toast.success(`Run completed: ${result.candidates} candidates processed`)
      await fetchData()
    } catch (error: any) {
      toast.error(error.message || 'Run failed')
    } finally {
      setRunning(false)
    }
  }

  const handleScan = async () => {
    setScanning(true)
    try {
      const result = await api.scan()
      setScanResult(result)
      toast.success('Scan completed')
    } catch (error: any) {
      toast.error(error.message || 'Scan failed')
    } finally {
      setScanning(false)
    }
  }

  return (
    <div className="space-y-8 max-w-5xl mx-auto">
      <HeroSection />

      <div className="flex flex-wrap items-center justify-center gap-3.5 -mt-2 mb-6">
        <Button onClick={handleRun} disabled={running} size="default" className="gap-2">
          {running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4 fill-current" />}
          {running ? 'Running Pipeline...' : 'Run Pipeline Now'}
        </Button>
        <Button onClick={handleScan} disabled={scanning} variant="outline" size="default" className="gap-2">
          {scanning ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          {scanning ? 'Scanning Folders...' : 'Scan & Refresh'}
        </Button>
      </div>

      {loading ? (
        <div className="flex flex-col items-center justify-center py-20 gap-3">
          <Loader2 className="w-8 h-8 animate-spin text-sky-400" />
          <p className="text-xs text-slate-400">Loading incoming posts and job history...</p>
        </div>
      ) : (
        <>
          {scanResult && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <Card className="glass border-slate-800/80 hover:border-slate-700/80 transition-all">
                <CardHeader className="pb-2">
                  <CardTitle className="text-xs font-medium uppercase tracking-wider text-slate-400">Ready Candidates</CardTitle>
                </CardHeader>
                <CardContent>
                  <span className="text-3xl font-extrabold text-slate-100">{scanResult.candidates.length}</span>
                  <p className="text-xs text-slate-400 mt-1">paired image + caption ready</p>
                </CardContent>
              </Card>
              <Card className="glass border-slate-800/80 hover:border-slate-700/80 transition-all">
                <CardHeader className="pb-2">
                  <CardTitle className="text-xs font-medium uppercase tracking-wider text-slate-400">Pending Files</CardTitle>
                </CardHeader>
                <CardContent>
                  <span className="text-3xl font-extrabold text-slate-100">{scanResult.pending.length}</span>
                  <p className="text-xs text-slate-400 mt-1">waiting for matching partner</p>
                </CardContent>
              </Card>
              <Card className="glass border-slate-800/80 hover:border-slate-700/80 transition-all">
                <CardHeader className="pb-2">
                  <CardTitle className="text-xs font-medium uppercase tracking-wider text-slate-400">Ambiguous Sets</CardTitle>
                </CardHeader>
                <CardContent>
                  <span className="text-3xl font-extrabold text-slate-100">{scanResult.ambiguous.length}</span>
                  <p className="text-xs text-slate-400 mt-1">multiple stems needing review</p>
                </CardContent>
              </Card>
            </div>
          )}

          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-display font-bold text-slate-100">Job History</h2>
              <span className="text-xs text-slate-400">Platform Analytics</span>
            </div>
            <StatusStats jobs={jobs} />
          </div>

          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-display font-bold text-slate-100">Recent Executions</h2>
              <span className="text-xs text-slate-400">{jobs.length} total recorded</span>
            </div>
            <AnimatePresence>
              {jobs.length === 0 ? (
                <Card className="glass border-slate-800/80">
                  <CardContent className="text-center py-12">
                    <Archive className="w-10 h-10 mx-auto mb-3 text-slate-500" />
                    <p className="text-sm font-medium text-slate-300">No jobs processed yet</p>
                    <p className="text-xs text-slate-400 mt-1">Drop paired image and text files into <code className="text-sky-400 bg-sky-950/40 px-1.5 py-0.5 rounded">data/LinkedIn/incoming/</code></p>
                  </CardContent>
                </Card>
              ) : (
                <>
                  {jobs.map((job, index) => (
                    <motion.div
                      key={job.id}
                      initial={{ opacity: 0, y: 15 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -15 }}
                      transition={{ delay: index * 0.04 }}
                    >
                      <Card className="glass border-slate-800/70 hover:border-slate-700/80 mb-2.5 transition-all">
                        <CardContent className="py-4">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-3">
                              <div>
                                <h3 className="font-semibold text-slate-100 text-sm">{job.stem}</h3>
                                <p className="text-xs text-slate-400">ID #{job.id} · {job.platform}</p>
                              </div>
                            </div>
                            <StatusBadge status={job.status} />
                          </div>
                          {job.error && (
                            <div className="mt-3 p-2.5 rounded-lg bg-rose-500/10 border border-rose-500/20">
                              <div className="flex items-start gap-2">
                                <AlertTriangle className="w-4 h-4 text-rose-400 mt-0.5 flex-shrink-0" />
                                <p className="text-xs text-rose-300">{job.error}</p>
                              </div>
                            </div>
                          )}
                          {job.platform_post_id && (
                            <p className="mt-2 text-xs text-slate-400 font-mono">
                              Platform Post ID: {job.platform_post_id}
                            </p>
                          )}
                        </CardContent>
                      </Card>
                    </motion.div>
                  ))}
                </>
              )}
            </AnimatePresence>
          </div>
        </>
      )}
    </div>
  )
}