import { Braces, CircleDashed, GitBranch, MessageSquareText, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { projectHistory } from "./api";
import type { Change, Project, ProjectEvent, ReviewKind, ReviewStatus, Scene } from "./types";

type Commit = (change: Change) => Promise<void>;

const reviewLabel: Record<ReviewStatus, string> = {
  open: "OPEN",
  resolved: "RESOLVED",
  dismissed: "DISMISSED",
  needs_recheck: "NEEDS RECHECK",
};

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

export function ChangesWorkspace({ project, commit }: { project: Project; commit: Commit }) {
  const [events, setEvents] = useState<ProjectEvent[]>([]);
  const [branchName, setBranchName] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const activeBranch = project.branches.find((branch) => branch.id === project.active_branch) ?? null;

  useEffect(() => {
    let cancelled = false;
    setLoadingHistory(true);
    setHistoryError(null);
    projectHistory(project, 0, 100)
      .then((next) => {
        if (!cancelled) setEvents(next);
      })
      .catch((reason) => {
        if (!cancelled) setHistoryError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false);
      });
    return () => {
      cancelled = true;
    };
  }, [project.id, project.revision]);

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
          onSubmit={async (event) => {
            event.preventDefault();
            const name = branchName.trim();
            if (!name) return;
            await commit({ type: "create_branch", name });
            setBranchName("");
          }}
        >
          <input
            aria-label="New branch name"
            maxLength={120}
            placeholder="new creative branch"
            value={branchName}
            onChange={(event) => setBranchName(event.target.value)}
          />
          <button className="button button-primary" disabled={!branchName.trim()} type="submit">
            <GitBranch size={13} /> Create from r{project.revision}
          </button>
        </form>

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
          <span className="count-label">{loadingHistory ? "loading" : events.length + " loaded"}</span>
        </div>
        {historyError ? (
          <div className="error-banner inline-error" role="alert">
            <strong>History unavailable.</strong>
            <span>{historyError}</span>
          </div>
        ) : (
          <div className="data-table" role="table" aria-label="Project event journal">
            <div className="data-row event-row data-head" role="row">
              <span>Revision</span><span>Change</span><span>Target</span><span>Committed</span>
            </div>
            {[...events].reverse().map((event) => (
              <div className="data-row event-row" role="row" key={event.revision}>
                <span className="mono">r{event.revision}</span>
                <span>{event.change.type.replaceAll("_", " ")}</span>
                <span className="mono">{eventTarget(event)}</span>
                <span>{new Date(event.created_at).toLocaleString()}</span>
              </div>
            ))}
            {!loadingHistory && events.length === 0 ? (
              <div className="journal-empty">
                <Braces size={16} />
                <span>No committed changes are stored for this project yet.</span>
              </div>
            ) : null}
          </div>
        )}
      </section>
    </div>
  );
}

