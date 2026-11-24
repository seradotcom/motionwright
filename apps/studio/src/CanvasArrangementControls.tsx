import { useMemo, useState } from "react";
import { ARRANGEMENT_ACTIONS, arrangementNodeIssue, emptyArrangementSelection } from "./canvasArrangement";
import type { ArrangementAction } from "./canvasArrangement";
import type { ScopedChangeDraft } from "./scopedChangeDraft";
import { stageDraftArrangement, updateDraftArrangement } from "./scopedChangeDraft";
import type { Project, Scene } from "./types";
import "./canvas-arrangement.css";

interface Props {
  draft: ScopedChangeDraft; project: Project; scene: Scene | null;
  disabled: boolean; stale: boolean; initiallyOpen?: boolean;
  onChange: (draft: ScopedChangeDraft) => void;
}
export default function CanvasArrangementControls({ draft, project, scene, disabled, stale, initiallyOpen, onChange }: Props) {
  const selection = draft.arrangement ?? emptyArrangementSelection();
  const [open, setOpen] = useState(Boolean(initiallyOpen || selection.nodeIds.length));
  const [error, setError] = useState<string | null>(null);
  const issue = useMemo(() => {
    try { stageDraftArrangement(draft, project, scene); return null; }
    catch (reason) { return reason instanceof Error ? reason.message : String(reason); }
  }, [draft, project, scene]);
  const choose = (nodeIds: string[], action = selection.action) => {
    setError(null);
    try { onChange(updateDraftArrangement(draft, { action, nodeIds })); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };
  const toggle = (id: string) => choose(selection.nodeIds.includes(id) ? selection.nodeIds.filter(value => value !== id) : [...selection.nodeIds, id]);
  const current = scene?.nodes.find(node => node.id === draft.editor.nodeId);
  const missing = selection.nodeIds.filter(id => !scene?.nodes.some(node => node.id === id));
  return <details className="canvas-arrangement" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Arrange object frames</summary>
    <section aria-label="Canvas arrangement tools">
      <p className="production-help">Align or distribute sibling objects using their saved frames. Animation paths move with each object; timing, curves, scale, rotation and opacity stay intact.</p>
      <p className="production-help">Stage the arrangement, then compare the full scene before applying it. Your selection and other pending edits are kept when you leave this view.</p>
      {draft.nativeSource && <p className="production-help" role="status">This native-frame repair has one fixed target. Start a separate scoped proposal to arrange several objects.</p>}
      <label className="production-field"><span>Arrangement</span><select aria-label="Arrangement operation" value={selection.action} disabled={disabled}
        onChange={event => choose(selection.nodeIds, event.target.value as ArrangementAction)}>
        {Object.entries(ARRANGEMENT_ACTIONS).map(([action, label]) => <option key={action} value={action}>{label}</option>)}
      </select></label>
      <div className="production-button-row">
        <button className="secondary-button" disabled={disabled || !current} onClick={() => choose(scene!.nodes.filter(node =>
          node.parent_id === current!.parent_id && !arrangementNodeIssue(node, scene!)).map(node => node.id))}>Select sibling frames</button>
        <button className="secondary-button" disabled={disabled || !selection.nodeIds.length} onClick={() => choose([])}>Clear frame selection</button>
      </div>
      <div className="canvas-arrangement-selection" role="group" aria-label="Arrangement frame selection">
        {scene?.nodes.map(node => {
          const nodeIssue = arrangementNodeIssue(node, scene);
          const selected = selection.nodeIds.includes(node.id);
          const parent = node.parent_id ? scene.nodes.find(item => item.id === node.parent_id)?.name ?? "Missing parent" : "Scene root";
          return <label key={node.id} className={nodeIssue ? "arrangement-unavailable" : ""}>
            <input id={"arrangement-" + node.id} type="checkbox" checked={selected}
              disabled={disabled || Boolean(nodeIssue && !selected)} data-node-id={node.id}
              onChange={() => toggle(node.id)}/>
            <span><strong>{node.name}</strong><small>{node.kind} · {parent}</small>{nodeIssue && <small>{nodeIssue}</small>}</span>
          </label>;
        })}
        {missing.map(id => <label key={id}><input type="checkbox" checked disabled={disabled} onChange={() => toggle(id)}/>
          <span>Unavailable object <small>{id}</small></span></label>)}
      </div>
      <p className="production-help" role="status">{selection.nodeIds.length} selected frame{selection.nodeIds.length === 1 ? "" : "s"}.</p>
      {(error || issue) && <p className={error || selection.nodeIds.length > 1 ? "production-error" : "production-help"} role="status">{error ?? issue}</p>}
      <button className="secondary-button" disabled={disabled || stale || issue !== null} onClick={() => {
        setError(null);
        try { onChange(stageDraftArrangement(draft, project, scene)); }
        catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
      }}>Stage arrangement</button>
    </section>
  </details>;
}
