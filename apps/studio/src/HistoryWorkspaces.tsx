import { Braces, GitBranch, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { projectHistoryRecent } from "./api";
import { HISTORY_PAGE_SIZE, validateRecentHistoryPage } from "./historyPaging";
import { BranchRecovery, useBranchDraft } from "./useBranchDraft";
import type { Change, Project, ProjectEvent } from "./types";
export { default as ReviewWorkspace } from "./ReviewWorkspace";

type Commit = (change: Change) => Promise<void>;

function eventTarget(event: ProjectEvent) {
  const sceneId = typeof event.change.scene_id === "string" ? event.change.scene_id : null;
  const nodeId = typeof event.change.node_id === "string" ? event.change.node_id : null;
  const reviewId = typeof event.change.review_id === "string" ? event.change.review_id : null;
  const branchId =
    typeof event.change.branch_id === "string"
      ? event.change.branch_id
      : typeof event.change.source_branch_id === "string"
        ? event.change.source_branch_id
        : null;
  if (nodeId) return "node:" + nodeId.slice(0, 8);
  if (sceneId) return "scene:" + sceneId.slice(0, 8);
  if (reviewId) return "review:" + reviewId.slice(0, 8);
  if (branchId) return "branch:" + branchId.slice(0, 8);
  return "project";
}

export function ChangesWorkspace({ project, commit, commitBranch, busy }: {
  project: Project; commit: Commit; commitBranch: (change: Change) => Promise<Project>; busy: boolean;
}) {
  const [events, setEvents] = useState<ProjectEvent[]>([]);
  const branch = useBranchDraft(project, commitBranch, busy);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [hasEarlier, setHasEarlier] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyRetry, setHistoryRetry] = useState(0);
  const inFlightEarlier = useRef(false);
  // Ignore late pages after a project open, branch change or creative revision.
  const requestKey = project.id + "/" + project.generation + "/" + project.revision;
  const activeRequestKey = useRef(requestKey);
  activeRequestKey.current = requestKey;

  const activeBranch = project.branches.find((branch) => branch.id === project.active_branch) ?? null;

  useEffect(() => {
    let cancelled = false;
    const key = requestKey;
    inFlightEarlier.current = false;
    setEvents([]);
    setLoadingHistory(true);
    setLoadingEarlier(false);
    setHasEarlier(false);
    setHistoryError(null);
    projectHistoryRecent(project, project.revision, HISTORY_PAGE_SIZE + 1)
      .then((returned) => {
        if (cancelled || activeRequestKey.current !== key) return;
        const page = validateRecentHistoryPage(returned, project.revision);
        setEvents(page.events);
        setHasEarlier(page.hasEarlier);
      })
      .catch((reason) => {
        if (!cancelled && activeRequestKey.current === key) {
          setHistoryError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (!cancelled && activeRequestKey.current === key) setLoadingHistory(false);
      });
    return () => { cancelled = true; };
  }, [project.id, project.generation, project.revision, historyRetry]);

  const loadEarlier = async () => {
    const lastRevision = events.at(-1)?.revision;
    if (!hasEarlier || loadingHistory || inFlightEarlier.current || lastRevision == null || lastRevision < 2) return;
    const key = requestKey;
    const through = lastRevision - 1;
    inFlightEarlier.current = true;
    setLoadingEarlier(true);
    setHistoryError(null);
    try {
      const returned = await projectHistoryRecent(project, through, HISTORY_PAGE_SIZE + 1);
      const page = validateRecentHistoryPage(returned, through);
      if (activeRequestKey.current !== key) return;
      setEvents((current) => {
        if (current.at(-1)?.revision !== lastRevision) return current;
        return [...current, ...page.events];
      });
      setHasEarlier(page.hasEarlier);
    } catch (reason) {
      if (activeRequestKey.current === key) {
        setHistoryError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (activeRequestKey.current === key) {
        setLoadingEarlier(false);
        inFlightEarlier.current = false;
      }
    }
  };

  return (
    <div className="workspace-scroll table-view">
      <header className="workspace-heading">
        <div>
          <h2>Changes</h2>
          <p>Creative branches isolate project state. Merges compare an explicit base and fail closed on overlapping semantic edits.</p>
        </div>
        <span className="revision-chip">r{project.revision}</span>
      </header>

      <section className="changes-section">
        <div className="section-title-row">
          <span className="section-label">Branches</span>
          <span className="count-label">{project.branches.length}</span>
        </div>

        <form
          className="inline-save-field"
          onSubmit={(event) => {
            event.preventDefault();
            void branch.save();
          }}
        >
          <input
            aria-label="New branch name"
            placeholder="new creative branch"
            value={branch.session.draft.name}
            onChange={(event) => branch.edit(event.target.value)}
          />
          <button className="button button-primary" disabled={!branch.canSave} type="submit">
            <GitBranch size={13} /> Create from r{project.revision}
          </button>
        </form>
        <BranchRecovery editor={branch} />

        <div className="data-table" role="table" aria-label="Creative branches">
          <div className="data-row data-head" role="row">
            <span>Branch</span><span>Base / head</span><span>Lineage</span><span>Actions</span>
          </div>
          {project.branches.map((branch) => {
            const active = branch.id === project.active_branch;
            const parent = project.branches.find((candidate) => candidate.id === branch.parent_branch);
            const mergeable = !active && branch.parent_branch === project.active_branch;
            return (
              <div className="data-row" role="row" key={branch.id}>
                <span>
                  <GitBranch size={14} />
                  <span>
                    <strong>{branch.name}</strong>
                    {branch.protected ? <small>protected</small> : null}
                  </span>
                </span>
                <span className="mono">r{branch.base_revision} → r{Math.max(branch.head_revision, branch.base_revision)}</span>
                <span>{parent ? "from " + parent.name : "root"}</span>
                <span>
                  {active ? (
                    <span className="status-pill status-current">ACTIVE</span>
                  ) : (
                    <span className="portable-actions">
                      <button
                        className="button compact"
                        type="button"
                        onClick={() => commit({ type: "checkout_branch", branch_id: branch.id })}
                      >
                        Checkout
                      </button>
                      <button
                        className="button compact"
                        type="button"
                        disabled={!mergeable}
                        title={mergeable ? "Three-way merge into the active parent branch" : "First-party merge currently requires a direct child of the active branch"}
                        onClick={() => commit({ type: "merge_branch", source_branch_id: branch.id })}
                      >
                        Merge
                      </button>
                    </span>
                  )}
                </span>
              </div>
            );
          })}
        </div>

        <div className="evidence-callout">
          <ShieldCheck size={18} />
          <div>
            <strong>{activeBranch?.protected ? "Protected active branch" : "Explicit merge boundary"}</strong>
            <span>
              No last-writer-wins path is used. A semantic overlap returns a conflict and leaves the active branch unchanged.
            </span>
          </div>
        </div>
      </section>

      <section className="changes-section">
        <div className="section-title-row">
          <span className="section-label">Merge receipts</span>
          <span className="count-label">{project.merges.length}</span>
        </div>
        <div className="data-table" role="table" aria-label="Merge receipts">
          <div className="data-row data-head" role="row">
            <span>Source</span><span>Target</span><span>Base</span><span>Committed</span>
          </div>
          {[...project.merges].reverse().map((merge) => {
            const source = project.branches.find((branch) => branch.id === merge.source_branch);
            const target = project.branches.find((branch) => branch.id === merge.target_branch);
            return (
              <div className="data-row" role="row" key={merge.id}>
                <span>{source?.name ?? merge.source_branch.slice(0, 8)}</span>
                <span>{target?.name ?? merge.target_branch.slice(0, 8)}</span>
                <span className="mono">r{merge.base_revision}</span>
                <span className="mono">{merge.committed_revision === null ? "pending" : "r" + merge.committed_revision}</span>
              </div>
            );
          })}
          {project.merges.length === 0 ? (
            <div className="journal-empty">
              <GitBranch size={16} />
              <span>No merge has been committed in this project.</span>
            </div>
          ) : null}
        </div>
      </section>

      <section className="changes-section">
        <div className="section-title-row">
          <span className="section-label">Committed event journal</span>
          <span className="count-label" aria-live="polite">
            {loadingHistory ? "Reading latest…" : events.length + " recent" + (hasEarlier ? " · older available" : "")}
          </span>
        </div>
        {historyError && (
          <div className="error-banner inline-error" role="alert">
            <strong>History read failed.</strong>
            <span>{historyError}</span>
            <button className="button compact" type="button"
              onClick={() => events.length > 0 ? void loadEarlier() : setHistoryRetry((value) => value + 1)}>
              Retry history read
            </button>
          </div>
        )}
        <div className="data-table" role="table" aria-label="Project event journal">
            <div className="data-row event-row data-head" role="row">
              <span>Revision</span><span>Change</span><span>Target</span><span>Committed</span>
            </div>
            {events.map((event) => (
              <div className="data-row event-row" role="row" key={event.revision}>
                <span className="mono">r{event.revision}</span>
                <span>{event.change.type.replaceAll("_", " ")}</span>
                <span className="mono">{eventTarget(event)}</span>
                <span>{new Date(event.created_at).toLocaleString()}</span>
              </div>
            ))}
            {!loadingHistory && !historyError && events.length === 0 ? (
              <div className="journal-empty">
                <Braces size={16} />
                <span>No committed changes are stored for this project yet.</span>
              </div>
            ) : null}
          </div>
        <div className="journal-pagination" role="group" aria-label="Journal history pagination">
          <span className="journal-range" aria-live="polite">
            {loadingHistory ? "Loading the newest committed changes" :
              events.length === 0 ? "No history rows loaded" :
              "Showing " + events.length + " committed revisions, newest first"}
          </span>
          {hasEarlier && (
            <button className="button compact" type="button"
              disabled={loadingHistory || loadingEarlier}
              onClick={() => void loadEarlier()}>
              {loadingEarlier ? "Reading earlier…" : "Load earlier changes"}
            </button>
          )}
          {!hasEarlier && !loadingHistory && !historyError && events.length > 0 && (
            <span className="journal-range">Beginning of recorded history</span>
          )}
        </div>
      </section>
    </div>
  );
}
