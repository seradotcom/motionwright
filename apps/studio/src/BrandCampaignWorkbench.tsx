import { useEffect, useState } from "react";
import { GitCompareArrows, ShieldAlert } from "lucide-react";
import type { Project, Change } from "./types";
import type { CampaignRaw } from "./brandAuthoringDraft";
import { BrandDraftFeedback, useBrandAuthoringDraft } from "./useBrandAuthoringDraft";
import "./brand-campaign.css";

const desktop = () => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
export default function BrandCampaignWorkbench({
  project, commit, busy,
}: { project: Project; commit: (change: Change) => Promise<Project>; busy: boolean }) {
  const profile = project.production_design?.brand_profile, heroes = project.production_design?.heroes ?? [];
  const palette = profile?.rules.find(rule => rule.kind === "allowed_accents");
  const mark = profile?.rules.find(rule => rule.kind === "required_wordmark");
  const colors = palette?.kind === "allowed_accents" ? palette.colors : [];
  const required = mark?.kind === "required_wordmark" ? mark.text : null;
  const editor = useBrandAuthoringDraft(project, "campaign", commit, busy);
  const raw = editor.draft.raw as CampaignRaw, proposal = editor.draft.proposal;
  // A retained preview is not a retained owner confirmation. Reload, source
  // changes and any subsequent author input require an explicit new check.
  const [ackFinal, setAckFinal] = useState(false);
  useEffect(() => { setAckFinal(false); }, [editor.draft.scope, editor.draft.sequence, proposal?.id, project]);
  const patch = (value: Partial<Omit<CampaignRaw, "kind">>) => {
    setAckFinal(false);
    editor.edit(current => {
      if (current.kind !== "campaign") throw new Error("The selected campaign editor changed.");
      return { ...current, ...value };
    });
  };
  const changeScenes = (id: string, checked: boolean) => {
    setAckFinal(false);
    editor.edit(current => {
      if (current.kind !== "campaign") throw new Error("The selected campaign editor changed.");
      return { ...current, sceneIds: checked
        ? current.sceneIds.includes(id) || current.sceneIds.length >= 8 ? current.sceneIds : [...current.sceneIds, id]
        : current.sceneIds.filter(value => value !== id) };
    });
  };
  const availableScenes = new Set(heroes.filter(hero => project.scenes.some(scene => scene.id === hero.scene_id)).map(hero => hero.scene_id));
  const missingScenes = raw.sceneIds.filter(id => !availableScenes.has(id));
  const retainedAccent = !!raw.accent && !colors.includes(raw.accent);
  const canApply = editor.proposalCurrent && !editor.blocked && ackFinal && desktop();
  const apply = () => { if (canApply) void editor.submit("campaign"); };
  return <section className="brand-campaign" aria-label="Campaign brand rollout review">
    <header className="production-section-title">
      <GitCompareArrows size={17} aria-hidden="true"/>
      <div><h3>Propagate brand to selected scenes</h3>
        <p>One owner-reviewed project revision, preserving compatible manual edits.</p>
      </div>
    </header>
    <p className="production-help">
      This updates only already-attached semantic Hero components.
      It never replaces unrelated Canvas objects, imported footage, audio, provider assets,
      other campaign projects or renderer output. Every affected scene must accept its
      existing three-way human-override checks.
    </p>
    <BrandDraftFeedback editor={editor} project={project}/>
    {!profile && <p className="production-notice" role="status">
      Save a versioned BrandProfile above before reviewing a campaign rollout. Retained input and proposals remain available here.
    </p>}
    {profile && <div className="brand-campaign-source">
      <span><strong>{profile.label}</strong> · policy v{profile.version}</span>
      <span>source r{project.revision} · {heroes.length} attached component(s)</span>
    </div>}
    <div className="production-plan-grid">
      <label className="production-field"><span>Campaign / creative batch label</span>
        <input aria-label="Brand rollout campaign" maxLength={160} value={raw.campaign}
          onChange={event => patch({ campaign: event.target.value })}/></label>
      <label className="production-field"><span>Owner declaration (not independently authenticated)</span>
        <input aria-label="Brand rollout owner" maxLength={160} value={raw.owner}
          onChange={event => patch({ owner: event.target.value })}/></label>
    </div>
    <div className="production-plan-grid">
      <label className="production-field"><span>One saved allowed accent to apply</span>
        <select aria-label="Brand rollout accent" disabled={!colors.length && !retainedAccent}
          value={raw.accent} onChange={event => patch({ accent: event.target.value })}>
          <option value="">Keep each existing accent</option>
          {retainedAccent && <option value={raw.accent}>Retained accent · {raw.accent} (not in current policy)</option>}
          {colors.map(color => <option value={color} key={color}>{color}</option>)}
        </select></label>
      <label className="brand-campaign-boolean">
        <input type="checkbox" aria-label="Propagate mandatory brand wordmark"
          disabled={!required && !raw.applyWordmark} checked={raw.applyWordmark}
          onChange={event => patch({ applyWordmark: event.target.checked })}/>
        <span>Enforce current mandatory wordmark <strong>{required ?? "No required wordmark rule"}</strong></span>
      </label>
    </div>
    <fieldset className="brand-campaign-scenes">
      <legend>Select up to eight existing editable scenes</legend>
      {heroes.map(hero => {
        const scene = project.scenes.find(item => item.id === hero.scene_id);
        if (!scene) return null;
        return <label key={hero.id}><input type="checkbox" checked={raw.sceneIds.includes(scene.id)}
          disabled={!raw.sceneIds.includes(scene.id) && raw.sceneIds.length >= 8}
          onChange={event => changeScenes(scene.id, event.target.checked)}/>
          <span><strong>{scene.name}</strong> · {hero.config.accent} · {hero.config.wordmark} · {scene.status}</span></label>;
      })}
      {missingScenes.map(id => <label key={id}><input type="checkbox" checked onChange={() => changeScenes(id, false)}/>
        <span>Retained unavailable scene · {id}. Its selection has not been replaced.</span></label>)}
      {!heroes.length && <p className="production-help">Attach editable Hero components first; unbound scenes cannot be silently rewritten.</p>}
    </fieldset>
    <label className="production-field"><span>Reason for this exact campaign propagation</span>
      <textarea aria-label="Brand rollout rationale" maxLength={1200} rows={2}
        value={raw.why} onChange={event => patch({ why: event.target.value })}/></label>
    <label className="production-check brand-campaign-boolean">
      <input type="checkbox" checked={raw.ackRebuild} onChange={event => patch({ ackRebuild: event.target.checked })}/>
      I understand existing Hero components will be rebuilt through the normal
      conflict-aware semantic update; protected human edits may refuse the entire operation.
    </label>
    <button type="button" className="primary-button"
      disabled={editor.blocked || !profile || !raw.ackRebuild || !raw.sceneIds.length}
      onClick={() => { setAckFinal(false); void editor.preview(); }}>
      {editor.session.preparing ? "Inspecting source policy…" : "Review per-scene brand changes"}
    </button>
    {proposal && <section className="brand-campaign-review" aria-label="Proposed brand campaign changes">
      <header><strong>{proposal.preview.changed_count} component(s) would change</strong>
        <span>Source SHA-256 {proposal.preview.brand_sha256.slice(0, 16)}… · r{proposal.preview.revision}</span></header>
      {!editor.proposalCurrent && <p className="production-notice" role="status">
        Retained proposal from r{proposal.sourceRevision}. Its rows and parameters are unchanged.
        Review the current source and input before any new application.
      </p>}
      <dl className="production-help">
        <dt>Reviewed campaign</dt><dd>{proposal.rollout.campaign}</dd>
        <dt>Recorded owner</dt><dd>{proposal.rollout.reviewer_declaration}</dd>
        <dt>Reviewed reason</dt><dd>{proposal.rollout.rationale}</dd>
      </dl>
      <table><thead><tr><th>Scene</th><th>Accent</th><th>Wordmark</th><th>Effect</th></tr></thead>
        <tbody>{proposal.preview.rows.map(row => <tr key={row.scene_id}>
          <th>{row.scene_name}</th>
          <td>{row.from_accent} → {row.to_accent}</td>
          <td>{row.from_wordmark} → {row.to_wordmark}</td>
          <td>{row.changed ? "Updates existing component" : "Unchanged"}</td>
        </tr>)}</tbody></table>
      <div className="production-notice">
        <ShieldAlert size={16} aria-hidden="true"/>
        <span>Preview cannot certify aesthetic consistency, ownership or native video.
          A late lock, manual override conflict or policy change rejects the whole CAS.
          Accepted changes receive scene-scoped decision records with source policy SHA.
        </span>
      </div>
      <label className="production-check brand-campaign-boolean">
        <input type="checkbox" checked={ackFinal} disabled={editor.blocked || !editor.proposalCurrent}
          onChange={event => setAckFinal(event.target.checked)}/>
        I reviewed the exact rows, campaign, brand digest and human-override consequences.
        Submit one atomic project revision; never overwrite the other scenes on partial failure.
      </label>
      <div className="production-button-row">
        <button className="primary-button" type="button" disabled={!canApply} onClick={apply}>
          {editor.session.saving ? "Submitting atomic CAS…" : "Apply owner-reviewed campaign via project CAS"}
        </button>
        <button className="secondary-button" type="button" disabled={editor.working || !!editor.draft.pending}
          onClick={() => { setAckFinal(false); editor.discardProposal(); }}>Discard brand proposal</button>
      </div>
      {!desktop() && <p className="production-help" role="status">Browser demo can inspect policy differences but cannot commit campaign changes.</p>}
    </section>}
  </section>;
}
