import {
  Activity,
  Braces,
  CircleDashed,
  GitBranch,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  Workflow,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { workflowOverview } from "./api";
import type { Project, WorkflowOverview } from "./types";

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
  return parts.join(" · ") || "Canonical read-only evidence";
}

function WorkflowList({
  label,
  icon,
  items,
  empty,
}: {
  label: string;
  icon: ReactNode;
  items: Row[];
  empty: string;
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
            const identity =
              text(item.id) ?? text(item.suggestion_id) ?? text(item.slug) ?? label + "-" + index;
            return (
              <article className="workflow-row" key={identity}>
                <span className="workflow-row-icon" aria-hidden="true">{icon}</span>
                <div>
                  <strong title={titleFor(item)}>{short(titleFor(item), 52)}</strong>
                  <span>{detailFor(item)}</span>
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
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);

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

  const available = overview?.status === "available";
  const statusLabel =
    overview?.status === "available"
      ? "CANONICAL"
      : overview?.status === "unconfigured"
        ? "NOT CONNECTED"
        : overview?.status === "browser_demo"
          ? "BROWSER DEMO"
          : "READING";

  return (
    <div className="workspace-scroll workflow-workspace">
      <header className="workspace-heading workflow-heading">
        <div>
          <h2>Workflow intelligence</h2>
          <p>
            Read distilled routine evidence from the canonical Semwright session. Motionwright
            does not record, accept, replay, promote, or demote workflows from this surface.
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
              ? "Semwright core is the read authority."
              : "No live workflow evidence is being presented."}
          </strong>
          <span>
            {available
              ? "The connection is owner-provisioned and resource-bound; every query re-enters Broker/Policy."
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

      <div className="workflow-columns">
        <WorkflowList
          label="Proposals"
          icon={<Sparkles size={14} />}
          items={data.proposals}
          empty="No proposal is currently ready from the connected evidence."
        />
        <WorkflowList
          label="Suggestions"
          icon={<Activity size={14} />}
          items={data.suggestions}
          empty="No repeated routine has reached the suggestion threshold."
        />
        <WorkflowList
          label="Candidates"
          icon={<Workflow size={14} />}
          items={data.candidates}
          empty="No accepted workflow candidate is present in the connected session."
        />
        <WorkflowList
          label="Promotions"
          icon={<GitBranch size={14} />}
          items={data.promotions}
          empty="No promoted workflow capability is present in the connected session."
        />
      </div>

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
            const id = text(item.id) ?? text(item.pattern_id) ?? "evidence-" + index;
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
        This is an observational surface only. Promotion authority, replay execution, consent,
        capability admission and provenance remain owned by the canonical Semwright runtime.
      </footer>
    </div>
  );
}
