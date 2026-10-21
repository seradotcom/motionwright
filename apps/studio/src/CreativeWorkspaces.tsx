import { LockKeyhole, Sparkles } from "lucide-react";
import { useMemo } from "react";
import ModelContextInspector from "./ModelContextInspector";
import type { Change, Project, Scene } from "./types";

type Commit = (change: Change) => Promise<void>;

export { default as RichBriefView } from "./BriefWorkspace";

export { default as RichNarrativeView } from "./NarrativeWorkspace";

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
      <div className="workspace-scroll alternatives-view">
        <header className="workspace-heading">
          <div>
            <h2>Alternatives</h2>
            <p>Model context is inspectable even when the creative path remains fully manual.</p>
          </div>
        </header>
        <ModelContextInspector project={project} scene={scene} />
        <div className="alternatives-empty-inline">
          <Sparkles size={20} />
          <strong>No proposals for this scene</strong>
          <span>Motionwright will not fabricate alternatives. Manual editing and external-agent authoring remain available without a model subscription.</span>
        </div>
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
      <ModelContextInspector project={project} scene={scene} />
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
