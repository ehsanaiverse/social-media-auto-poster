import type {
  ApiError,
  HealthResponse,
  JobDetail,
  JobSummary,
  LinkedInStatusResponse,
  PlatformInfo,
  PostCheckResponse,
  PostCreatePayload,
  PostCreateResponse,
  ResolveRequest,
  ResolveResponse,
  RunReport,
  RunRequest,
  ScanResponse,
  ScheduleCreateRequest,
  ScheduleListResponse,
  ScheduleReconfirmRequest,
  ScheduleResponse
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

  // `platform` is left out for LinkedIn, so those requests are exactly as before.
  scan: (real = false, platform?: string) =>
    request<ScanResponse>(`/scan?real=${real}${platform ? `&platform=${encodeURIComponent(platform)}` : ''}`),

  getPlatforms: () => request<PlatformInfo[]>('/platforms'),

  // The backend's verdict on an image and caption, while the post is being written. Writes nothing.
  checkPost(payload: { caption: string; image: File; platform?: string }) {
    const form = new FormData();
    form.append('caption', payload.caption);
    form.append('image', payload.image);
    if (payload.platform) form.append('platform', payload.platform);
    return request<PostCheckResponse>('/posts/check', { method: 'POST', body: form, headers: { 'X-AutoPoster': '1' } });
  },

  addPost(payload: PostCreatePayload) {
    const form = new FormData();
    form.append('name', payload.name);
    form.append('caption', payload.caption);
    form.append('image', payload.image);
    if (payload.platform) form.append('platform', payload.platform);
    // The custom header makes this a non-simple request, so another site
    // cannot submit it from a plain form (api-plan rule 7).
    return request<PostCreateResponse>('/posts', { method: 'POST', body: form, headers: { 'X-AutoPoster': '1' } });
  },

  run: (req: RunRequest) => postJson<RunReport>('/run', req),

  getJobs(last = 30, status = 'ALL', platform?: string) {
    const query = new URLSearchParams({ last: String(last) });
    if (status !== 'ALL') query.set('status', status);
    if (platform) query.set('platform', platform);
    return request<JobSummary[]>(`/jobs?${query}`);
  },

  getJob: (idOrPrefix: string) => request<JobDetail>(`/jobs/${encodeURIComponent(idOrPrefix)}`),

  resolveJob: (id: string, req: ResolveRequest) => postJson<ResolveResponse>(`/jobs/${encodeURIComponent(id)}/resolve`, req),

  getLinkedInStatus: (check = false) => request<LinkedInStatusResponse>(`/linkedin/status?check=${check}`),

  // Scheduled posts (D46). Reading never changes anything; the others take the run lock (409 while busy).
  getSchedules: (all = false, platform?: string) => {
    const query = new URLSearchParams();
    if (all) query.set('all', 'true');
    if (platform) query.set('platform', platform);
    return request<ScheduleListResponse>(query.size ? `/schedules?${query}` : '/schedules');
  },

  createSchedule: (req: ScheduleCreateRequest) => postJson<ScheduleResponse>('/schedules', req),

  cancelSchedule: (id: string) => postJson<ScheduleResponse>(`/schedules/${encodeURIComponent(id)}/cancel`, {}),

  reconfirmSchedule: (id: string, req: ScheduleReconfirmRequest) =>
    postJson<ScheduleResponse>(`/schedules/${encodeURIComponent(id)}/reconfirm`, req)
};