export function ReviewWorkspace({
  project,
  scene,
  commit,
}: {
  project: Project;
  scene: Scene | null;
  commit: Commit;
}) {
  const [kind, setKind] = useState<ReviewKind>("creative");
  const [body, setBody] = useState("");
  const [resolutions, setResolutions] = useState<Record<string, string>>({});

  const resource = scene ? "scene:" + scene.id : "project:" + project.id;
  const sortedReviews = useMemo(
    () => [...project.reviews].sort((left, right) => right.created_at.localeCompare(left.created_at)),
    [project.reviews],
  );
  const sceneLocks = scene ? project.locks.filter((lock) => lock.resource === resource) : [];

  return (
    <div className="workspace-scroll review-view">
      <header className="workspace-heading">
        <div>
          <h2>Review</h2>
          <p>Comments are anchored to an exact branch and revision. Later content changes never inherit an earlier approval.</p>
        </div>
        <span className="count-label">{project.reviews.length} anchored</span>
      </header>

      <div className="review-columns">
        <section>
          <span className="section-label">New situated review</span>
          <div className="narrative-sheet">
            <div className="two-column-fields">
              <label>
                <span className="field-label">Channel</span>
                <select aria-label="Review kind" value={kind} onChange={(event) => setKind(event.target.value as ReviewKind)}>
                  <option value="technical">Technical</option>
                  <option value="creative">Creative</option>
                  <option value="editorial">Editorial</option>
                </select>
              </label>
              <label>
                <span className="field-label">Anchor</span>
                <div className="read-field mono">{resource}</div>
              </label>
            </div>
            <label>
              <span className="field-label">Comment</span>
              <textarea
                aria-label="Review comment"
                maxLength={8000}
                rows={5}
                value={body}
                placeholder={scene ? "Review " + scene.name + " at the current revision…" : "Review the current project revision…"}
                onChange={(event) => setBody(event.target.value)}
              />
            </label>
            <div className="sheet-actions">
              <span>branch {project.branches.find((branch) => branch.id === project.active_branch)?.name ?? "unknown"} · r{project.revision}</span>
              <button
                className="button button-primary"
                type="button"
                disabled={!body.trim()}
                onClick={async () => {
                  await commit({
                    type: "add_review",
                    kind,
                    resource,
                    body: body.trim(),
                    start: null,
                    end: null,
                    locale: null,
                    profile_id: null,
                  });
                  setBody("");
                }}
              >
                <MessageSquareText size={13} /> Add anchored review
              </button>
            </div>
          </div>

          <div className="review-entry">
            <CircleDashed size={18} />
            <div>
              <strong>Technical evidence</strong>
              <span>No canonical Effect Conformance report is attached by this UI. Creative comments cannot manufacture a PASS.</span>
            </div>
            <span className="status-pill status-unknown">UNKNOWN</span>
          </div>
          <div className="review-entry">
            <ShieldCheck size={18} />
            <div>
              <strong>Selected-resource locks</strong>
              <span>{sceneLocks.length ? sceneLocks.map((lock) => lock.kind).join(", ") : "No explicit locks on this resource."}</span>
            </div>
          </div>
        </section>

        <section>
          <span className="section-label">Anchored review history</span>
          {sortedReviews.map((review) => {
            const branch = project.branches.find((candidate) => candidate.id === review.anchor.branch_id);
            const resolution = resolutions[review.id] ?? "";
            return (
              <article className="critique-copy" key={review.id}>
                <div className="section-title-row">
                  <strong>{review.kind.toUpperCase()}</strong>
                  <span className={"status-pill status-" + (review.status === "needs_recheck" ? "stale" : review.status === "resolved" ? "current" : "unknown")}>
                    {reviewLabel[review.status]}
                  </span>
                </div>
                <p>{review.body}</p>
                <div className="story-meta">
                  <span>{branch?.name ?? review.anchor.branch_id.slice(0, 8)} · r{review.anchor.revision}</span>
                  <span className="mono">{review.anchor.resource}</span>
                </div>
                {review.resolution ? <p><strong>Resolution:</strong> {review.resolution}</p> : null}
                {review.status !== "resolved" ? (
                  <div className="inline-save-field">
                    <input
                      aria-label={"Resolution for review " + review.id}
                      maxLength={4000}
                      placeholder="What changed or what evidence closed this review?"
                      value={resolution}
                      onChange={(event) => setResolutions((current) => ({ ...current, [review.id]: event.target.value }))}
                    />
                    <button
                      className="button compact"
                      type="button"
                      disabled={!resolution.trim()}
                      onClick={() => commit({ type: "resolve_review", review_id: review.id, resolution: resolution.trim() })}
                    >
                      Resolve
                    </button>
                  </div>
                ) : (
                  <button
                    className="button compact"
                    type="button"
                    onClick={() => commit({ type: "reopen_review", review_id: review.id })}
                  >
                    Reopen for recheck
                  </button>
                )}
              </article>
            );
          })}
          {sortedReviews.length === 0 ? (
            <div className="empty-workspace">
              <MessageSquareText size={24} />
              <strong>No anchored reviews</strong>
              <span>Add one against the exact active branch and revision.</span>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
