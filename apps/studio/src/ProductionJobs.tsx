import {
  Activity,
  CircleCheck,
  CircleDashed,
  Clock3,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { productionJobs, productionJobsHistory } from "./api";
import { RECEIPT_RECHECK_MS, shouldAutoRecheckReceipts } from "./jobRefresh";
import type {
  ProductionJobProjection,
  ProductionJobsHistoryCursor,
  ProductionJobsHistoryPage,
  ProductionJobState,
  Project,
} from "./types";

const stateLabels: Record<ProductionJobState, string> = {
  queued: "QUEUED",
  running: "RUNNING",
  cancel_requested: "CANCEL REQUESTED",
  succeeded: "SUCCEEDED",
  failed: "FAILED",
  cancelled: "CANCELLED",
  outcome_unknown: "OUTCOME UNKNOWN",
};

function shortRef(value: string) {
  return value.length <= 24 ? value : value.slice(0, 21) + "…";
}

function providerLabel(provider: string) {
  if (provider === "driver:motion-canvas") return "Motion Canvas";
  if (provider === "driver:mlt-video") return "MLT";
  return provider.replace(/^driver:/, "");
}

function observedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function observationLabel(job: ProductionJobProjection) {
  if (job.last_observation === "failed_known") return "Last query failed";
  if (job.last_observation === "outcome_unknown") return "Last outcome unknown";
  return "Observed";
}

function StateMark({ state }: { state: ProductionJobState }) {
  if (state === "succeeded") return <CircleCheck size={14} aria-hidden="true" />;
  if (state === "failed" || state === "outcome_unknown") {
    return <TriangleAlert size={14} aria-hidden="true" />;
  }
  if (state === "running" || state === "cancel_requested") {
    return <RefreshCw size={14} aria-hidden="true" />;
  }
  if (state === "queued") return <Clock3 size={14} aria-hidden="true" />;
  return <CircleDashed size={14} aria-hidden="true" />;
}

function JobRow({ job }: { job: ProductionJobProjection }) {
  const progress = job.progress
    ? job.progress.total == null
      ? String(job.progress.completed)
      : job.progress.completed + " / " + job.progress.total
    : null;

  return (
    <div className="jobs-ledger-row" role="row">
      <div className="job-ref-cell" role="cell">
        <strong className="mono" title={job.job_ref}>{shortRef(job.job_ref)}</strong>
        <span>
          {job.local_observations} observation{job.local_observations === 1 ? "" : "s"}
          {job.provider_generation == null ? "" : " · provider g" + job.provider_generation}
        </span>
      </div>
      <div role="cell">
        <strong>{providerLabel(job.provider)}</strong>
        <span className="mono">{job.provider}</span>
      </div>
      <div role="cell">
        <span className={"job-state job-state-" + job.state}>
          <StateMark state={job.state} />
          {stateLabels[job.state]}
        </span>
        {job.cancellation_requested && job.state !== "cancelled" && (
          <span className="job-secondary-state">Cancellation is not confirmed</span>
        )}
      </div>
      <div role="cell">
        <strong className="mono">r{job.revision}</strong>
        <span className="mono" title={job.generation}>{job.generation.slice(0, 8)}…</span>
      </div>
      <div role="cell">
        <span className={"job-applicability job-applicability-" + job.applicability}>
          {job.applicability.toUpperCase()}
        </span>
        <span>{job.applicability === "current" ? "Applies to open revision" : "Historical result"}</span>
      </div>
      <div className="job-observation-cell" role="cell">
        <strong>{observationLabel(job)}</strong>
        <span>{observedAt(job.last_observed_at)}</span>
        <span>
          {progress == null ? "No canonical progress reported" : "Progress " + progress}
          {job.artifact_available ? " · artifact recorded" : ""}
          {job.result_available ? " · result recorded" : ""}
        </span>
      </div>
    </div>
  );
}

export default function ProductionJobsWorkspace({
  project,
  desktopMode,
}: {
  project: Project;
  desktopMode: boolean;
}) {
  const [jobs, setJobs] = useState<ProductionJobProjection[]>([]);
  const [loading, setLoading] = useState(desktopMode);
  const [refreshing, setRefreshing] = useState(false);
  const [lastReadAt, setLastReadAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refreshRef = useRef<() => void>(() => {});
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historicalJobs, setHistoricalJobs] = useState<ProductionJobProjection[]>([]);
  const [historicalCursor, setHistoricalCursor] = useState<ProductionJobsHistoryCursor | null>(null);
  const [historicalComplete, setHistoricalComplete] = useState(false);
  const [historicalTotal, setHistoricalTotal] = useState(0);
  const [historicalReadAt, setHistoricalReadAt] = useState<string | null>(null);
  const [historicalLoading, setHistoricalLoading] = useState(false);
  const [historicalError, setHistoricalError] = useState<string | null>(null);
  const historyEpochRef = useRef(0);
  const historyKey = [project.id, project.generation, project.revision].join(":");
  const historyKeyRef = useRef(historyKey);
  historyKeyRef.current = historyKey;
  const historicalInFlight = useRef<{ key: string; epoch: number } | null>(null);
  // Survives React Strict Mode's effect cleanup/setup; it never caches a settled read.
  const pendingReadRef = useRef<{
    key: string;
    promise: Promise<ProductionJobProjection[]>;
  } | null>(null);

  useEffect(() => {
    let disposed = false;
    let inFlight = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let latestJobs: ProductionJobProjection[] = [];
    const requestKey = JSON.stringify([project.id, project.generation, project.revision]);

    function readOnce(): Promise<ProductionJobProjection[]> {
      const pending = pendingReadRef.current;
      if (pending?.key === requestKey) return pending.promise;
      const promise = productionJobs(project);
      pendingReadRef.current = { key: requestKey, promise };
      const release = () => {
        if (pendingReadRef.current?.promise === promise) pendingReadRef.current = null;
      };
      // Observe both settlements without creating an unhandled rejection.
      void promise.then(release, release);
      return promise;
    }

    function clearTimer() {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
    }

    function scheduleRead() {
      clearTimer();
      if (!disposed && shouldAutoRecheckReceipts(
        desktopMode, document.visibilityState === "visible", latestJobs,
      )) {
        timer = window.setTimeout(() => {
          timer = null;
          void readReceipts(false);
        }, RECEIPT_RECHECK_MS);
      }
    }

    async function readReceipts(initial: boolean) {
      if (disposed || inFlight) return;
      clearTimer();
      inFlight = true;
      if (!initial) setRefreshing(true);
      let succeeded = false;
      try {
        const next = await readOnce();
        if (disposed) return;
        latestJobs = next;
        setJobs(next);
        setLastReadAt(new Date().toISOString());
        setError(null);
        succeeded = true;
      } catch (reason) {
        if (!disposed) setError(reason instanceof Error ? reason.message : String(reason));
        // Preserve the last successful snapshot, but stop automatic rereads on failure.
      } finally {
        inFlight = false;
        if (!disposed) {
          setLoading(false);
          setRefreshing(false);
          if (succeeded) scheduleRead();
        }
      }
    }

    function onVisibilityChange() {
      if (document.visibilityState !== "visible") {
        clearTimer();
      } else if (shouldAutoRecheckReceipts(desktopMode, true, latestJobs)) {
        void readReceipts(false);
      }
    }

    // Invalidate rows from the previous project or revision before the new read.
    historyEpochRef.current += 1;
    setHistoryOpen(false);
    setHistoricalJobs([]);
    setHistoricalCursor(null);
    setHistoricalComplete(false);
    setHistoricalTotal(0);
    setHistoricalReadAt(null);
    setHistoricalLoading(false);
    setHistoricalError(null);
    setJobs([]);
    setLastReadAt(null);
    setError(null);
    setLoading(desktopMode);
    setRefreshing(false);
    refreshRef.current = () => { void readReceipts(false); };
    document.addEventListener("visibilitychange", onVisibilityChange);
    void readReceipts(true);

    return () => {
      disposed = true;
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibilityChange);
      refreshRef.current = () => {};
    };
  }, [project.id, project.generation, project.revision, desktopMode]);

  // A separate on-demand history view never triggers polling or driver
  // commands. Pages are stable only while both creative revision and the
  // independent production receipt watermark remain unchanged.
  async function readHistory(
    cursor: ProductionJobsHistoryCursor | null,
    epoch: number,
  ) {
    const existing = historicalInFlight.current;
    if (existing?.key === historyKey && existing.epoch === epoch) return;
    const token = { key: historyKey, epoch };
    historicalInFlight.current = token;
    setHistoricalLoading(true);
    setHistoricalError(null);
    try {
      const page: ProductionJobsHistoryPage = await productionJobsHistory(project, 16, cursor);
      if (historyKeyRef.current !== token.key || historyEpochRef.current !== epoch) return;
      if (cursor === null) {
        setHistoricalJobs(page.items);
        setHistoricalReadAt(new Date().toISOString());
      } else {
        // Refuse duplicate pages without mutating the canonical source.
        setHistoricalJobs((current) => {
          const seen = new Set(current.map((entry) => entry.job_ref));
          return [...current, ...page.items.filter((entry) => !seen.has(entry.job_ref))];
        });
      }
      setHistoricalCursor(page.next);
      setHistoricalComplete(page.complete);
      setHistoricalTotal(page.total_jobs);
    } catch (reason) {
      if (historyKeyRef.current === token.key && historyEpochRef.current === epoch) {
        setHistoricalError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (historicalInFlight.current === token) historicalInFlight.current = null;
      if (historyKeyRef.current === token.key && historyEpochRef.current === epoch) {
        setHistoricalLoading(false);
      }
    }
  }

  function openHistory() {
    if (historyOpen) {
      historyEpochRef.current += 1;
      setHistoryOpen(false);
      return;
    }
    setHistoryOpen(true);
    restartHistory();
  }

  function restartHistory() {
    const epoch = ++historyEpochRef.current;
    setHistoricalJobs([]);
    setHistoricalCursor(null);
    setHistoricalComplete(false);
    setHistoricalTotal(0);
    setHistoricalReadAt(null);
    void readHistory(null, epoch);
  }

  const visibleJobs = historyOpen ? historicalJobs : jobs;
  const waitingForFirstPage = historyOpen ? historicalLoading && historicalJobs.length === 0 : loading;

  return (
    <section className="jobs-workspace" aria-label="Production jobs">
      <header className="workspace-heading jobs-heading">
        <div>
          <h2>Production jobs</h2>
          <p>
            Durable correlation projected from Motionwright receipts. Semwright remains the
            execution, cancellation and runtime-state authority.
          </p>
        </div>
        <div className="jobs-heading-actions">
          <span className="count-label" aria-live="polite">
            {historyOpen
              ? historicalLoading && historicalJobs.length === 0
                ? "Reading history…"
                : historicalJobs.length + " / " + historicalTotal + " historical jobs"
              : loading ? "Reading receipts…" : jobs.length + (jobs.length === 1 ? " job" : " jobs")}
          </span>
          <span className="jobs-read-at">
            {lastReadAt ? "Local receipts read " + observedAt(lastReadAt) : "No successful receipt read yet"}
          </span>
          <button
            type="button"
            className="button compact"
            aria-label={historyOpen ? "Back to recent jobs" : "Browse full production history"}
            disabled={!desktopMode}
            title={historyOpen
              ? "Return to the lightweight latest-window job projection."
              : "On-demand, locally persisted historical jobs at a fixed receipt watermark."}
            onClick={openHistory}
          >
            {historyOpen ? "Recent jobs" : "Full history"}
          </button>
          <button
            type="button"
            className="button compact"
            aria-label="Refresh production receipts"
            disabled={!desktopMode || loading || refreshing}
            title={desktopMode
              ? "Read persisted Motionwright receipts only; this does not query the driver."
              : "Native receipt storage requires the desktop runtime."}
            onClick={() => refreshRef.current()}
          >
            <RefreshCw size={13} aria-hidden="true" />
            {refreshing ? "Reading…" : "Refresh receipts"}
          </button>
        </div>
      </header>

      <div className="jobs-authority-strip">
        <Activity size={15} aria-hidden="true" />
        <div>
          <strong>Execution state and result applicability are separate.</strong>
          <span>
            A late successful render can be STALE. A cancellation request stays unconfirmed until
            the canonical driver reports CANCELLED. This panel rereads local receipts while active
            jobs are visible; it never queries or advances the native job itself.
          </span>
        </div>
      </div>

      {historyOpen && (
        <div className="jobs-history-banner" role="region" aria-label="Historical production snapshot">
          <div>
            <strong>Read-only historical snapshot</strong>
            <span>All locally persisted job receipts as of one verified watermark.
              New status receipts require restarting the snapshot; no driver is queried.</span>
            {historicalReadAt && <span>Snapshot read {observedAt(historicalReadAt)}</span>}
          </div>
          <button type="button" className="button compact"
            disabled={historicalLoading}
            onClick={restartHistory}>Restart history</button>
        </div>
      )}
      {historyOpen && historicalError && (
        <div className="jobs-inline-error" role="alert">
          <TriangleAlert size={15} aria-hidden="true" />
          <div>
            <strong>Historical receipt snapshot could not be continued.</strong>
            <span>{historicalError}</span>
            <span>Existing rows belong to the earlier snapshot; restart to check the latest receipts.</span>
          </div>
        </div>
      )}

      {!historyOpen && error && (
        <div className="jobs-inline-error" role="alert">
          <TriangleAlert size={15} aria-hidden="true" />
          <div>
            <strong>Job receipts could not be read.</strong>
            <span>{error}</span>
            {lastReadAt && <span>Last successful local receipt snapshot is still shown; it may be out of date.</span>}
          </div>
        </div>
      )}

      {!waitingForFirstPage && visibleJobs.length === 0 ? (
        <div className="jobs-empty">
          <CircleDashed size={20} aria-hidden="true" />
          <strong>{historyOpen ? "No historical production jobs in this snapshot." :
            "No canonical render job receipts yet."}</strong>
          <span>
            {historyOpen
              ? historicalError
                ? "The history read failed; restart to validate the receipt stream again."
                : "All persisted observations are checked without invoking the native driver."
              : error
                ? "Receipt read failed. Use Refresh receipts to try again; no driver action was dispatched."
                : desktopMode
                  ? "Start production through the Semwright-backed production boundary to create traceable job evidence."
                  : "Browser demo mode intentionally does not fabricate runtime jobs or render evidence."}
          </span>
        </div>
      ) : (
        <div className="jobs-ledger-scroll">
          <div className="jobs-ledger" role="table"
            aria-label={historyOpen ? "Historical production job receipts" : "Canonical production job receipts"}>
            <div className="jobs-ledger-row jobs-ledger-head" role="row">
              <span role="columnheader">Job</span>
              <span role="columnheader">Provider</span>
              <span role="columnheader">Execution</span>
              <span role="columnheader">Base</span>
              <span role="columnheader">Applicability</span>
              <span role="columnheader">Last observation</span>
            </div>
            {visibleJobs.map((job) => <JobRow key={job.job_ref} job={job} />)}
          </div>
        </div>
      )}

      {historyOpen && (
        <div className="jobs-history-pager">
          <span className="mono">{historicalJobs.length} of {historicalTotal} persisted jobs
            {historicalComplete ? " · complete at verified snapshot" : " · more may remain"}</span>
          <button type="button" className="button compact"
            aria-label="Load older production jobs"
            disabled={historicalLoading || historicalError !== null || historicalCursor === null}
            onClick={() => {
              if (historicalCursor) void readHistory(historicalCursor, historyEpochRef.current);
            }}>
            {historicalLoading ? "Reading…" : "Load older jobs"}
          </button>
        </div>
      )}
      <footer className="jobs-footnote">
        Local observations are derived from immutable request receipts; they do not mint Project
        Graph admission, Effect Conformance PASS, creative approval, or new runtime authority.
      </footer>
    </section>
  );
}
