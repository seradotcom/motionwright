import { CircleDashed, LockKeyhole, Sparkles, Type } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { Change, Project, Scene } from "./types";

type Commit = (change: Change) => Promise<void>;

const lines = (value: string) => value.split("\n").map((item) => item.trim()).filter(Boolean);
const lineText = (value: string[]) => value.join("\n");

export function RichBriefView({ project, commit }: { project: Project; commit: Commit }) {
  const [title, setTitle] = useState(project.title);
  const [objective, setObjective] = useState(project.brief.objective);
  const [audience, setAudience] = useState(project.brief.audience);
  const [constraints, setConstraints] = useState(lineText(project.brief.constraints));
  const [exclusions, setExclusions] = useState(lineText(project.brief.exclusions));

  useEffect(() => {
    setTitle(project.title);
    setObjective(project.brief.objective);
    setAudience(project.brief.audience);
    setConstraints(lineText(project.brief.constraints));
    setExclusions(lineText(project.brief.exclusions));
  }, [project]);

  const titleDirty = title.trim() !== project.title && title.trim().length > 0;
  const briefDirty =
    objective !== project.brief.objective ||
    audience !== project.brief.audience ||
    constraints !== lineText(project.brief.constraints) ||
    exclusions !== lineText(project.brief.exclusions);

  return (
    <div className="workspace-scroll document-view">
      <header className="workspace-heading">
        <div>
          <h2>Project brief</h2>
          <p>Audience, objective and constraints are versioned project state. They are not hidden prompt context.</p>
        </div>
        <span className={"status-pill status-" + project.state}>{project.state.toUpperCase()}</span>
      </header>
      <div className="document-grid">
        <section className="document-main">
          <label className="field-label" htmlFor="project-title-rich">Project title</label>
          <div className="inline-save-field">
            <input id="project-title-rich" value={title} onChange={(event) => setTitle(event.target.value)} />
            <button className="button button-primary" type="button" disabled={!titleDirty}
              onClick={() => commit({ type: "rename_project", title: title.trim() })}>Save title</button>
          </div>

          <div className="form-stack">
            <label>
              <span className="field-label">Production objective</span>
              <textarea aria-label="Production objective" rows={5} value={objective} onChange={(event) => setObjective(event.target.value)} />
            </label>
            <label>
              <span className="field-label">Audience</span>
              <textarea aria-label="Audience" rows={3} value={audience} onChange={(event) => setAudience(event.target.value)} />
            </label>
            <div className="two-column-fields">
              <label>
                <span className="field-label">Constraints · one per line</span>
                <textarea aria-label="Constraints" rows={6} value={constraints} onChange={(event) => setConstraints(event.target.value)} />
              </label>
              <label>
                <span className="field-label">Exclusions · one per line</span>
                <textarea aria-label="Exclusions" rows={6} value={exclusions} onChange={(event) => setExclusions(event.target.value)} />
              </label>
            </div>
            <div className="sheet-actions">
              <span>{project.brief.claims.length} evidence-linked claims</span>
              <button className="button button-primary" type="button" disabled={!briefDirty}
                onClick={() => commit({
                  type: "set_brief",
                  objective,
                  audience,
                  constraints: lines(constraints),
                  exclusions: lines(exclusions),
                })}>Commit brief</button>
            </div>
          </div>
        </section>
        <aside className="document-side">
          <h3>Project authority</h3>
          <div className="brief-facts vertical">
            <div><span>Revision</span><strong>r{project.revision}</strong></div>
            <div><span>Claims</span><strong>{project.brief.claims.length}</strong></div>
            <div><span>Model receipts</span><strong>{project.model_invocations.length}</strong></div>
          </div>
          <div className="evidence-callout compact-callout">
            <CircleDashed size={17} />
            <div><strong>No invisible approval</strong><span>Missing evidence stays explicit until a source is attached.</span></div>
          </div>
        </aside>
      </div>
    </div>
  );
}

