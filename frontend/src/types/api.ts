// Shapes follow docs/api-plan.md. Optional fields are shown when the API sends them.

// The backend's job statuses (CLAUDE.md, "Job states"). There is no "SUCCESS":
// a real confirmed post is PUBLISHED, a simulated one is SIMULATED.
export type JobStatus = 'PUBLISHING' | 'PUBLISHED' | 'SIMULATED' | 'FAILED' | 'NEEDS_REVIEW';
export type JobMode = 'simulated' | 'real';

export interface HealthResponse {
  status: string;
  simulated_by_default: boolean;
  linkedin_version: string;
}

export interface Candidate {
  name: string;
  stable: boolean;
  valid: boolean;
  errors: string[];
  duplicate_of: string | null;
  only_simulated: boolean;
  // Optional display extras.
  image_filename?: string;
  caption_filename?: string;
  file_size_bytes?: number;
}

// The backend's ScanResult holds pending files and ambiguous stem groups as
// lists; accept a plain count too until the API shape is fixed.
export type CountOrList = number | unknown[];

export interface ScanResponse {
  candidates: Candidate[];
  pending: CountOrList;
  ambiguous: CountOrList;
  warnings: string[];
}

export interface RunRequest {
  real: boolean;
  publish_simulated: boolean;
  confirm: string | null;
}

export interface RunJobResult {
  job_id: string;
  stem: string;
  mode: JobMode;
  status: JobStatus;
  post_id?: string | null;
  destination?: string | null;
  errors?: string[];
}

export interface RunReport {
  exit_code: number;
  mode: JobMode;
  resumed: number | unknown[];
  recovered: number | unknown[];
  jobs: RunJobResult[];
  pending: CountOrList;
  ambiguous: CountOrList;
  refused?: string | null;
}

export interface JobSummary {
  job_id: string;
  created_at: string;
  stem: string;
  mode: JobMode;
  status: JobStatus;
  errors?: string[] | null;
  post_id?: string | null;
  destination?: string | null;
}

// GET /api/jobs/{id}: every column of the jobs table. Caption text is never
// stored in the job database, so it is not part of a job.
export interface JobDetail extends JobSummary {
  platform?: string;
  media_filename?: string;
  caption_filename?: string;
  media_sha256?: string | null;
  caption_sha256?: string | null;
  updated_at?: string;
}

// Exactly one of the two, as in `resolve <id> --published <post_id> | --failed`.
export type ResolveRequest = { published_post_id: string } | { failed: true };

export interface ResolveResponse {
  job_id: string;
  status: JobStatus;
  note: string;
  files_at: string;
}

export type LinkedInTokenState = 'valid' | 'expiring' | 'expired' | 'missing' | 'unknown';

export interface LinkedInStatusResponse {
  token_set: boolean;
  state: LinkedInTokenState;
  expires_on: string | null;
  days_left: number | null;
  // Only after check=true: who posts would be published as.
  member?: { name: string; urn: string } | null;
}

export interface PostCreatePayload {
  name: string;
  image: File;
  caption: string;
}

export interface PostCreateResponse {
  name: string;
  files: { image: string; caption: string };
  duplicate_of?: string | null;
}

export interface ApiError {
  status: number;
  message: string;
}

export const countOf = (value: CountOrList | undefined | null): number =>
  Array.isArray(value) ? value.length : value ?? 0;

export const isApiError = (err: unknown): err is ApiError =>
  typeof err === 'object' && err !== null && 'status' in err && 'message' in err;

export const errorMessage = (err: unknown, fallback: string): string =>
  isApiError(err) ? err.message : err instanceof Error ? err.message : fallback;

// A candidate the next run would process.
export const isReady = (c: Candidate): boolean => c.stable && c.valid && !c.duplicate_of;
