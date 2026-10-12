/** v0.1 read-only owner recovery classification for locally projected jobs.
 * This is never a canonical provider status observation or a retry grant.
 */
import type { ProductionJobProjection, Project } from "./types";

export interface NativeRecoveryWarning {
  code: "ORIGINAL_JOB_OUTCOME_UNKNOWN" | "CANCELLATION_NOT_CONFIRMED";
  affectedJobs: number;
  requiresOwnerReconciliation: true;
  mayAutomaticallyRetry: false;
  providerTerminalConfirmed: false;
}

export function nativeRecoveryWarning(
  project: Project,
  locallyObserved: ProductionJobProjection[],
): NativeRecoveryWarning | null {
  // History is source data, not an authorization token. Reject all other
  // projects, revisions and providers when advising on the current project.
  const relevant = locallyObserved.filter(job =>
    (job.provider === "driver:motion-canvas" || job.provider === "driver:mlt-video")
    && job.generation === project.generation
    && job.revision === project.revision
    && job.applicability === "current"
  );
  const unknown = relevant.filter(job => job.state === "outcome_unknown");
  if (unknown.length) {
    return {
      code: "ORIGINAL_JOB_OUTCOME_UNKNOWN", affectedJobs: unknown.length,
      requiresOwnerReconciliation: true,
      mayAutomaticallyRetry: false,
      providerTerminalConfirmed: false,
    };
  }
  const cancelling = relevant.filter(job =>
    job.state === "cancel_requested"
      || (job.cancellation_requested && (job.state === "queued" || job.state === "running")));
  if (cancelling.length) {
    return {
      code: "CANCELLATION_NOT_CONFIRMED", affectedJobs: cancelling.length,
      requiresOwnerReconciliation: true,
      mayAutomaticallyRetry: false,
      providerTerminalConfirmed: false,
    };
  }
  return null;
}
