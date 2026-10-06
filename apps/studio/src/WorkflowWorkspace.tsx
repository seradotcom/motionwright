import {
  Activity,
  Braces,
  Check,
  CircleDashed,
  GitBranch,
  Play,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Square,
  TriangleAlert,
  Workflow,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { workflowAction, workflowOverview } from "./api";
import type {
  Project,
  WorkflowAction as WorkflowActionName,
  WorkflowActionResult,
  WorkflowOverview,
} from "./types";

type Row = Record<string, unknown>;

function rows(section: unknown, key: string): Row[] {
  if (!section || typeof section !== "object" || Array.isArray(section)) return [];
  const value = (section as Row)[key];
  return Array.isArray(value)
    ? value.filter((item): item is Row => Boolean(item) && typeof item === "object")
    : [];
}

function text(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return null;
}

function short(value: string, length = 34) {
  return value.length <= length ? value : value.slice(0, length - 1) + "…";
}

function titleFor(row: Row) {
  return (
    text(row.suggested_name) ??
    text(row.name) ??
    text(row.slug) ??
    text(row.capability) ??
    text(row.id) ??
    "Workflow evidence"
  );
}

function identityFor(row: Row, fallback: string) {
  return (
    text(row.id) ??
    text(row.suggestion_id) ??
    text(row.proposal_id) ??
    text(row.pattern_id) ??
    text(row.slug) ??
    fallback
  );
}

function detailFor(row: Row) {
  const parts: string[] = [];
  const occurrences = text(row.occurrences);
  const sourceTraceCount = text(row.source_trace_count);
  const successfulReplays = text(row.successful_replays);
  const status = text(row.status);
  const tier =
    row.evidence && typeof row.evidence === "object"
      ? text((row.evidence as Row).tier)
      : null;

  if (occurrences) parts.push(occurrences + " occurrences");
  if (sourceTraceCount) parts.push(sourceTraceCount + " source traces");
  if (successfulReplays) parts.push(successfulReplays + " successful replays");
  if (tier) parts.push(tier + " evidence");
  if (status) parts.push(status.replaceAll("_", " "));
  return parts.join(" · ") || "Canonical Semwright evidence";
}

function slugify(value: string) {
  const slug = value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  return /^[a-z][a-z0-9-]{0,38}[a-z0-9]$/.test(slug) ? slug : "";
}

function recordingStorageKey(projectId: string) {
  return "motionwright.workflow.recording." + projectId;
}

function storedRecording(projectId: string) {
  if (typeof sessionStorage === "undefined") return false;
  try {
    return sessionStorage.getItem(recordingStorageKey(projectId)) === "active";
  } catch {
    return false;
  }
}

function WorkflowList({
  label,
  icon,
  items,
  empty,
  actions,
}: {
  label: string;
  icon: ReactNode;
  items: Row[];
  empty: string;
  actions?: (item: Row) => ReactNode;
}) {
  return (
    <section className="workflow-panel">
      <header>
        <span className="section-label">{label}</span>
        <span className="count-label">{items.length}</span>
      </header>
      {items.length === 0 ? (
        <div className="workflow-panel-empty">
          <CircleDashed size={16} aria-hidden="true" />
          <span>{empty}</span>
        </div>
      ) : (
        <div className="workflow-list">
          {items.slice(0, 12).map((item, index) => {
            const identity = identityFor(item, label + "-" + index);
            return (
              <article className="workflow-row" key={identity}>
                <span className="workflow-row-icon" aria-hidden="true">
                  {icon}
                </span>
                <div className="workflow-row-copy">
                  <strong title={titleFor(item)}>{short(titleFor(item), 52)}</strong>
                  <span>{detailFor(item)}</span>
                  {actions ? <div className="workflow-row-actions">{actions(item)}</div> : null}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

export default function WorkflowWorkspace({ project }: { project: Project }) {
  const [overview, setOverview] = useState<WorkflowOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<WorkflowActionResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState<WorkflowActionName | null>(null);
  const [writesEnabled, setWritesEnabled] = useState(false);
  const [recording, setRecording] = useState(() => storedRecording(project.id));
  const [recordName, setRecordName] = useState("Motionwright routine");
  const [recordIntent, setRecordIntent] = useState("");
  const [captureValues, setCaptureValues] = useState(false);
  const [selectedTraceIds, setSelectedTraceIds] = useState<string[]>([]);
  const [compileName, setCompileName] = useState("");
  const [compileDescription, setCompileDescription] = useState("");
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
  const [replayInputs, setReplayInputs] = useState("{}");
  const [promotionSlug, setPromotionSlug] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    workflowOverview(project)
      .then((next) => {
        if (active) setOverview(next);
      })
      .catch((reason) => {
        if (active) {
          setOverview(null);
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [project.id, project.generation, project.revision, refresh]);

  useEffect(() => {
    setRecording(storedRecording(project.id));
    setWritesEnabled(false);
    setSelectedTraceIds([]);
    setSelectedCandidateId(null);
    setReceipt(null);
    setActionError(null);
  }, [project.id]);

  const data = useMemo(() => {
    if (!overview) {
      return {
        traces: [] as Row[],
        candidates: [] as Row[],
        patterns: [] as Row[],
        suggestions: [] as Row[],
        proposals: [] as Row[],
        promotions: [] as Row[],
      };
    }
    return {
      traces: rows(overview.traces, "traces"),
      candidates: rows(overview.candidates, "candidates"),
      patterns: rows(overview.patterns, "patterns"),
      suggestions: rows(overview.suggestions, "suggestions"),
      proposals: rows(overview.proposals, "proposals"),
      promotions: rows(overview.promotions, "promotions"),
    };
  }, [overview]);

  const selectedCandidate = useMemo(
    () =>
      data.candidates.find((candidate, index) => {
        const id = identityFor(candidate, "candidate-" + index);
        return id === selectedCandidateId;
      }) ?? null,
    [data.candidates, selectedCandidateId],
  );

  const available = overview?.status === "available";
  const statusLabel =
    overview?.status === "available"
      ? "CANONICAL"
      : overview?.status === "unconfigured"
        ? "NOT CONNECTED"
        : overview?.status === "browser_demo"
          ? "BROWSER DEMO"
          : "READING";

  async function runAction(
    action: WorkflowActionName,
    args: Record<string, unknown>,
    after?: () => void,
  ) {
    if (!available) return;
    setBusy(action);
    setActionError(null);
    try {
      const next = await workflowAction(project, action, args);
      setReceipt(next);
      after?.();
      setRefresh((value) => value + 1);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  function markRecording(active: boolean) {
    setRecording(active);
    if (typeof sessionStorage === "undefined") return;
    try {
      if (active) {
        sessionStorage.setItem(recordingStorageKey(project.id), "active");
      } else {
        sessionStorage.removeItem(recordingStorageKey(project.id));
      }
    } catch {
      // Session storage is only a UI hint. Semwright remains authoritative.
    }
  }

  function toggleTrace(traceId: string, checked: boolean) {
    setSelectedTraceIds((current) => {
      if (!checked) return current.filter((id) => id !== traceId);
      if (current.includes(traceId) || current.length >= 8) return current;
      return [...current, traceId];
    });
  }

  function selectCandidate(candidate: Row, index: number) {
    const id = identityFor(candidate, "candidate-" + index);
    setSelectedCandidateId(id);
    setReplayInputs("{}");
    setPromotionSlug(slugify(titleFor(candidate)));
    setActionError(null);
  }

  function replaySelected() {
    if (!selectedCandidateId) return;
    let inputs: unknown;
    try {
      inputs = JSON.parse(replayInputs);
    } catch {
      setActionError("Replay inputs must be valid JSON.");
      return;
    }
    if (!inputs || typeof inputs !== "object" || Array.isArray(inputs)) {
      setActionError("Replay inputs must be a JSON object.");
      return;
    }
    void runAction("replay", {
      candidate_id: selectedCandidateId,
      inputs,
    });
  }

  return (
    <div className="workspace-scroll workflow-workspace">
      <header className="workspace-heading workflow-heading">
        <div>
          <h2>Workflow intelligence</h2>
          <p>
            Distill repeated Semwright operations without creating a parallel workflow store.
            Recording, compilation, replay and promotion always re-enter canonical Broker and
            Policy gates.
          </p>
        </div>
        <div className="workflow-heading-actions">
          <span className={"status-pill " + (available ? "status-current" : "status-unknown")}>
            {statusLabel}
          </span>
          <button
            className="button compact"
            type="button"
            disabled={loading}
            onClick={() => setRefresh((value) => value + 1)}
          >
            <RefreshCw size={13} aria-hidden="true" />
            Refresh
          </button>
        </div>
      </header>

      <div className="workflow-authority-strip">
        <ShieldCheck size={16} aria-hidden="true" />
        <div>
          <strong>
            {available
              ? "Semwright core is the workflow authority."
              : "No live workflow evidence is being presented."}
          </strong>
          <span>
            {available
              ? "The owner-provisioned connection is resource-bound. Reads and explicit actions re-enter current Broker, Policy, consent and descriptor checks."
              : overview?.reason ?? "Workflow evidence is unavailable until the desktop boundary is configured."}
          </span>
          {overview?.connection_identity ? (
            <code title={overview.connection_identity}>
              connection {short(overview.connection_identity, 54)}
            </code>
          ) : null}
        </div>
      </div>

      {error ? (
        <div className="jobs-inline-error" role="alert">
          <TriangleAlert size={15} aria-hidden="true" />
          <div>
            <strong>Workflow evidence could not be read.</strong>
            <span>{error}</span>
          </div>
        </div>
      ) : null}

      {actionError ? (
        <div className="jobs-inline-error" role="alert">
          <TriangleAlert size={15} aria-hidden="true" />
          <div>
            <strong>The canonical action did not complete.</strong>
            <span>{actionError}</span>
          </div>
        </div>
      ) : null}

      <section className="workflow-summary-grid" aria-label="Workflow evidence summary">
        {[
          ["Traces", data.traces.length],
          ["Patterns", data.patterns.length],
          ["Suggestions", data.suggestions.length],
          ["Proposals", data.proposals.length],
          ["Candidates", data.candidates.length],
          ["Promoted", data.promotions.length],
        ].map(([label, count]) => (
          <div className="workflow-metric" key={label}>
            <span>{label}</span>
            <strong>{loading ? "—" : count}</strong>
          </div>
        ))}
      </section>

      {available ? (
        <>
          <section className="workflow-write-gate" aria-label="Workflow write consent">
            <label>
              <input
                type="checkbox"
                checked={writesEnabled}
                onChange={(event) => setWritesEnabled(event.target.checked)}
              />
              <span>
                <strong>Enable explicit workflow changes for this workspace session.</strong>
                <small>
                  This never grants authority. Each write is still authorized by Semwright. Replay
                  may invoke real application mutations and is never run automatically.
                </small>
              </span>
            </label>
          </section>

          <div className="workflow-control-grid">
            <section className="workflow-control-panel" aria-label="Explicit workflow recorder">
              <header>
                <div>
                  <span className="section-label">V1 recorder</span>
                  <strong>{recording ? "Recording active" : "Explicit capture only"}</strong>
                </div>
                {recording ? <span className="status-pill status-stale">RECORDING</span> : null}
              </header>
              <div className="workflow-form">
                <label>
                  <span>Recording name</span>
                  <input
                    value={recordName}
                    maxLength={80}
                    disabled={recording || Boolean(busy)}
                    onChange={(event) => setRecordName(event.target.value)}
                  />
                </label>
                <label>
                  <span>Intent</span>
                  <textarea
                    value={recordIntent}
                    maxLength={4096}
                    rows={2}
                    disabled={recording || Boolean(busy)}
                    onChange={(event) => setRecordIntent(event.target.value)}
                    placeholder="What reusable operation are you demonstrating?"
                  />
                </label>
                <label className="workflow-inline-check">
                  <input
                    type="checkbox"
                    checked={captureValues}
                    disabled={recording || Boolean(busy)}
                    onChange={(event) => setCaptureValues(event.target.checked)}
                  />
                  <span>
                    Capture argument values. Off by default; enable only when the demonstrated
                    values are safe to persist in a bounded workflow trace.
                  </span>
                </label>
                <div className="workflow-form-actions">
                  {recording ? (
                    <>
                      <button
                        className="button compact primary"
                        type="button"
                        disabled={!writesEnabled || Boolean(busy)}
                        onClick={() =>
                          void runAction("record_stop", { successful: true }, () =>
                            markRecording(false),
                          )
                        }
                      >
                        <Square size={12} aria-hidden="true" />
                        Stop & keep
                      </button>
                      <button
                        className="button compact"
                        type="button"
                        disabled={!writesEnabled || Boolean(busy)}
                        onClick={() =>
                          void runAction("record_stop", { successful: false }, () =>
                            markRecording(false),
                          )
                        }
                      >
                        Stop as failed
                      </button>
                    </>
                  ) : (
                    <button
                      className="button compact primary"
                      type="button"
                      disabled={!writesEnabled || Boolean(busy) || !recordName.trim()}
                      onClick={() =>
                        void runAction(
                          "record_start",
                          {
                            name: recordName.trim(),
                            intent: recordIntent.trim(),
                            capture_values: captureValues,
                          },
                          () => markRecording(true),
                        )
                      }
                    >
                      <Play size={12} aria-hidden="true" />
                      Start explicit recording
                    </button>
                  )}
                </div>
              </div>
            </section>

            <section className="workflow-control-panel" aria-label="Trace compiler">
              <header>
                <div>
                  <span className="section-label">V1 compiler</span>
                  <strong>{selectedTraceIds.length}/8 traces selected</strong>
                </div>
              </header>
              <div className="workflow-trace-picker">
                {data.traces.slice(0, 12).map((trace, index) => {
                  const traceId = identityFor(trace, "trace-" + index);
                  return (
                    <label key={traceId}>
                      <input
                        type="checkbox"
                        checked={selectedTraceIds.includes(traceId)}
                        disabled={
                          Boolean(busy) ||
                          (!selectedTraceIds.includes(traceId) && selectedTraceIds.length >= 8)
                        }
                        onChange={(event) => toggleTrace(traceId, event.target.checked)}
                      />
                      <span>
                        <strong>{short(titleFor(trace), 38)}</strong>
                        <small className="mono">{short(traceId, 36)}</small>
                      </span>
                    </label>
                  );
                })}
                {data.traces.length === 0 ? (
                  <div className="workflow-panel-empty">
                    <CircleDashed size={15} aria-hidden="true" />
                    <span>No explicit traces are available to compile.</span>
                  </div>
                ) : null}
              </div>
              <div className="workflow-form workflow-compile-form">
                <label>
                  <span>Candidate name</span>
                  <input
                    value={compileName}
                    maxLength={80}
                    onChange={(event) => setCompileName(event.target.value)}
                  />
                </label>
                <label>
                  <span>Description</span>
                  <input
                    value={compileDescription}
                    maxLength={4096}
                    onChange={(event) => setCompileDescription(event.target.value)}
                  />
                </label>
                <button
                  className="button compact primary"
                  type="button"
                  disabled={
                    !writesEnabled ||
                    Boolean(busy) ||
                    selectedTraceIds.length === 0 ||
                    !compileName.trim()
                  }
                  onClick={() =>
                    void runAction(
                      "compile",
                      {
                        trace_ids: selectedTraceIds,
                        name: compileName.trim(),
                        description: compileDescription.trim(),
                      },
                      () => {
                        setSelectedTraceIds([]);
                        setCompileName("");
                        setCompileDescription("");
                      },
                    )
                  }
                >
                  <Check size={12} aria-hidden="true" />
                  Compile candidate
                </button>
              </div>
            </section>
          </div>
        </>
      ) : null}

      <div className="workflow-columns">
        <WorkflowList
          label="Proposals"
          icon={<Sparkles size={14} />}
          items={data.proposals}
          empty="No proposal is currently ready from the connected evidence."
          actions={
            available
              ? (item) => {
                  const proposalId = text(item.id);
                  if (!proposalId) return null;
                  return (
                    <>
                      <button
                        className="button compact"
                        type="button"
                        disabled={Boolean(busy)}
                        onClick={() =>
                          void runAction("proposal_plan", {
                            proposal_id: proposalId,
                            inputs: {},
                          })
                        }
                      >
                        Plan only
                      </button>
                      <button
                        className="button compact"
                        type="button"
                        disabled={!writesEnabled || Boolean(busy)}
                        onClick={() =>
                          void runAction("proposal_accept", { proposal_id: proposalId })
                        }
                      >
                        Accept exact proposal
                      </button>
                    </>
                  );
                }
              : undefined
          }
        />
        <WorkflowList
          label="Suggestions"
          icon={<Activity size={14} />}
          items={data.suggestions}
          empty="No repeated routine has reached the suggestion threshold."
          actions={
            available
              ? (item) => {
                  const suggestionId = text(item.suggestion_id);
                  if (!suggestionId) return null;
                  return (
                    <button
                      className="button compact"
                      type="button"
                      disabled={!writesEnabled || Boolean(busy)}
                      onClick={() =>
                        void runAction("suggestion_compile", {
                          suggestion_id: suggestionId,
                        })
                      }
                    >
                      Compile suggestion
                    </button>
                  );
                }
              : undefined
          }
        />
        <WorkflowList
          label="Candidates"
          icon={<Workflow size={14} />}
          items={data.candidates}
          empty="No accepted or compiled workflow candidate is present in the connected session."
          actions={
            available
              ? (item) => {
                  const index = data.candidates.indexOf(item);
                  const candidateId = identityFor(item, "candidate-" + index);
                  return (
                    <button
                      className="button compact"
                      type="button"
                      disabled={Boolean(busy)}
                      onClick={() => selectCandidate(item, index)}
                    >
                      {selectedCandidateId === candidateId ? "Selected" : "Inspect gates"}
                    </button>
                  );
                }
              : undefined
          }
        />
        <WorkflowList
          label="Promotions"
          icon={<GitBranch size={14} />}
          items={data.promotions}
          empty="No promoted workflow capability is present in the connected session."
        />
      </div>

      {available && selectedCandidate && selectedCandidateId ? (
        <section className="workflow-candidate-console" aria-label="Candidate verification and replay">
          <header>
            <div>
              <span className="section-label">Candidate gates</span>
              <strong>{titleFor(selectedCandidate)}</strong>
            </div>
            <code>{short(selectedCandidateId, 52)}</code>
          </header>
          <div className="workflow-gate-facts">
            <span>
              static verified <strong>{text(selectedCandidate.static_verified) ?? "false"}</strong>
            </span>
            <span>
              successful replays{" "}
              <strong>{text(selectedCandidate.successful_replays) ?? "0"}</strong>
            </span>
            <span>
              status <strong>{text(selectedCandidate.status) ?? "unknown"}</strong>
            </span>
          </div>
          <p className="workflow-stochastic-note">
            Recipes can automate model calls and validation, but AI-backed steps remain stochastic.
            Promotion does not make creative output deterministic.
          </p>
          <div className="workflow-candidate-actions">
            <button
              className="button compact"
              type="button"
              disabled={!writesEnabled || Boolean(busy)}
              onClick={() =>
                void runAction("verify", {
                  candidate_id: selectedCandidateId,
                })
              }
            >
              Verify current descriptors
            </button>
            <label>
              <span>Replay inputs JSON</span>
              <textarea
                value={replayInputs}
                rows={4}
                spellCheck={false}
                onChange={(event) => setReplayInputs(event.target.value)}
              />
            </label>
            <button
              className="button compact primary"
              type="button"
              disabled={!writesEnabled || Boolean(busy)}
              onClick={replaySelected}
            >
              <Play size={12} aria-hidden="true" />
              Replay through Broker
            </button>
            <label>
              <span>Promotion slug</span>
              <input
                value={promotionSlug}
                maxLength={40}
                onChange={(event) => setPromotionSlug(event.target.value)}
                placeholder="my-workflow"
              />
            </label>
            <button
              className="button compact"
              type="button"
              disabled={
                !writesEnabled ||
                Boolean(busy) ||
                !/^[a-z][a-z0-9-]{0,38}[a-z0-9]$/.test(promotionSlug)
              }
              onClick={() =>
                void runAction("promote", {
                  candidate_id: selectedCandidateId,
                  slug: promotionSlug,
                })
              }
            >
              Promote after replay
            </button>
          </div>
        </section>
      ) : null}

      {receipt ? (
        <section className="workflow-receipt" aria-label="Last canonical workflow receipt">
          <header>
            <div>
              <span className="section-label">Last canonical receipt</span>
              <strong>{receipt.command}</strong>
            </div>
            <span className="mono">{short(receipt.request_id, 48)}</span>
          </header>
          <pre>{JSON.stringify(receipt.result, null, 2)}</pre>
        </section>
      ) : null}

      <section className="workflow-evidence">
        <header>
          <div>
            <span className="section-label">Evidence ledger</span>
            <strong>{data.traces.length + data.patterns.length} observed items</strong>
          </div>
          <Braces size={16} aria-hidden="true" />
        </header>
        <div className="workflow-ledger">
          {[...data.patterns, ...data.traces].slice(0, 18).map((item, index) => {
            const id = identityFor(item, "evidence-" + index);
            return (
              <div className="workflow-ledger-row" key={id}>
                <span className="mono">{short(id, 26)}</span>
                <strong>{short(titleFor(item), 56)}</strong>
                <span>{detailFor(item)}</span>
              </div>
            );
          })}
          {!loading && data.traces.length + data.patterns.length === 0 ? (
            <div className="workflow-panel-empty">
              <CircleDashed size={16} aria-hidden="true" />
              <span>No trace or pattern evidence is available to display.</span>
            </div>
          ) : null}
        </div>
      </section>

      <footer className="jobs-footnote">
        Motionwright never records desktop activity in the background and never auto-accepts,
        replays or promotes learned behavior. Every operational step is explicit and remains subject
        to the canonical Semwright session, current policy, consent and descriptor state.
      </footer>
    </div>
  );
}
