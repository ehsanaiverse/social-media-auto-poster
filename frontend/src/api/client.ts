const API_BASE = import.meta.env.VITE_API_BASE || '/api'

export interface Job {
  id: number
  platform: string
  stem: string
  media_path: string
  caption_path: string
  media_hash: string | null
  caption_hash: string | null
  status: string
  error: string | null
  platform_post_id: string | null
}

export interface ScanResult {
  platform: string
  candidates: Array<{ stem: string; media_path: string; caption_path: string }>
  pending: string[]
  ambiguous: string[][]
}

export interface RunResult {
  platform: string
  candidates: number
  pending: string[]
  ambiguous: string[][]
  jobs: Array<{
    stem: string
    status: string
    errors: string[]
    simulated: boolean
    platform_post_id: string | null
    destination: string | null
    retry_later: boolean
  }>
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
  scan(platform: string = 'LINKEDIN'): Promise<ScanResult> {
    return request(`/scan?platform=${platform}`)
  },

  run(platform: string = 'LINKEDIN'): Promise<RunResult> {
    return request(`/run`, {
      method: 'POST',
      body: JSON.stringify({}),
    })
  },

  listJobs(platform?: string, limit: number = 100): Promise<Job[]> {
    const params = new URLSearchParams()
    if (platform) params.set('platform', platform)
    params.set('limit', String(limit))
    return request(`/jobs?${params}`)
  },

  getJob(id: number): Promise<Job> {
    return request(`/jobs/${id}`)
  },
}

export default api