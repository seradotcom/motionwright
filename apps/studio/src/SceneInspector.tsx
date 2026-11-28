import { Clock3, LockKeyhole, Settings2, Unlock } from "lucide-react";
import SceneBeatEditor from "./SceneBeatEditor";
import { useRef, useState } from "react";
import type { Ref } from "react";
import type { Change, Project, RationalTime, RendererKind, Scene } from "./types";
import type { TimecodeMode } from "./timecode";
import { inspectorChange, useInspectorDraft, type InspectorChange, type InspectorField } from "./sceneInspectorDraft";

const rendererAvailable = (project: Project, renderer: RendererKind) => {
  if (renderer === "remotion") {
    return project.extensions.some((extension) => extension.kind === "remotion-renderer" && extension.enabled && extension.rights_status === "cleared");
  }
  if (renderer === "manim-gl") {
    return project.extensions.some((extension) => extension.kind === "manim-gl-renderer" && extension.enabled && extension.rights_status === "cleared");
  }
  return true;
};

interface SceneInspectorProps {
  project: Project; scene: Scene | null;
  panelRef?: Ref<HTMLElement>;
  commit: (change: Change) => Promise<void>;
  commitScene: (change: Change) => Promise<Project>;
  commitBeat: (change: Change) => Promise<Project>;
  rate: RationalTime; mode: TimecodeMode; rendererLabels: Record<RendererKind, string>;
}
export default function SceneInspector(props: SceneInspectorProps) {
  if (!props.scene) return <aside ref={props.panelRef} tabIndex={-1} className="inspector" aria-label="Scene inspector">
    <div className="inspector-heading">Inspector</div>
    <div className="empty-workspace"><Settings2 size={24} /><strong>Nothing selected</strong>
      <span>Select a scene to inspect its semantic state.</span></div>
  </aside>;
  return <SelectedSceneInspector {...props} scene={props.scene} />;
}
function SelectedSceneInspector({ project, scene, commit, commitScene, commitBeat, rate, mode, rendererLabels, panelRef }:
  SceneInspectorProps & { scene: Scene }) {
  const editor = useInspectorDraft(project, scene.id, commitScene);
  const candidate = (field: InspectorField): { change: InspectorChange | null; error: string | null } => {
    if (!editor.draft.dirty[field]) return { change: null, error: null };
    try { return { change: inspectorChange(editor.draft, project, field), error: null }; }
    catch (reason) { return { change: null, error: reason instanceof Error ? reason.message : String(reason) }; }
  };
  const objective = candidate("objective"), duration = candidate("duration");

  const resource = "scene:" + scene.id;
  const locks = project.locks.filter((lock) => lock.resource === resource);
  const timingLocked = locks.some((lock) => lock.kind === "timing");
  const contentLocked = locks.some((lock) => lock.kind === "content");
  const rendererLocked = locks.some(lock => lock.kind === "renderer");
  const rendererSource = JSON.stringify([project.id, project.generation, project.active_branch, project.revision, scene.id]);
  const [rendererChoice, setRendererChoice] = useState<{ source: string; value: RendererKind } | null>(null);
  const [savingRenderer, setSavingRenderer] = useState(false);
  const rendererSelect = useRef<HTMLSelectElement>(null);
  const proposedRenderer = rendererChoice?.source === rendererSource && !rendererLocked
    && rendererAvailable(project, rendererChoice.value) ? rendererChoice.value : null;

  const applyRendererChange = async () => {
    if (!proposedRenderer || savingRenderer) return;
    setSavingRenderer(true);
    setRendererChoice(null);
    try {
      await commit({ type: "set_scene_renderer", scene_id: scene.id, renderer: proposedRenderer });
    } finally {
      setSavingRenderer(false);
    }
  };
  return (
    <aside ref={panelRef} tabIndex={-1} className="inspector" aria-label="Scene inspector">
      <div className="inspector-heading">
        <span>Inspector</span>
        <span className="mono">r{project.revision}</span>
      </div>

      <section className="inspector-section">
        <div className="inspector-section-title">Scene</div>
        <label className="field-label">Name</label>
        <div className="read-field">{scene.name}</div>
        <label className="field-label" htmlFor="renderer">Renderer</label>
        <select
          id="renderer"
          ref={rendererSelect}
          value={proposedRenderer ?? scene.renderer}
          disabled={rendererLocked || savingRenderer}
          onChange={event => {
            const value = event.target.value as RendererKind;
            setRendererChoice(value === scene.renderer ? null : { source: rendererSource, value });
          }}
        >
          {Object.entries(rendererLabels).map(([value, label]) => {
            const renderer = value as RendererKind;
            const available = rendererAvailable(project, renderer);
            return (
              <option key={value} value={value} disabled={!available}>
                {label}{available ? "" : " · opt-in required"}
              </option>
            );
          })}
        </select>
        {proposedRenderer && <section className="renderer-change-review" aria-label="Review renderer change">
          <strong>{rendererLabels[scene.renderer]} to {rendererLabels[proposedRenderer]}</strong>
          <p>Canvas objects and timing stay editable. Review the new renderer's output before using it in a cut.</p>
          {proposedRenderer === "blender" && scene.nodes.some(node => node.kind === "text") &&
            <p>Canvas text needs a separate realization for Blender.</p>}
          <div className="renderer-change-actions">
            <button type="button" className="button compact" onClick={() => void applyRendererChange()}>Apply renderer change</button>
            <button type="button" className="button compact" onClick={() => {
              setRendererChoice(null);
              rendererSelect.current?.focus();
            }}>Cancel renderer change</button>
          </div>
        </section>}
        {rendererLocked && <p className="renderer-note"><LockKeyhole size={13} aria-hidden="true"/>The renderer is locked.</p>}
        <label className="field-label" htmlFor="scene-status">Review state</label>
        <select
          id="scene-status"
          aria-label="Scene status"
          value={scene.status}
          onChange={(event) => commit({
            type: "set_scene_status",
            scene_id: scene.id,
            status: event.target.value as Scene["status"],
          })}
        >
          <option value="draft">Draft</option>
          <option value="review">Review</option>
          <option value="approved">Approved</option>
          <option value="needs_work">Needs work</option>
        </select>
        <label className="field-label" htmlFor="scene-duration">Duration · seconds</label>
        <div className="inline-save-field compact-inline">
          <input
            id="scene-duration"
            aria-label="Scene duration seconds"
            type="text"
            inputMode="decimal"
            value={editor.draft.raw.duration}
            disabled={timingLocked}
            onChange={(event) => editor.edit("duration", event.target.value)}
          />
          <button
            type="button"
            className="button compact"
            disabled={editor.blocked || timingLocked || !duration.change}
            onClick={() => { if (duration.change) void editor.submit(duration.change); }}
          >
            Ripple
          </button>
        </div>
        {duration.error && <p role="status">{duration.error}</p>}
        <div className="renderer-note">
          <Clock3 size={13} />
          Duration accepts exact seconds or fractions. Ripple reflows later scene starts without stretching or inferring voice timing.
        </div>
      </section>

      <section className="inspector-section">
        <div className="inspector-section-title">Objective</div>
        <textarea aria-label="Inspector scene objective" value={editor.draft.raw.objective} onChange={(event) => editor.edit("objective", event.target.value)} rows={5} disabled={contentLocked} />
        <button
          type="button"
          className="button full"
          disabled={editor.blocked || contentLocked || !objective.change}
          onClick={() => { if (objective.change) void editor.submit(objective.change); }}
        >
          Commit objective
        </button>
        {objective.error && <p role="status">{objective.error}</p>}
      </section>

      <section className="inspector-section" aria-label="Inspector draft recovery">
        <p>Objective and duration input is retained for this project, generation, branch and scene.</p>
        {editor.storageError && <p role="alert">{editor.storageError}</p>}
        {editor.error && <p role="alert">{editor.error}</p>}
        {editor.notice && <p role="status">{editor.notice}</p>}
        {editor.saving && <p role="status">Waiting for the original scene reply. Later edits are retained.</p>}
        {editor.stale && !editor.unknown && <div>
          <p>The saved project changed. Review the current scene before retaining this input against r{project.revision}.</p>
          <button type="button" className="button compact" disabled={editor.busy || !!editor.storageError}
            onClick={editor.advance}>Retain Inspector input against current revision</button>
        </div>}
        {editor.unknown && <div role="region" aria-label="Unconfirmed Inspector save">
          <p>The original scene change is UNKNOWN. Matching saved values do not acknowledge it, and no request is replayed.</p>
          <button type="button" className="button compact" disabled={editor.busy || editor.reviewing}
            onClick={() => void editor.readOutcome()}>Read saved scene and history</button>
          {editor.outcome && <div role="region" aria-label="Saved Inspector outcome">
            <p>Saved scene and contiguous history read at r{editor.outcome.project.revision}.
              These observations do not identify or acknowledge the original request.</p>
            <p>Matching change revisions: {editor.outcome.matchingValueRevisions.join(", ") || "none"}.</p>
            <button type="button" className="button compact" disabled={editor.busy || !!editor.storageError}
              onClick={editor.separateIntent}>Keep original attempt and start a separate scene edit</button>
          </div>}
        </div>}
        <div className="inline-actions">
          {editor.storageError && <button type="button" className="button compact" onClick={editor.retryBackup}>Verify Inspector recovery copy</button>}
          <button type="button" className="button compact" onClick={editor.download}>Export Inspector input</button>
        </div>
      </section>

      <section className="inspector-section">
        <SceneBeatEditor
          project={project}
          scene={scene}
          contentLocked={contentLocked}
          timingLocked={timingLocked}
          commit={commitBeat}
          rate={rate}
          mode={mode}
        />
      </section>

      <section className="inspector-section">
        <div className="inspector-section-title">Locks</div>
        {locks.length === 0 ? (
          <p className="inspector-empty">No explicit locks.</p>
        ) : (
          <div className="lock-list">
            {locks.map((lock) => (
              <div className="lock-row" key={lock.id}>
                <LockKeyhole size={13} />
                <div><strong>{lock.kind}</strong><span>{lock.note || "Locked"}</span></div>
                <button
                  type="button"
                  className="plain-icon"
                  aria-label={"Remove " + lock.kind + " lock"}
                  onClick={() => commit({ type: "remove_lock", lock_id: lock.id })}
                >
                  <Unlock size={13} />
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="lock-actions">
          <button type="button" className="button compact" onClick={() => commit({ type: "set_lock", resource, kind: "content", note: "Protected from content edits" })}>
            <LockKeyhole size={13} /> Content
          </button>
          <button type="button" className="button compact" onClick={() => commit({ type: "set_lock", resource, kind: "timing", note: "Protected timing" })}>
            <Clock3 size={13} /> Timing
          </button>
        </div>
      </section>
    </aside>
  );
}
