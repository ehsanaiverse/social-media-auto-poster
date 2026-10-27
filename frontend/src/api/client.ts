const API_BASE = import.meta.env.VITE_API_BASE || '/api'

// Shapes match backend/app/models/schemas.py.
export interface Job {
  job_id: string // UUID as 32 hex characters
  platform: string
  mode: 'simulated' | 'real'
  stem: string
  media_name: string
  caption_name: string
  media_sha256: string | null
  caption_sha256: string | null
  status: string
  platform_post_id: string | null
  destination: string | null
  errors: string[]
  created_at: string
  updated_at: string
}

export interface ScanResult {
  platform: string
  candidates: Array<{
    stem: string
    media_name: string
    caption_name: string
    stable: boolean
    unstable_files: string[]
    valid: boolean
    errors: string[]
    duplicate_of: string | null
  }>
  pending: string[]
  ambiguous: string[][]
  interrupted_jobs: number
}

export interface RunResult {
  exit_code: number
  simulated: boolean
  completed: boolean
  candidates: number
  recovered: Array<{
    job_id: string
    stem: string
    mode: string
    status: string
    notes: string[]
    destination: string | null
  }>
  jobs: Array<{
    stem: string
    result: string
    note: string
    details: string[]
    outcome: string
    problem: boolean
  }>
  pending: string[]
  ambiguous: string[][]
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${API_BASE}${path}`
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(error.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  // LinkedIn is the only platform; the backend scans its incoming folder.
  scan(): Promise<ScanResult> {
    return request(`/scan`)
  },

  // Publishing is simulated until Milestone 4. 409 if another run is in progress.
  run(): Promise<RunResult> {
    return request(`/run`, { method: 'POST' })
  },

  listJobs(limit: number = 20, status?: string): Promise<Job[]> {
    const params = new URLSearchParams()
    params.set('limit', String(limit))
    if (status) params.set('status', status)
    return request(`/jobs?${params}`)
  },

  // A full job ID, or a unique prefix of it.
  getJob(jobId: string): Promise<Job> {
    return request(`/jobs/${encodeURIComponent(jobId)}`)
  },
}

export default api