import type {
  ApiError,
  HealthResponse,
  JobDetail,
  JobSummary,
  LinkedInStatusResponse,
  PostCreatePayload,
  PostCreateResponse,
  ResolveRequest,
  ResolveResponse,
  RunReport,
  RunRequest,
  ScanResponse
} from '../types/api';

// The API binds to 127.0.0.1 only (docs/api-plan.md, rule 3). Set VITE_API_BASE
// in frontend/.env to point elsewhere.
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8000/api';

// Every call goes to the real API. There is no fallback data: if the API is
// down, the call fails and the page says so.
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers: { Accept: 'application/json', ...init?.headers } });
  } catch {
    throw { status: 0, message: "Can't reach your poster. Make sure it is running." } satisfies ApiError;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = body && typeof body.detail === 'string' ? body.detail : `Something went wrong (error ${res.status}).`;
    throw { status: res.status, message: detail } satisfies ApiError;
  }
  return res.json() as Promise<T>;
}

const postJson = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export const apiService = {
  async ping(): Promise<boolean> {
    try {
      const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(2000), headers: { Accept: 'application/json' } });
      return res.ok;
    } catch {
      return false;
    }
  },

  getHealth: () => request<HealthResponse>('/health'),

  scan: (real = false) => request<ScanResponse>(`/scan?real=${real}`),

  addPost(payload: PostCreatePayload) {
    const form = new FormData();
    form.append('name', payload.name);
    form.append('caption', payload.caption);
    form.append('image', payload.image);
    // The custom header makes this a non-simple request, so another site
    // cannot submit it from a plain form (api-plan rule 7).
    return request<PostCreateResponse>('/posts', { method: 'POST', body: form, headers: { 'X-AutoPoster': '1' } });
  },

  run: (req: RunRequest) => postJson<RunReport>('/run', req),

  getJobs(last = 30, status = 'ALL') {
    const query = new URLSearchParams({ last: String(last) });
    if (status !== 'ALL') query.set('status', status);
    return request<JobSummary[]>(`/jobs?${query}`);
  },

  getJob: (idOrPrefix: string) => request<JobDetail>(`/jobs/${encodeURIComponent(idOrPrefix)}`),

  resolveJob: (id: string, req: ResolveRequest) => postJson<ResolveResponse>(`/jobs/${encodeURIComponent(id)}/resolve`, req),

  getLinkedInStatus: (check = false) => request<LinkedInStatusResponse>(`/linkedin/status?check=${check}`)
};