export function RichNarrativeView({ project, scene, commit }: { project: Project; scene: Scene | null; commit: Commit }) {
  const [premise, setPremise] = useState(project.narrative.premise);
  const [objective, setObjective] = useState(scene?.objective ?? "");

  useEffect(() => setPremise(project.narrative.premise), [project.narrative.premise]);
  useEffect(() => setObjective(scene?.objective ?? ""), [scene?.id, scene?.objective]);

  if (!scene) {
    return (
      <div className="empty-workspace">
        <Type size={24} />
        <strong>Select a scene</strong>
        <span>Narrative editing is bound to persistent scene identity.</span>
      </div>
    );
  }

  return (
    <div className="workspace-scroll narrative-view">
      <header className="workspace-heading">
        <div>
          <h2>Narrative</h2>
          <p>The project premise and selected-scene objective remain semantic intent, independent of renderer code.</p>
        </div>
        <span className={"status-pill status-" + scene.status}>{scene.status.replace("_", " ").toUpperCase()}</span>
      </header>
      <div className="narrative-columns">
        <section className="narrative-sheet">
          <span className="sheet-label">Project premise</span>
          <textarea aria-label="Narrative premise" rows={8} value={premise} onChange={(event) => setPremise(event.target.value)} />
          <div className="sheet-actions">
            <span>{project.narrative.beats.length} narrative beats</span>
            <button type="button" className="button button-primary"
              disabled={premise === project.narrative.premise}
              onClick={() => commit({ type: "set_narrative_premise", premise })}>Commit premise</button>
          </div>
        </section>
        <section className="narrative-sheet">
          <span className="sheet-label">Selected scene objective</span>
          <h3>{scene.name}</h3>
          <textarea aria-label="Scene objective" rows={8} value={objective} onChange={(event) => setObjective(event.target.value)} />
          <div className="sheet-actions">
            <span>{objective.length} / 4000</span>
            <button type="button" className="button button-primary"
              disabled={!objective.trim() || objective === scene.objective}
              onClick={() => commit({ type: "update_scene_objective", scene_id: scene.id, objective: objective.trim() })}>Commit objective</button>
          </div>
        </section>
      </div>
    </div>
  );
}

export function RichAlternativesView({ project, scene, commit }: { project: Project; scene: Scene | null; commit: Commit }) {
  const proposalSet = useMemo(() => {
    if (!scene) return null;
    return [...project.proposal_sets].reverse().find((set) => set.scope.kind === "scene" && set.scope.scene_id === scene.id) ?? null;
  }, [project.proposal_sets, scene]);

  if (!scene) {
    return (
      <div className="empty-workspace">
        <Sparkles size={24} />
        <strong>No comparison target</strong>
        <span>Select a scene before comparing structural alternatives.</span>
      </div>
    );
  }

  if (!proposalSet) {
    return (
      <div className="empty-workspace">
        <Sparkles size={24} />
        <strong>No proposals for this scene</strong>
        <span>Motionwright will not fabricate alternatives. A planner or external agent must add a versioned proposal set first.</span>
      </div>
    );
  }

  const stale = proposalSet.base_revision + 1 !== project.revision;
  return (
    <div className="workspace-scroll alternatives-view">
      <header className="workspace-heading">
        <div>
          <h2>Alternatives</h2>
          <p>These are stored structural proposals with an explicit search budget and base revision.</p>
        </div>
        <span className={stale ? "status-pill status-stale" : "revision-chip"}>
          {stale ? "STALE SET" : "base r" + proposalSet.base_revision}
        </span>
      </header>
      <div className="proposal-budget">
        <span>Candidate budget <strong>{proposalSet.search_budget.candidates}</strong></span>
        <span>Model calls <strong>{proposalSet.search_budget.model_calls}</strong></span>
        <span>Token ceiling <strong>{proposalSet.search_budget.max_tokens}</strong></span>
      </div>
      <div className="alternatives-grid">
        {proposalSet.proposals.map((proposal, index) => {
          const selected = proposalSet.selected === proposal.id;
          return (
            <article className={"alternative stored-alternative" + (selected ? " selected" : "")} key={proposal.id}>
              <div className="alternative-head">
                <span className="alternative-id">{String.fromCharCode(65 + index)}</span>
                {selected && <span className="selected-label">Selected</span>}
              </div>
              <h3>{proposal.title}</h3>
              <p className="alternative-structure">{proposal.structure.join(" → ")}</p>
              <p>{proposal.rationale}</p>
              <div className="proposal-footer">
                <span>{proposal.edits.length} typed edit{proposal.edits.length === 1 ? "" : "s"}</span>
                <button type="button" className="button compact"
                  disabled={stale || selected}
                  onClick={() => commit({ type: "select_proposal", proposal_set_id: proposalSet.id, proposal_id: proposal.id })}>
                  {selected ? "Selected" : "Select for review"}
                </button>
              </div>
            </article>
          );
        })}
      </div>
      <div className="evidence-callout">
        <LockKeyhole size={18} />
        <div><strong>Selection is not execution</strong><span>Choosing a proposal records intent. Its edits still pass through normal locks, CAS and transaction checks.</span></div>
      </div>
    </div>
  );
}
