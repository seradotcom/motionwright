import { useState } from "react";
import type { Ref } from "react";
import { Download, MessageSquareText, X } from "lucide-react";
import { createAssistantRequest, readAssistantProposal } from "./assistantExchange";
import type { ScopedChangeDraft } from "./scopedChangeDraft";
import type { Project, Scene } from "./types";

interface Props {
  project: Project;
  panelRef?: Ref<HTMLElement>;
  scene: Scene | null;
  selectedNodeId: string | null;
  playhead: number;
  busy: boolean;
  reviewing: boolean;
  onClose: () => void;
  onReview: (draft?: ScopedChangeDraft) => void;
  onOpenReview: (nodeId: string | null) => void;
}

export default function AssistantDock({ project, panelRef, scene, selectedNodeId, playhead, busy, reviewing, onClose, onReview, onOpenReview }: Props) {
  const [scope, setScope] = useState<{ nodeId: string | null }>();
  const [intention, setIntention] = useState("");
  const [rawProposal, setRawProposal] = useState("");
  const [error, setError] = useState<string | null>(null);
  const nodeId = scope ? scope.nodeId : selectedNodeId;
  const scopeAvailable = !!scene?.nodes.length && (nodeId === null || scene.nodes.some(node => node.id === nodeId));
  const changeScope = (value: string) => {
    setScope(value === "selection" ? undefined : { nodeId: value === "scene" ? null : value.slice(7) });
    setError(null);
  };
  const download = () => {
    if (!scene || !scopeAvailable) return;
    setError(null);
    try {
      const raw = createAssistantRequest(project, scene, nodeId, playhead, intention);
      const url = URL.createObjectURL(new Blob([raw], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "motionwright-assistant-request.json";
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };
  const review = () => {
    if (!scene || !scopeAvailable || reviewing) return;
    setError(null);
    try {
      onReview(readAssistantProposal(rawProposal, project, scene, nodeId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  return <aside ref={panelRef} tabIndex={-1} className="inspector assistant-dock" aria-label="Assistant">
    <header className="inspector-heading">
      <h2><MessageSquareText size={14} aria-hidden="true" /> Assistant</h2>
      <button type="button" className="icon-button" onClick={onClose} aria-label="Close assistant"><X size={14} aria-hidden="true" /></button>
    </header>
    {!scene ? <p className="inspector-empty">Select a scene to define the editing context.</p> : <div className="assistant-fields">
      <p className="assistant-source"><strong>{scene.name}</strong><span>Saved revision {project.revision}</span></p>
      {scene.nodes.length > 0 ? <>
        <label className="production-field"><span>Editing scope</span>
          <select value={!scope ? (selectedNodeId ? "selection" : "scene") : scope.nodeId === null ? "scene" : "object:" + scope.nodeId} onChange={event => changeScope(event.target.value)}>
            {selectedNodeId && <option value="selection">Canvas selection · {scene.nodes.find(node => node.id === selectedNodeId)?.name}</option>}
            <option value="scene">All scene objects</option>
            {scope?.nodeId && !scopeAvailable && <option value={"object:" + scope.nodeId}>Object unavailable</option>}
            {scene.nodes.map(node => <option key={node.id} value={"object:" + node.id}>{node.name}</option>)}
          </select>
        </label>
        <label className="production-field"><span>Editing instruction</span>
          <textarea rows={3} maxLength={4000} value={intention} onChange={event => setIntention(event.target.value)} placeholder="Describe the change you want to review." />
        </label>
        <button type="button" className="button" disabled={!scopeAvailable || !intention.trim()} onClick={download}><Download size={14} aria-hidden="true" /> Download context</button>
        <p className="production-help">Use the JSON with a model of your choice. Studio keeps this exchange local.</p>
        <label className="production-field assistant-proposal"><span>Proposal JSON</span>
          <textarea rows={3} maxLength={128 * 1024 + 1} spellCheck={false} value={rawProposal} onChange={event => {
            setRawProposal(event.target.value);
            setError(null);
          }} placeholder="Paste a scoped text or transform proposal." />
        </label>
        <button type="button" className="button button-primary" disabled={busy || reviewing || !scopeAvailable || !rawProposal.trim()} onClick={review}>Review proposal</button>
      </> : <p className="production-help">Add an object in Canvas to prepare a proposal.</p>}
      <div className="production-button-row">
        <button type="button" className="button" disabled={busy || reviewing} onClick={() => onReview()}>Open scoped changes</button>
        <button type="button" className="button" disabled={busy || (nodeId !== null && !scopeAvailable)}
          onClick={() => onOpenReview(nodeId)}>Review comments</button>
      </div>
      {error && <p className="production-error" role="alert">{error}</p>}
    </div>}
  </aside>;
}
