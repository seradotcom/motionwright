import { CircleDashed, Clock3, MessageSquareText, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";
import CreativeAlternativesPanel from "./CreativeAlternativesPanel";
import { reviewHasWork, reviewProfileAnchor, reviewTimes, useReviewDraft } from "./reviewDraft";
import type { Change, CreativeReview, Project, RationalTime, ReviewKind, ReviewStatus, Scene } from "./types";
import { seconds } from "./types";
import { graphReviewAnchor } from "./semanticGraph";

const reviewLabel: Record<ReviewStatus, string> = {
  open: "OPEN", resolved: "RESOLVED", dismissed: "DISMISSED", needs_recheck: "NEEDS RECHECK",
};

export default function ReviewWorkspace({
  project,
  scene,
  commit,
  playhead,
  onSeek,
  externalResource = null,
}: {
  project: Project;
  externalResource?: string | null;
  scene: Scene | null;
  commit: (change: Change) => Promise<Project>;
  playhead: number;
  onSeek: (absoluteSeconds: number) => void;
}) {
  const [anchorScope, setAnchorScope] = useState<"selection" | "project" | "graph">(externalResource ? "graph" : "selection");
  const resource = anchorScope === "graph" && externalResource ? externalResource : anchorScope === "project" || !scene ? "project:" + project.id : "scene:" + scene.id;
  const graphAnchor = anchorScope === "graph" && externalResource ? graphReviewAnchor(project, externalResource) : null;
  const graphUnavailable = anchorScope === "graph" && !graphAnchor;
  const editor = useReviewDraft(project, { kind: "comment", id: resource }, commit);
  const { kind, timeMode, rangeStart, rangeEnd, body, profileId = "" } = editor.draft.raw;
  const setKind = (value: ReviewKind) => editor.edit("kind", value);
  const setTimeMode = (value: "resource" | "playhead" | "range") => editor.edit("timeMode", value);
  const setRangeStart = (value: string) => editor.edit("rangeStart", value);
  const setRangeEnd = (value: string) => editor.edit("rangeEnd", value);
  const setBody = (value: string) => editor.edit("body", value);
  const sortedReviews = useMemo(
    () => [...project.reviews].sort((left, right) => right.created_at.localeCompare(left.created_at)),
    [project.reviews],
  );
  const sceneLocks = scene ? project.locks.filter((lock) => lock.resource === resource) : [];
  const projectDuration = Math.max(
    1,
    ...project.scenes.map((item) => seconds(item.start) + seconds(item.duration)),
    ...project.audio.voice_tracks.map((track) => seconds(track.measured_duration)),
    ...project.audio.transcript.map((segment) => seconds(segment.end)),
    ...project.audio.cues.map((cue) => seconds(cue.at)),
    ...project.markers.map((marker) => seconds(marker.at)),
  );
  const boundsStart = graphAnchor?.start ? seconds(graphAnchor.start) : scene && anchorScope === "selection" ? seconds(scene.start) : 0;
  const boundsEnd = graphAnchor?.end ? seconds(graphAnchor.end) : scene && anchorScope === "selection"
    ? seconds(scene.start) + seconds(scene.duration) : projectDuration;
  let times: { start: RationalTime | null; end: RationalTime | null } | null = null;
  try { times = reviewTimes(project, resource, editor.draft.raw, playhead); } catch { /* Keep incomplete author input unchanged. */ }
  let profileAnchor: ReturnType<typeof reviewProfileAnchor> | null = null, profileError: string | null = null;
  try { profileAnchor = reviewProfileAnchor(project, resource, editor.draft.raw); }
  catch (reason) { profileError = reason instanceof Error ? reason.message : String(reason); }
  const selectedProfileMissing = !!profileId && !project.deliverables.some(profile => profile.id === profileId);
  const pointValid = timeMode !== "playhead" || times !== null;
  const rangeValid = timeMode !== "range" || times !== null;

  return (
    <div className="workspace-scroll review-view">
      <header className="workspace-heading">
        <div>
          <h2>Review</h2>
          <p>Comments are anchored to an exact branch and revision. Later content changes never inherit an earlier approval.</p>
        </div>
        <span className="count-label">{project.reviews.length} anchored</span>
      </header>

      <CreativeAlternativesPanel project={project} commit={commit} />

      <div className="review-columns">
        <section>
          <span className="section-label">New situated review</span>
          <div className="narrative-sheet">
            <ReviewRecoveryStatus editor={editor} />
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
                <span className="field-label">Anchor scope</span>
                <select aria-label="Review resource scope" value={anchorScope}
                  onChange={(event) => setAnchorScope(event.target.value as typeof anchorScope)}>
                  <option value="selection">{scene ? "Selected scene" : "Current project"}</option>
                  <option value="project">Whole project</option>
                  {externalResource && <option value="graph">Saved Graph identity and profile</option>}
                </select>
                <span className="review-resource-name mono">{resource}</span>
              </label>
            </div>
            {graphUnavailable && <p role="alert">This saved Graph identity or profile is unavailable. Your review input is retained.</p>}
            {anchorScope === "graph" ? <p className="review-time-hint">
              Saved Graph deliverable: <span className="mono">{graphAnchor?.profile_id ?? "unavailable"}</span>
              {" · language: "}{graphAnchor?.locale ?? "unavailable"}. This anchor belongs to the saved Graph.
            </p> : <label>
              <span className="field-label">Deliverable and language</span>
              <select aria-label="Review deliverable" value={profileId}
                onChange={event => editor.edit("profileId", event.target.value)}>
                <option value="">No deliverable or language anchor</option>
                {selectedProfileMissing && <option value={profileId}>Unavailable deliverable · {profileId}</option>}
                {project.deliverables.map(profile => <option key={profile.id} value={profile.id}>
                  {profile.name} · {profile.language}
                </option>)}
              </select>
              <span className="review-time-hint">{profileAnchor?.profile_id
                ? "Review language: " + profileAnchor.locale + ". Saved with this revision's deliverable."
                : "No deliverable or language will be inferred from the current preview."}</span>
            </label>}
            {profileError && !graphUnavailable && <p role="alert">{profileError}</p>}
            <div className="review-time-form">
              <label>
                <span className="field-label">Time anchor</span>
                <select
                  aria-label="Review time anchor"
                  value={timeMode}
                  onChange={(event) => setTimeMode(event.target.value as typeof timeMode)}
                >
                  <option value="resource">{graphAnchor ? "Whole saved Graph interval" : "Resource only"}</option>
                  <option value="playhead">Current playhead (project time)</option>
                  <option value="range">Explicit project time range</option>
                </select>
              </label>
              {timeMode === "playhead" && (
                <div className={"review-time-hint" + (!pointValid ? " invalid" : "")}>
                  <Clock3 size={13} aria-hidden="true" />
                  {pointValid
                    ? "At " + playhead.toFixed(3) + "s on the project clock; captured when added."
                    : "Playhead is outside the selected scene. Choose project scope or seek within the scene."}
                </div>
              )}
              {timeMode === "range" && (
                <>
                  <div className="review-time-fields">
                    <label>
                      <span className="field-label">Start (project seconds)</span>
                      <input
                        aria-label="Review range start"
                        inputMode="decimal"
                        value={rangeStart}
                        onChange={(event) => setRangeStart(event.target.value)}
                      />
                    </label>
                    <label>
                      <span className="field-label">End (project seconds)</span>
                      <input
                        aria-label="Review range end"
                        inputMode="decimal"
                        value={rangeEnd}
                        onChange={(event) => setRangeEnd(event.target.value)}
                      />
                    </label>
                  </div>
                  <div className={"review-time-hint" + (!rangeValid ? " invalid" : "")}>
                    Allowed {boundsStart.toFixed(3)}–{boundsEnd.toFixed(3)}s on the project clock.
                    End must not precede start; choose project scope for cross-scene reviews.
                    Switching time anchors keeps these inputs.
                  </div>
                </>
              )}
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
                disabled={editor.blocked || graphUnavailable || !profileAnchor || !times || !body.trim() || (timeMode === "range" && !rangeValid) || (timeMode === "playhead" && !pointValid)}
                onClick={() => {
                  if (!times || !profileAnchor) return;
                  void editor.submit({
                    type: "add_review", kind, resource, body: body.trim(),
                    start: times.start, end: times.end, locale: profileAnchor.locale, profile_id: profileAnchor.profile_id,
                  });
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
            const sameBranch = review.anchor.branch_id === project.active_branch;
            const isHistorical = review.status === "needs_recheck" || !sameBranch;
            return (
              <article className="critique-copy" key={review.id} data-review-id={review.id}>
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
                <p className="review-history-note" aria-label="Stored review deliverable and language">
                  Stored deliverable: <span className="mono">{review.anchor.profile_id ?? "none"}</span>
                  {" · stored language: "}{review.anchor.locale ?? "none"}
                  {" · values recorded at r"}{review.anchor.revision}; current deliverable settings do not update this anchor.
                </p>
                {review.anchor.start && (
                  <div className="review-time-actions">
                    <span className="mono">
                      {seconds(review.anchor.start).toFixed(3)}s
                      {review.anchor.end ? " – " + seconds(review.anchor.end).toFixed(3) + "s" : " · point"}
                    </span>
                    <button
                      className="button compact"
                      type="button"
                      aria-label={"Seek to review time: " + review.body.slice(0, 60)}
                      disabled={!sameBranch}
                      title={sameBranch
                        ? "Seek to stored project timestamp. This does not revalidate the historical content."
                        : "Select the review's original branch before seeking."}
                      onClick={() => onSeek(seconds(review.anchor.start!))}
                    >
                      <Clock3 size={13} aria-hidden="true" /> Jump to time
                    </button>
                  </div>
                )}
                {isHistorical && review.anchor.start && (
                  <p className="review-history-note">
                    {sameBranch
                      ? "Historical edit: this timestamp may no longer describe the same content."
                      : "Other branch: select the original branch before navigating to the stored time."}
                  </p>
                )}
                {review.resolution ? <p><strong>Resolution:</strong> {review.resolution}</p> : null}
                <ReviewResolution project={project} review={review} commit={commit} />
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

export function ReviewRecoveryStatus({ editor }: { editor: ReturnType<typeof useReviewDraft> }) {
  return <div role="group" aria-label="Review draft recovery">
    {editor.unknown && <p role="status">Original review outcome UNKNOWN. {editor.saving
      ? "Waiting for the original response." : "The input was retained and this request will not be replayed."}</p>}
    {editor.unknown && <button type="button" className="button compact" disabled={editor.saving || editor.reviewing}
      onClick={() => void editor.readOutcome()}>Read saved review and history</button>}
    {editor.outcome && <section role="region" aria-label="Saved review outcome">
      <p>Read saved revision r{editor.outcome.project.revision} and {editor.outcome.events.length} contiguous history entries.</p>
      <p>Matching authored values at revisions {editor.outcome.matchingValueRevisions.join(", ") || "none"} do not identify or acknowledge the original request.</p>
      <p>The original UNKNOWN copy will remain. Later author input is kept; unchanged submitted text starts blank to prevent replay.</p>
      {editor.outcome.project.revision <= editor.outcome.pending.revision && <p role="status">
        The original request can still commit on this revision. Keep it UNKNOWN and inspect again after the saved revision advances.
      </p>}
      <button type="button" className="button compact" disabled={editor.saving || editor.reviewing
        || editor.outcome.project.revision <= editor.outcome.pending.revision}
        onClick={editor.separateIntent}>Keep original attempt and start a separate review</button>
    </section>}
    {editor.storageError && <div role="alert"><p>{editor.storageError}</p>
      <button type="button" className="button compact" onClick={editor.retryBackup}>Retry review recovery copy</button></div>}
    {editor.stale && !editor.unknown && <div role="status">
      <p>This draft was authored at r{editor.draft.baseRevision}. Compare the current source before keeping it for this revision.</p>
      <button type="button" className="button compact" disabled={editor.saving || editor.unknown}
        onClick={editor.advance}>Review draft on current revision</button>
    </div>}
    {editor.error && <p role="alert">{editor.error}</p>}
    {editor.notice && <p role="status">{editor.notice}</p>}
    {reviewHasWork(editor.draft) && <button type="button" className="button compact"
      onClick={editor.download}>Export review recovery copy</button>}
  </div>;
}
export function ReviewResolution({ project, review, commit }: {
  project: Project; review: CreativeReview; commit: (change: Change) => Promise<Project>;
}) {
  const editor = useReviewDraft(project, { kind: "resolution", id: review.id }, commit);
  const resolution = editor.draft.raw.resolution;
  return <div>
    <ReviewRecoveryStatus editor={editor} />
    {(review.status !== "resolved" || resolution.length > 0) && <div className="inline-save-field">
      <input aria-label={"Resolution for review " + review.id} maxLength={4000}
        placeholder="What changed or what evidence closed this review?" value={resolution}
        onChange={event => editor.edit("resolution", event.target.value)} />
      <button className="button compact" type="button"
        disabled={editor.blocked || review.status === "resolved" || !resolution.trim()}
        onClick={() => void editor.submit({ type: "resolve_review", review_id: review.id, resolution: resolution.trim() })}>
        Resolve
      </button>
    </div>}
    {review.status === "resolved" && <button className="button compact" type="button" disabled={editor.blocked}
      onClick={() => void editor.submit({ type: "reopen_review", review_id: review.id })}>Reopen for recheck</button>}
  </div>;
}
