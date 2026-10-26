import BrandCampaignWorkbench from "./BrandCampaignWorkbench";
import type { Project, Change } from "./types";
import type { TastePreference } from "./brandGovernance";
import type { GovernanceRaw } from "./brandAuthoringDraft";
import { BrandDraftFeedback, useBrandAuthoringDraft } from "./useBrandAuthoringDraft";

type Props = { project: Project; commit: (change: Change) => Promise<Project>; busy: boolean };
export default function BrandTasteWorkbench({ project, commit, busy }: Props) {
  const design = project.production_design, stored = design?.brand_profile;
  const editor = useBrandAuthoringDraft(project, "governance", commit, busy);
  const raw = editor.draft.raw as GovernanceRaw, disabled = editor.blocked;
  const patch = (value: Partial<Omit<GovernanceRaw, "kind">>) => editor.edit(current => {
    if (current.kind !== "governance") throw new Error("The selected brand editor changed.");
    return { ...current, ...value };
  });
  const preferences = (update: (value: TastePreference[]) => TastePreference[]) => editor.edit(current => {
    if (current.kind !== "governance") throw new Error("The selected taste editor changed.");
    return { ...current, preferences: update(current.preferences) };
  });
  const missingScene = !!raw.sceneId && !project.scenes.some(scene => scene.id === raw.sceneId);
  const missingRule = !!raw.ruleId && !stored?.rules.some(rule => rule.id === raw.ruleId);
  return <div className="production-plan" aria-label="Brand and taste governance">
    <div className="production-section-title"><div><h2>Brand rules and creative taste</h2>
      <p>Brand constraints are mandatory. Taste is advisory. Campaign exceptions have one scene, one rule and one policy digest.</p></div></div>
    <BrandDraftFeedback editor={editor} project={project}/>
    <section className="production-approval">
      <h3>BrandProfile · enforced in authored scenes</h3>
      <div className="production-plan-grid">
        <label className="production-field"><span>Brand identity</span><input value={raw.brandName} onChange={event => patch({ brandName: event.target.value })}/></label>
        <label className="production-field"><span>Allowed accent colors (#RRGGBB, comma separated)</span><input value={raw.accents} onChange={event => patch({ accents: event.target.value })}/></label>
        <label className="production-field"><span>Required hero wordmark (optional rule)</span><input value={raw.wordmark} onChange={event => patch({ wordmark: event.target.value })}/></label>
        <label className="production-field"><span>Forbidden copy phrases (one per line)</span><textarea rows={4} value={raw.forbidden} onChange={event => patch({ forbidden: event.target.value })}/></label>
      </div>
      <p className="production-help">Saving a changed policy advances its version. Existing exceptions require explicit revocation/re-approval because their policy digest becomes stale. The recorded author is not authenticated by this panel.</p>
      <div className="production-button-row">
        <button className="primary-button" disabled={disabled} onClick={() => void editor.submit("brand")}>Save brand policy</button>
        <button className="secondary-button" disabled={disabled || !design?.brand_exceptions?.length}
          onClick={() => void editor.submit("revoke-all")}>Revoke campaign exceptions</button>
      </div>
    </section>
    <section className="production-approval">
      <h3>Campaign exceptions · scope cannot escape one scene</h3>
      {design?.brand_exceptions?.map(exception => <div className="production-button-row" key={exception.id}>
        <span className="mono">{exception.campaign} · {project.scenes.find(scene => scene.id === exception.scene_id)?.name} · rule {exception.rule_id.slice(0, 8)} · {exception.author}</span>
        <button className="secondary-button" disabled={disabled || !stored}
          onClick={() => void editor.submit({ revoke: exception.id })}>Revoke</button>
      </div>)}
      <div className="production-plan-grid">
        <label className="production-field"><span>Scene scope</span><select value={raw.sceneId} onChange={event => patch({ sceneId: event.target.value })}>
          <option value="">Choose a saved scene</option>
          {missingScene && <option value={raw.sceneId}>Retained unavailable scene · {raw.sceneId}</option>}
          {project.scenes.map(scene => <option key={scene.id} value={scene.id}>{scene.name}</option>)}
        </select></label>
        <label className="production-field"><span>Brand rule</span><select value={raw.ruleId} onChange={event => patch({ ruleId: event.target.value })}>
          <option value="">Choose a saved rule</option>
          {missingRule && <option value={raw.ruleId}>Retained unavailable rule · {raw.ruleId}</option>}
          {stored?.rules.map(rule => <option key={rule.id} value={rule.id}>{rule.kind} · {rule.id.slice(0, 8)}</option>)}
        </select></label>
        <label className="production-field"><span>Campaign</span><input value={raw.campaign} onChange={event => patch({ campaign: event.target.value })}/></label>
        <label className="production-field"><span>Recorded author</span><input value={raw.author} onChange={event => patch({ author: event.target.value })}/></label>
        <label className="production-field"><span>Reason for exception</span><textarea rows={2} value={raw.rationale} onChange={event => patch({ rationale: event.target.value })}/></label>
      </div>
      <button className="secondary-button"
        disabled={disabled || !stored || !raw.sceneId || !raw.ruleId || missingScene || missingRule || !raw.campaign.trim() || !raw.author.trim() || !raw.rationale.trim()}
        onClick={() => void editor.submit("exception")}>Record scene-scoped exception</button>
    </section>
    <section className="production-approval">
      <h3>TasteProfile · preferences cannot override brand rules</h3>
      <label className="production-field"><span>Taste label</span><input value={raw.tasteName} onChange={event => patch({ tasteName: event.target.value })}/></label>
      {raw.preferences.map((preference, index) => <div className="production-plan-grid" key={index}>
        <label className="production-field"><span>Axis</span><input value={preference.axis}
          onChange={event => preferences(values => values.map((value, position) => position === index ? { ...value, axis: event.target.value } : value))}/></label>
        <label className="production-field"><span>Preference</span><input value={preference.preference}
          onChange={event => preferences(values => values.map((value, position) => position === index ? { ...value, preference: event.target.value } : value))}/></label>
        <label><input type="checkbox" checked={preference.inferred}
          onChange={event => preferences(values => values.map((value, position) => position === index ? { ...value, inferred: event.target.checked } : value))}/> Inferred, not reviewed</label>
        <button className="secondary-button" onClick={() => preferences(values => values.filter((_, position) => position !== index))}>Remove</button>
      </div>)}
      <div className="production-button-row">
        <button className="secondary-button" disabled={raw.preferences.length >= 64}
          onClick={() => preferences(values => [...values, { axis: "", preference: "", inferred: true }])}>Add preference</button>
        <button className="primary-button" disabled={disabled} onClick={() => void editor.submit("taste")}>Save taste</button>
      </div>
    </section>
    <section className="production-approval">
      <h3>CreativeDecision · project-scoped record</h3>
      <p className="production-help">A recorded decision documents who claimed a choice and why. It cannot waive a brand rule or approve external asset rights.</p>
      {(design?.project_decisions ?? []).slice(-8).map(value => <p key={value.id}><strong>{value.author}:</strong> {value.decision} <small>({project.scenes.find(scene => scene.id === value.scene_id)?.name})</small></p>)}
      <label className="production-field"><span>Decision</span><textarea rows={2} value={raw.decision} onChange={event => patch({ decision: event.target.value })}/></label>
      <button className="secondary-button" disabled={disabled || !raw.sceneId || missingScene || !raw.author.trim() || !raw.rationale.trim() || !raw.decision.trim()}
        onClick={() => void editor.submit("decision")}>Record decision for selected scene</button>
    </section>
    <BrandCampaignWorkbench project={project} busy={busy} commit={commit}/>
  </div>;
}
