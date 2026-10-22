import { useEffect, useState } from "react";
import type { Change, Claim, Project } from "./types";
import { claimFields, claimRaw, claimScope } from "./claimSourceDraft";
import type { ClaimRaw, ClaimSourceChange } from "./claimSourceDraft";
import { useClaimSourceDraft } from "./useClaimSourceDraft";
import { useReviewDraft } from "./reviewDraft";
import { ReviewRecoveryStatus, ReviewResolution } from "./ReviewWorkspace";
import "./claim-source-editor.css";

type Commit = (change: Change | ClaimSourceChange) => Promise<Project>;
const sourceNames: Record<ClaimRaw["sourceKind"], string> = {
  none: "No source", asset: "Project asset", url: "URL", project_revision: "Project revision", manual_note: "Manual note",
};
const sourceValue = (raw: ClaimRaw) => raw.sourceKind === "asset" ? raw.assetId
  : raw.sourceKind === "url" ? raw.url : raw.sourceKind === "project_revision" ? raw.projectRevision
    : raw.sourceKind === "manual_note" ? raw.note : "No source attached";
function Comparison({ title, raw }: { title: string; raw: ClaimRaw }) {
  return <section className="claim-comparison"><h4>{title}</h4><dl>
    <dt>Claim text</dt><dd>{raw.text || "(empty)"}</dd>
    <dt>Context</dt><dd>{raw.context || "(empty)"}</dd>
    <dt>{sourceNames[raw.sourceKind]}</dt><dd>{sourceValue(raw) || "(empty)"}</dd>
    <dt>Source version</dt><dd>{raw.pinEnabled ? raw.pin || "(empty value)" : "Not specified"}</dd>
  </dl></section>;
}
export default function ClaimSourceEditor({ project, commit }: { project: Project; commit: Commit }) {
  const owner = JSON.stringify([project.id, project.generation, project.active_branch]);
  const [selections, setSelections] = useState<Record<string, string>>(() => ({ [owner]: project.brief.claims[0]?.id ?? "new" }));
  const firstClaim = project.brief.claims[0]?.id ?? "new";
  useEffect(() => {
    setSelections(previous => Object.hasOwn(previous, owner) ? previous : { ...previous, [owner]: firstClaim });
  }, [owner, firstClaim]);
  const target = selections[owner] ?? firstClaim;
  const setSelected = (value: string) => setSelections(previous => ({ ...previous, [owner]: value }));
  const missing = target !== "new" && !project.brief.claims.some(claim => claim.id === target);
  return <section className="claims-workbench" aria-label="Claims and sources">
    <div className="section-title-row"><div><h3>Claims and sources</h3>
      <p>Keep a claim connected to its source, context and version as the project changes.</p></div>
      <span className="count-label">{project.brief.claims.length} claims</span></div>
    <div className="claim-editor-layout">
      <nav className="claim-list" aria-label="Claim selection">
        <button className="button compact" type="button" aria-pressed={target === "new"} onClick={() => setSelected("new")}>New claim</button>
        {project.brief.claims.map((claim, index) => <button className="claim-choice" type="button" key={claim.id}
          aria-pressed={target === claim.id} aria-label={"Edit claim " + (index + 1) + ": " + claim.text}
          onClick={() => setSelected(claim.id)}>
          <strong>{claim.text}</strong><span>{sourceNames[claim.source?.kind ?? "none"]}</span>
        </button>)}
        {missing && <button className="claim-choice" type="button" aria-pressed="true" onClick={() => setSelected(target)}>
          <strong>Unavailable selected claim</strong><span>Its local draft remains accessible</span></button>}
        {!project.brief.claims.length && <p>No claims recorded. Add one with the context you want to preserve.</p>}
      </nav>
      <ClaimEditForm key={claimScope(project, target)} project={project} target={target} commit={commit} />
    </div>
  </section>;
}
function ClaimEditForm({ project, target, commit }: { project: Project; target: string; commit: Commit }) {
  const editor = useClaimSourceDraft(project, target, commit), { draft, session } = editor, raw = draft.raw;
  const saved = project.brief.claims.find(claim => claim.id === target);
  const captures = (project.production_design?.captures ?? []).filter(capture => capture.claim_id === target);
  return <div className="claim-editor" data-claim-id={target}>
    <div className="section-title-row"><h4>{target === "new" ? "New claim" : "Edit claim"}</h4>
      <span>Base of this edit: r{draft.baseRevision}</span></div>
    {saved && <details className="claim-identity"><summary>Claim identity</summary><code>{saved.id}</code></details>}
    {!saved && target !== "new" && <p role="status">This claim is absent from the current saved branch.
      Its local input remains here for recovery or export; another claim has not replaced the selected draft.</p>}
    <label><span className="field-label">Claim text</span>
      <textarea aria-label="Claim text" rows={3} value={raw.text} onChange={event => editor.edit("text", event.target.value)} /></label>
    <label><span className="field-label">Claim context</span>
      <textarea aria-label="Claim context" rows={3} value={raw.context} onChange={event => editor.edit("context", event.target.value)} /></label>
    <label><span className="field-label">Source type</span>
      <select aria-label="Claim source type" value={raw.sourceKind}
        onChange={event => editor.edit("sourceKind", event.target.value as ClaimRaw["sourceKind"])}>
        {Object.entries(sourceNames).map(([kind, name]) => <option key={kind} value={kind}>{name}</option>)}
      </select></label>
    {raw.sourceKind === "asset" && <label><span className="field-label">Source asset</span>
      <select aria-label="Claim source asset" value={raw.assetId} onChange={event => editor.edit("assetId", event.target.value)}>
        <option value="">Choose an asset</option>
        {raw.assetId && !project.assets.some(asset => asset.id === raw.assetId) && <option value={raw.assetId}>Unavailable asset: {raw.assetId}</option>}
        {project.assets.map(asset => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
      </select></label>}
    {raw.sourceKind === "url" && <label><span className="field-label">Source URL</span>
      <input aria-label="Claim source URL" type="text" value={raw.url} onChange={event => editor.edit("url", event.target.value)} /></label>}
    {raw.sourceKind === "project_revision" && <label><span className="field-label">Source revision</span>
      <input aria-label="Claim source revision" type="text" inputMode="numeric" value={raw.projectRevision}
        onChange={event => editor.edit("projectRevision", event.target.value)} />
      <span className="claim-help">The revision cited by this source. Changing the edit base does not change this citation.</span></label>}
    {raw.sourceKind === "manual_note" && <label><span className="field-label">Source note</span>
      <textarea aria-label="Claim source note" rows={2} value={raw.note} onChange={event => editor.edit("note", event.target.value)} /></label>}
    <label className="claim-version-toggle"><input type="checkbox" checked={raw.pinEnabled}
      onChange={event => editor.edit("pinEnabled", event.target.checked)} />Record a source version</label>
    {raw.pinEnabled && <label><span className="field-label">Source version</span>
      <input aria-label="Claim source version" value={raw.pin} onChange={event => editor.edit("pin", event.target.value)} /></label>}
    {captures.length > 0 && <p className="claim-help">This claim is linked to {captures.length} saved capture record(s).
      Its context and source revision must keep those original links. Other edits remain subject to the saved project rules.</p>}
    {editor.locked && <p role="status">Claim content is locked. Keep drafting here; saving requires the existing content lock to be released.</p>}
    {(draft.intent || editor.conflict) && <div className="claim-comparisons" aria-label="Claim source comparison">
      {editor.conflict && draft.base && <Comparison title="Draft started from" raw={claimRaw(draft.base)} />}
      <Comparison title={"Saved now · r" + project.revision} raw={claimRaw(saved ? claimFields(saved) : null)} />
      <Comparison title="Your proposal" raw={raw} />
    </div>}
    {editor.conflict && <div role="status"><p>The saved project changed. Compare the current claim before choosing this draft's next base.</p>
      <div className="sheet-actions">
        <button className="button compact" type="button" disabled={editor.saving || editor.unknown} onClick={() => editor.choose("saved")}>Keep saved claim</button>
        <button className="button compact" type="button" disabled={editor.saving || editor.unknown} onClick={() => editor.choose("draft")}>Use current claim as edit base</button>
      </div></div>}
    {editor.unknown && <section role="status" aria-label="Unconfirmed claim save">
      <p>Original claim outcome UNKNOWN. {editor.saving ? "Waiting for its original response." : "The attempt and your input are retained; nothing will be resent automatically."}</p>
      <button className="button compact" type="button" disabled={editor.saving || editor.reviewing} onClick={() => void editor.readOutcome()}>Read saved claim and history</button>
      {editor.outcome && <div><p>Read r{editor.outcome.project.revision} and {editor.outcome.events.length} complete history entries.
        Matching values at revisions {editor.outcome.matchingValueRevisions.join(", ") || "none"} do not acknowledge the original request.</p>
        {editor.outcome.project.revision <= draft.pending!.revision && <p>The original request could still commit on this base. Keep it unconfirmed until a later saved revision makes the earlier base obsolete.</p>}
        <button className="button compact" type="button" disabled={editor.saving || editor.outcome.project.revision <= draft.pending!.revision}
          onClick={editor.separateIntent}>Keep original attempt and start a separate edit</button>
      </div>}
    </section>}
    {draft.pending?.outcome === "NOT_SENT" && <div role="status"><p>This attempt was not sent because its local copy could not be confirmed.</p>
      <button className="button compact" type="button" disabled={editor.saving || !!session.storageError}
        onClick={() => editor.choose("draft")}>Keep draft for an explicit new save</button></div>}
    {session.storageError && <div role="alert"><p>{session.storageError}</p>
      <div className="sheet-actions"><button className="button compact" type="button" disabled={editor.saving} onClick={editor.retryBackup}>Retry claim recovery copy</button>
        <button className="button compact" type="button" disabled={editor.saving} onClick={editor.freshCopy}>Keep input in a new local copy</button></div></div>}
    {session.error && <p role="alert">{session.error}</p>}
    {session.notice && <p role="status">{session.notice}</p>}
    {draft.intent && editor.validation && !editor.unknown && <p className="claim-help" role="status">{editor.validation}</p>}
    <div className="sheet-actions">
      <span>{session.custody.expectedRaw && !session.storageError ? "Recoverable in this browser" : "Local input"}
        {" · "}Saving a content change makes resolved reviews on this branch need recheck.</span>
      <button className="button button-primary" type="button" disabled={editor.blocked} onClick={() => void editor.save()}>
        {target === "new" ? "Add claim" : "Save claim changes"}</button>
    </div>
    <details className="claim-recovery"><summary>Claim draft recovery · {session.copies.length} copies</summary>
      <p>Copies retain complete raw input and uncertain attempts. They do not carry permission to change a project.</p>
      <div className="sheet-actions"><button className="button compact" type="button" onClick={() => editor.download()}>Export claim draft</button>
        <label className="button compact">Import claim draft<input type="file" aria-label="Import claim draft" accept="application/json,.json"
          disabled={editor.saving || !!draft.pending} onChange={event => { const file = event.target.files?.[0]; if (file) void editor.importCopy(file); event.currentTarget.value = ""; }} /></label>
        <button className="button compact" type="button" onClick={editor.refresh}>Refresh claim copies</button></div>
      {session.limited && <p role="alert">Only the first 4096 storage keys could be inspected. No hidden copy was removed.</p>}
      {session.copies.map(copy => <article className="claim-copy" key={copy.key}>
        <span>{copy.value ? copy.value.draft.pending ? "Unconfirmed attempt" : "Raw author input" : "Unreadable original copy"} · {copy.writerId.slice(0, 8)}</span>
        {copy.error && <p>{copy.error}</p>}
        <div className="sheet-actions"><button className="button compact" type="button" disabled={editor.saving || !!draft.pending || !copy.value}
          onClick={() => editor.restore(copy)}>Restore claim copy</button>
          <button className="button compact" type="button" onClick={() => editor.download(copy.raw)}>Export original copy</button>
          <button className="button compact" type="button" disabled={editor.saving || copy.writerId === session.custody.writerId}
            onClick={() => editor.discard(copy)}>Remove this local copy</button></div>
      </article>)}
    </details>
    {saved && <ClaimReviews project={project} claim={saved} commit={commit} disabled={editor.saving} />}
  </div>;
}
function ClaimReviews({ project, claim, commit, disabled }: { project: Project; claim: Claim; commit: Commit; disabled: boolean }) {
  const resource = "claim:" + claim.id, editor = useReviewDraft(project, { kind: "comment", id: resource }, commit);
  const reviews = project.reviews.filter(review => review.anchor.resource === resource);
  return <section className="claim-reviews" aria-label="Claim reviews">
    <h4>Review this claim</h4><p>A comment records a review at its saved revision. Resolving it does not verify the claim's truth or approve a delivery.</p>
    <ReviewRecoveryStatus editor={editor} />
    <label><span className="field-label">Claim review comment</span><textarea aria-label="Claim review comment" rows={3}
      value={editor.draft.raw.body} onChange={event => editor.edit("body", event.target.value)} /></label>
    <button className="button compact" type="button" disabled={disabled || editor.blocked || !editor.draft.raw.body.trim()}
      onClick={() => void editor.submit({ type: "add_review", kind: editor.draft.raw.kind, resource,
        body: editor.draft.raw.body.trim(), start: null, end: null, locale: null, profile_id: null })}>Add claim review at r{project.revision}</button>
    {reviews.map(review => <article className="claim-review" key={review.id} data-review-id={review.id}>
      <div className="section-title-row"><strong>{review.status.replaceAll("_", " ").toUpperCase()}</strong><span>Anchored at r{review.anchor.revision}</span></div>
      <p>{review.body}</p>{review.resolution && <p>Resolution: {review.resolution}</p>}
      {review.anchor.branch_id === project.active_branch ? <ReviewResolution project={project} review={review} commit={commit} />
        : <p>Historical review from another branch. Open its original branch to continue it.</p>}
    </article>)}
  </section>;
}
