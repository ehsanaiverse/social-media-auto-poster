import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type {
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
import { errorMessage, isApiError } from '../types/api';
import { apiService } from '../services/apiService';

export interface ToastMessage {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  title: string;
  message?: string;
}

export type Connection = 'checking' | 'online' | 'offline';

interface ApiContextType {
  connection: Connection;
  retryConnection: () => Promise<void>;

  scanData: ScanResponse | null;
  scanReal: boolean;
  isScanning: boolean;
  refreshScan: (real?: boolean) => Promise<void>;

  jobs: JobSummary[];
  isLoadingJobs: boolean;
  jobStatusFilter: string;
  setJobStatusFilter: (status: string) => void;
  refreshJobs: () => Promise<void>;

  selectedJob: JobDetail | null;
  openJob: (idOrPrefix: string) => Promise<void>;
  closeJob: () => void;

  isRunning: boolean;
  lastRunReport: RunReport | null;
  executeRun: (req: RunRequest) => Promise<RunReport | null>;

  isCreatingPost: boolean;
  createPost: (payload: PostCreatePayload) => Promise<PostCreateResponse | null>;

  resolvingJob: JobSummary | null;
  setResolvingJob: (job: JobSummary | null) => void;
  resolveJob: (jobId: string, req: ResolveRequest) => Promise<ResolveResponse | null>;

  linkedInStatus: LinkedInStatusResponse | null;
  isCheckingLinkedIn: boolean;
  refreshLinkedInStatus: (check?: boolean) => Promise<void>;

  toasts: ToastMessage[];
  addToast: (type: ToastMessage['type'], title: string, message?: string) => void;
  removeToast: (id: string) => void;
}

const ApiContext = createContext<ApiContextType | null>(null);

const STATUS_TITLES: Record<number, string> = {
  0: "Can't connect",
  409: 'Busy right now',
  412: 'Nothing was posted',
  422: 'Not possible'
};

// While the API is down, look for it again this often.
const RETRY_MS = 10_000;

export const ApiProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [connection, setConnection] = useState<Connection>('checking');

  const [scanData, setScanData] = useState<ScanResponse | null>(null);
  // The page only posts for real, so the queue is always checked against real history.
  const [scanReal, setScanReal] = useState(true);
  const [isScanning, setIsScanning] = useState(false);

  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [isLoadingJobs, setIsLoadingJobs] = useState(false);
  const [jobStatusFilter, setJobStatusFilter] = useState('ALL');
  const [selectedJob, setSelectedJob] = useState<JobDetail | null>(null);

  const [isRunning, setIsRunning] = useState(false);
  const [lastRunReport, setLastRunReport] = useState<RunReport | null>(null);

  const [isCreatingPost, setIsCreatingPost] = useState(false);
  const [resolvingJob, setResolvingJob] = useState<JobSummary | null>(null);

  const [linkedInStatus, setLinkedInStatus] = useState<LinkedInStatusResponse | null>(null);
  const [isCheckingLinkedIn, setIsCheckingLinkedIn] = useState(false);

  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const filterRef = useRef(jobStatusFilter);
  filterRef.current = jobStatusFilter;
  const scanRealRef = useRef(scanReal);
  scanRealRef.current = scanReal;

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addToast = useCallback(
    (type: ToastMessage['type'], title: string, message?: string) => {
      const id = crypto.randomUUID();
      setToasts((prev) => [...prev.slice(-3), { id, type, title, message }]);
      setTimeout(() => removeToast(id), type === 'error' ? 8000 : 5000);
    },
    [removeToast]
  );

  // A failed call with status 0 means the API went away.
  const noteError = useCallback((err: unknown) => {
    if (isApiError(err) && err.status === 0) setConnection('offline');
  }, []);

  const refreshScan = useCallback(
    async (real?: boolean) => {
      const useReal = real ?? scanRealRef.current;
      setScanReal(useReal);
      setIsScanning(true);
      try {
        setScanData(await apiService.scan(useReal));
      } catch (err) {
        noteError(err);
        setScanData(null);
        if (!isApiError(err) || err.status !== 0) addToast('error', "Couldn't load the queue", errorMessage(err, 'Please try again.'));
      } finally {
        setIsScanning(false);
      }
    },
    [addToast, noteError]
  );

  const refreshJobs = useCallback(async () => {
    setIsLoadingJobs(true);
    try {
      setJobs(await apiService.getJobs(30, filterRef.current));
    } catch (err) {
      noteError(err);
      setJobs([]);
      if (!isApiError(err) || err.status !== 0) addToast('error', "Couldn't load the history", errorMessage(err, 'Please try again.'));
    } finally {
      setIsLoadingJobs(false);
    }
  }, [addToast, noteError]);

  const refreshLinkedInStatus = useCallback(
    async (check = false) => {
      setIsCheckingLinkedIn(true);
      try {
        const status = await apiService.getLinkedInStatus(check);
        setLinkedInStatus(status);
        if (check) {
          const ok = status.state === 'valid' || status.state === 'expiring';
          addToast(ok ? 'success' : 'warning', ok ? 'LinkedIn is connected' : 'LinkedIn needs attention', status.member ? `Posting as ${status.member.name}.` : undefined);
        }
      } catch (err) {
        noteError(err);
        if (check || !isApiError(err) || err.status !== 0) addToast('error', "Couldn't check LinkedIn", errorMessage(err, 'Please try again.'));
      } finally {
        setIsCheckingLinkedIn(false);
      }
    },
    [addToast, noteError]
  );

  const retryConnection = useCallback(async () => {
    setConnection('checking');
    if (await apiService.ping()) {
      setConnection('online');
      await Promise.all([refreshScan(), refreshJobs(), refreshLinkedInStatus()]);
    } else {
      setConnection('offline');
      setScanData(null);
      setJobs([]);
      setLinkedInStatus(null);
    }
  }, [refreshScan, refreshJobs, refreshLinkedInStatus]);

  // First load.
  useEffect(() => {
    void retryConnection();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Losing the connection clears what was loaded, so the page never shows stale numbers.
  useEffect(() => {
    if (connection !== 'offline') return;
    setScanData(null);
    setJobs([]);
    setLinkedInStatus(null);
  }, [connection]);

  // While offline, keep looking for the API so the page fills in on its own.
  useEffect(() => {
    if (connection !== 'offline') return;
    const id = setInterval(async () => {
      if (await apiService.ping()) void retryConnection();
    }, RETRY_MS);
    return () => clearInterval(id);
  }, [connection, retryConnection]);

  // A filter change reloads only the job list.
  const firstFilter = useRef(true);
  useEffect(() => {
    if (firstFilter.current) {
      firstFilter.current = false;
      return;
    }
    if (connection === 'online') void refreshJobs();
  }, [jobStatusFilter, refreshJobs, connection]);

  const createPost = useCallback(
    async (payload: PostCreatePayload) => {
      setIsCreatingPost(true);
      try {
        const res = await apiService.addPost(payload);
        addToast(
          res.duplicate_of ? 'warning' : 'success',
          `Added '${res.name}' to your queue`,
          res.duplicate_of ? 'It matches an earlier post, so it will be skipped.' : 'Publish it whenever you are ready.'
        );
        await refreshScan();
        return res;
      } catch (err) {
        noteError(err);
        addToast('error', 'Post not added', errorMessage(err, 'Something went wrong. Please try again.'));
        return null;
      } finally {
        setIsCreatingPost(false);
      }
    },
    [addToast, noteError, refreshScan]
  );

  const executeRun = useCallback(
    async (req: RunRequest) => {
      setIsRunning(true);
      try {
        const report = await apiService.run(req);
        setLastRunReport(report);
        const published = report.jobs.filter((j) => j.status === 'PUBLISHED').length;
        addToast(
          report.exit_code === 0 ? 'success' : 'warning',
          report.exit_code === 0 ? 'All done' : 'Done, with something to check',
          report.mode === 'real' ? `${published} post(s) published.` : 'Test finished. Nothing was posted.'
        );
        await Promise.all([refreshJobs(), refreshScan()]);
        return report;
      } catch (err) {
        noteError(err);
        const status = isApiError(err) ? err.status : -1;
        addToast('error', STATUS_TITLES[status] ?? 'Something went wrong', errorMessage(err, 'Please try again.'));
        return null;
      } finally {
        setIsRunning(false);
      }
    },
    [addToast, noteError, refreshJobs, refreshScan]
  );

  const openJob = useCallback(
    async (idOrPrefix: string) => {
      try {
        setSelectedJob(await apiService.getJob(idOrPrefix));
      } catch (err) {
        noteError(err);
        addToast('error', 'Post not found', errorMessage(err, 'Please try again.'));
      }
    },
    [addToast, noteError]
  );

  const closeJob = useCallback(() => setSelectedJob(null), []);

  const resolveJob = useCallback(
    async (jobId: string, req: ResolveRequest) => {
      try {
        const res = await apiService.resolveJob(jobId, req);
        addToast('success', 'Saved', res.status === 'PUBLISHED' ? 'Marked as posted.' : 'Marked as not posted. You can post it again.');
        setResolvingJob(null);
        await Promise.all([refreshJobs(), refreshScan()]);
        return res;
      } catch (err) {
        noteError(err);
        addToast('error', 'Not saved', errorMessage(err, 'Please try again.'));
        return null;
      }
    },
    [addToast, noteError, refreshJobs, refreshScan]
  );

  const value = useMemo<ApiContextType>(
    () => ({
      connection,
      retryConnection,
      scanData,
      scanReal,
      isScanning,
      refreshScan,
      jobs,
      isLoadingJobs,
      jobStatusFilter,
      setJobStatusFilter,
      refreshJobs,
      selectedJob,
      openJob,
      closeJob,
      isRunning,
      lastRunReport,
      executeRun,
      isCreatingPost,
      createPost,
      resolvingJob,
      setResolvingJob,
      resolveJob,
      linkedInStatus,
      isCheckingLinkedIn,
      refreshLinkedInStatus,
      toasts,
      addToast,
      removeToast
    }),
    [
      connection, retryConnection, scanData, scanReal, isScanning, refreshScan, jobs, isLoadingJobs, jobStatusFilter,
      refreshJobs, selectedJob, openJob, closeJob, isRunning, lastRunReport, executeRun, isCreatingPost, createPost,
      resolvingJob, resolveJob, linkedInStatus, isCheckingLinkedIn, refreshLinkedInStatus, toasts, addToast, removeToast
    ]
  );

  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
};

export const useApi = () => {
  const context = useContext(ApiContext);
  if (!context) throw new Error('useApi must be used within an ApiProvider');
  return context;
};
