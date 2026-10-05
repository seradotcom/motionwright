import {
  AlignLeft,
  Box,
  Boxes,
  Braces,
  ChevronDown,
  ChevronUp,
  CircleCheck,
  CircleDashed,
  Clock3,
  Columns3,
  FileOutput,
  Film,
  GitBranch,
  Layers3,
  LockKeyhole,
  MessageSquareText,
  Moon,
  MoreHorizontal,
  MoveRight,
  PanelLeftClose,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  SlidersHorizontal,
  Sparkles,
  Sun,
  Type,
  Unlock,
  Wand2,
  Workflow,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { applyChange, bootstrap } from "./api";
import type {
  Bootstrap,
  Change,
  Project,
  ProjectState,
  RendererKind,
  Scene,
} from "./types";
import { seconds } from "./types";

type Workspace =
  | "Brief"
  | "Narrative"
  | "Storyboard"
  | "Canvas"
  | "Timeline"
  | "Alternatives"
  | "Changes"
  | "Dependencies"
  | "Review"
  | "Deliver";

const workspaces: Array<{ name: Workspace; icon: typeof Film }> = [
  { name: "Brief", icon: AlignLeft },
  { name: "Narrative", icon: Type },
  { name: "Storyboard", icon: Columns3 },
  { name: "Canvas", icon: Box },
  { name: "Timeline", icon: Film },
  { name: "Alternatives", icon: Sparkles },
  { name: "Changes", icon: GitBranch },
  { name: "Dependencies", icon: Workflow },
  { name: "Review", icon: MessageSquareText },
  { name: "Deliver", icon: FileOutput },
];

const rendererLabels: Record<RendererKind, string> = {
  "motion-canvas": "Motion Canvas",
  mlt: "MLT",
  blender: "Blender",
  "manim-community": "Manim Community",
  remotion: "Remotion",
  "manim-gl": "ManimGL",
};

function formatTime(value: number) {
  const mins = Math.floor(value / 60);
  const secs = Math.floor(value % 60);
  const frames = Math.floor((value % 1) * 24);
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}:${String(frames).padStart(2, "0")}`;
}

function stateLabel(state: ProjectState) {
  return state.toUpperCase();
}

function StatusPill({ state }: { state: ProjectState | Scene["status"] }) {
  const label = state.replace("_", " ").toUpperCase();
  return <span className={"status-pill status-" + state}>{label}</span>;
}

function IconButton({
  label,
  onClick,
  children,
  disabled = false,
}: {
  label: string;
  onClick?: () => void;
  children: React.ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      className="icon-button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      type="button"
    >
      {children}
    </button>
  );
}

function PreviewSurface({
  scene,
  project,
  playhead,
}: {
  scene: Scene | null;
  project: Project;
  playhead: number;
}) {
  const relative = scene ? Math.max(0, Math.min(seconds(scene.duration), playhead - seconds(scene.start))) : 0;

  return (
    <section className="preview-wrap" aria-label="Program preview">
      <div className="preview-toolbar">
        <div className="preview-mode">
          <span className="preview-dot" />
          Program
          <span className="toolbar-divider" />
          <span className="muted">Design representation</span>
        </div>
        <div className="timecode">{formatTime(playhead)}</div>
      </div>
      <div className="preview-stage">
        <div className="safe-frame">
          <div className="frame-meta">
            <span>{scene?.name ?? "No scene selected"}</span>
            <span>{scene ? rendererLabels[scene.renderer] : "—"}</span>
          </div>
          {scene ? (
            <>
              <div className="preview-copy">
                <span className="scene-kicker">SCENE {project.scenes.indexOf(scene) + 1}</span>
                <h2>{scene.name}</h2>
                <p>{scene.objective || "No objective set."}</p>
              </div>
              <div className="semantic-diagram" aria-label="Semantic composition representation">
                <div className="semantic-node strong">Intent</div>
                <MoveRight size={16} aria-hidden="true" />
                <div className="semantic-node">Native SDK</div>
                <MoveRight size={16} aria-hidden="true" />
                <div className="semantic-node accent">{rendererLabels[scene.renderer]}</div>
              </div>
              <div className="preview-progress" aria-hidden="true">
                <span
                  style={{
                    width: `${Math.max(4, (relative / Math.max(seconds(scene.duration), 1)) * 100)}%`,
                  }}
                />
              </div>
            </>
          ) : (
            <div className="empty-stage">
              <Film size={26} />
              <strong>Select a scene</strong>
              <span>Its semantic preview and revision state will appear here.</span>
            </div>
          )}
        </div>
      </div>
      <footer className="preview-footer">
        <span>1920 × 1080</span>
        <span>24 fps</span>
        <span className="preview-evidence">
          <CircleDashed size={13} /> No render evidence attached
        </span>
      </footer>
    </section>
  );
}

function StoryboardView({
  project,
  selectedSceneId,
  onSelect,
}: {
  project: Project;
  selectedSceneId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="workspace-scroll storyboard-view">
      <header className="workspace-heading">
        <div>
          <h2>Storyboard</h2>
          <p>Persistent scene identity, timing and realization choice.</p>
        </div>
        <span className="count-label">{project.scenes.length} scenes</span>
      </header>
      <div className="storyboard-strip">
        {project.scenes.map((scene, index) => (
          <button
            key={scene.id}
            type="button"
            className={"story-card " + (scene.id === selectedSceneId ? "selected" : "")}
            onClick={() => onSelect(scene.id)}
          >
            <div className="story-frame">
              <span>{String(index + 1).padStart(2, "0")}</span>
              <div className="story-frame-mark">{scene.name.slice(0, 1)}</div>
            </div>
            <div className="story-copy">
              <div className="story-title-row">
                <strong>{scene.name}</strong>
                <StatusPill state={scene.status} />
              </div>
              <p>{scene.objective || "No objective yet."}</p>
              <div className="story-meta">
                <span>{formatTime(seconds(scene.duration))}</span>
                <span>{rendererLabels[scene.renderer]}</span>
              </div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function BriefView({
  project,
  commit,
}: {
  project: Project;
  commit: (change: Change) => Promise<void>;
}) {
  const [title, setTitle] = useState(project.title);
  useEffect(() => setTitle(project.title), [project.title]);
  const dirty = title.trim() !== project.title && title.trim().length > 0;

  return (
    <div className="workspace-scroll document-view">
      <header className="workspace-heading">
        <div>
          <h2>Project brief</h2>
          <p>Product truth and production constraints belong to the project, not to a prompt transcript.</p>
        </div>
        <StatusPill state={project.state} />
      </header>
      <div className="document-grid">
        <section className="document-main">
          <label className="field-label" htmlFor="project-title">Project title</label>
          <div className="inline-save-field">
            <input id="project-title" value={title} onChange={(event) => setTitle(event.target.value)} />
            <button
              type="button"
              className="button button-primary"
              disabled={!dirty}
              onClick={() => commit({ type: "rename_project", title: title.trim() })}
            >
              Save title
            </button>
          </div>

          <h3>Production objective</h3>
          <p className="long-copy">
            Build a maintainable audiovisual project where narrative, scene identity, renderer choice,
            review state and deliverables remain connected through repeated revisions.
          </p>

          <div className="brief-facts">
            <div><span>Project revision</span><strong>r{project.revision}</strong></div>
            <div><span>Generation</span><strong className="mono">{project.generation.slice(0, 8)}…</strong></div>
            <div><span>Active branch</span><strong>{project.branches.find((branch) => branch.id === project.active_branch)?.name ?? "Unknown"}</strong></div>
          </div>
        </section>
        <aside className="document-side">
          <h3>Constraints</h3>
          <ul className="constraint-list">
            <li><CircleCheck size={15} /> Native SDK transaction boundary</li>
            <li><CircleCheck size={15} /> Revision-safe mutations</li>
            <li><CircleCheck size={15} /> Human and agent share one project state</li>
            <li><CircleDashed size={15} /> Render evidence not attached in this project</li>
          </ul>
        </aside>
      </div>
    </div>
  );
}

function NarrativeView({
  scene,
  commit,
}: {
  scene: Scene | null;
  commit: (change: Change) => Promise<void>;
}) {
  const [objective, setObjective] = useState(scene?.objective ?? "");
  useEffect(() => setObjective(scene?.objective ?? ""), [scene?.id, scene?.objective]);

  if (!scene) {
    return <EmptyWorkspace icon={Type} title="Select a scene" body="Narrative editing is bound to persistent scene identity." />;
  }

  const dirty = objective !== scene.objective && objective.trim().length > 0;
  return (
    <div className="workspace-scroll narrative-view">
      <header className="workspace-heading">
        <div>
          <h2>Narrative</h2>
          <p>Edit purpose before realization. Renderer-specific details stay downstream.</p>
        </div>
        <StatusPill state={scene.status} />
      </header>
      <div className="narrative-sheet">
        <span className="sheet-label">Scene objective</span>
        <h3>{scene.name}</h3>
        <textarea
          value={objective}
          onChange={(event) => setObjective(event.target.value)}
          rows={7}
          aria-label="Scene objective"
        />
        <div className="sheet-actions">
          <span>{objective.length} / 4000</span>
          <button
            type="button"
            className="button button-primary"
            disabled={!dirty}
            onClick={() => commit({
              type: "update_scene_objective",
              scene_id: scene.id,
              objective: objective.trim(),
            })}
          >
            Commit objective
          </button>
        </div>
      </div>
    </div>
  );
}

function AlternativesView({ scene }: { scene: Scene | null }) {
  const [choice, setChoice] = useState<"A" | "B" | "C">("B");
  if (!scene) {
    return <EmptyWorkspace icon={Sparkles} title="No comparison target" body="Select a scene before generating or comparing structural alternatives." />;
  }
  const alternatives = [
    { id: "A" as const, title: "Direct proof", structure: "Problem → semantic control → persisted revision", note: "Fastest comprehension; low visual detour." },
    { id: "B" as const, title: "Contrast cut", structure: "Pixel fragility ↔ native semantics ↔ artifact truth", note: "Strongest mechanism contrast for this scene." },
    { id: "C" as const, title: "Artifact trail", structure: "Intent → graph → renderer → readback → revision", note: "Best provenance story; denser technical load." },
  ];
  return (
    <div className="workspace-scroll alternatives-view">
      <header className="workspace-heading">
        <div>
          <h2>Alternatives</h2>
          <p>Structural proposals are separate until explicitly selected. These are planning proposals, not rendered evidence.</p>
        </div>
        <span className="count-label">A / B / C synchronized</span>
      </header>
      <div className="alternatives-grid">
        {alternatives.map((alternative) => (
          <button
            type="button"
            key={alternative.id}
            className={"alternative " + (choice === alternative.id ? "selected" : "")}
            onClick={() => setChoice(alternative.id)}
          >
            <div className="alternative-head">
              <span className="alternative-id">{alternative.id}</span>
              {choice === alternative.id && <span className="selected-label">Selected for review</span>}
            </div>
            <h3>{alternative.title}</h3>
            <p className="alternative-structure">{alternative.structure}</p>
            <p>{alternative.note}</p>
          </button>
        ))}
      </div>
      <p className="honesty-note">
        Selection here is local comparison state. No project mutation is claimed until a change set is committed.
      </p>
    </div>
  );
}

function ChangesView({ project }: { project: Project }) {
  return (
    <div className="workspace-scroll table-view">
      <header className="workspace-heading">
        <div>
          <h2>Changes</h2>
          <p>Branches have explicit bases. Restores create new changes instead of rewriting history.</p>
        </div>
        <span className="revision-chip">r{project.revision}</span>
      </header>
      <div className="data-table" role="table" aria-label="Project branches">
        <div className="data-row data-head" role="row">
          <span>Branch</span><span>Base</span><span>Head</span><span>State</span>
        </div>
        {project.branches.map((branch) => (
          <div className="data-row" role="row" key={branch.id}>
            <span><GitBranch size={14} /> {branch.name}</span>
            <span className="mono">r{branch.base_revision}</span>
            <span className="mono">{branch.id === project.active_branch ? "r" + project.revision : "—"}</span>
            <span>{branch.id === project.active_branch ? <span className="status-pill status-current">ACTIVE</span> : <span className="muted">stored base</span>}</span>
          </div>
        ))}
      </div>
      <div className="evidence-callout">
        <Braces size={18} />
        <div>
          <strong>Event journal is application-owned</strong>
          <span>The current UI does not fabricate historical rows that have not been loaded from storage.</span>
        </div>
      </div>
    </div>
  );
}

function DependenciesView({ project }: { project: Project }) {
  return (
    <div className="workspace-scroll table-view">
      <header className="workspace-heading">
        <div>
          <h2>Dependencies</h2>
          <p>Local projections are visible here; canonical CURRENT / STALE / UNKNOWN requires Project Graph evidence.</p>
        </div>
        <span className="status-pill status-unknown">UNKNOWN</span>
      </header>
      <div className="data-table" role="table" aria-label="Project dependencies">
        <div className="data-row dependency-row data-head" role="row">
          <span>Source</span><span>Consumer</span><span>Projection</span><span>Canonical state</span>
        </div>
        {project.assets.map((asset, index) => (
          <div className="data-row dependency-row" role="row" key={asset.id}>
            <span>{asset.name}</span>
            <span>{project.scenes[index % Math.max(project.scenes.length, 1)]?.name ?? "Project"}</span>
            <span>{asset.source_revision ?? "unversioned"}</span>
            <span className="status-pill status-unknown">NOT ADMITTED</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ReviewView({ project, scene }: { project: Project; scene: Scene | null }) {
  const sceneLocks = scene ? project.locks.filter((lock) => lock.resource === "scene:" + scene.id) : [];
  return (
    <div className="workspace-scroll review-view">
      <header className="workspace-heading">
        <div>
          <h2>Review</h2>
          <p>Technical evidence and creative critique remain separate channels.</p>
        </div>
      </header>
      <div className="review-columns">
        <section>
          <span className="section-label">Technical</span>
          <div className="review-entry">
            <CircleDashed size={18} />
            <div>
              <strong>Effect Conformance</strong>
              <span>No canonical report attached to this revision.</span>
            </div>
            <span className="status-pill status-unknown">UNKNOWN</span>
          </div>
          <div className="review-entry">
            <LockKeyhole size={18} />
            <div>
              <strong>Resource locks</strong>
              <span>{sceneLocks.length ? sceneLocks.map((lock) => lock.kind).join(", ") : "No locks on selected scene."}</span>
            </div>
          </div>
        </section>
        <section>
          <span className="section-label">Creative</span>
          <div className="critique-copy">
            <strong>{scene?.name ?? "Select a scene"}</strong>
            <p>{scene ? "Creative notes can recommend changes, but cannot mint a technical PASS or bypass locks." : "Scene-bound critique appears here."}</p>
          </div>
        </section>
      </div>
    </div>
  );
}

function DeliverView({ project }: { project: Project }) {
  return (
    <div className="workspace-scroll deliver-view">
      <header className="workspace-heading">
        <div>
          <h2>Deliver</h2>
          <p>Variant intent is versioned independently from renderer output.</p>
        </div>
        <span className="count-label">{project.deliverables.length} profiles</span>
      </header>
      <div className="deliver-list">
        {project.deliverables.map((profile) => (
          <div className="deliver-row" key={profile.id}>
            <div className="format-frame" style={{ aspectRatio: profile.width + " / " + profile.height }} />
            <div>
              <strong>{profile.name}</strong>
              <span>{profile.width} × {profile.height} · {profile.language.toUpperCase()}</span>
            </div>
            <span className="captions-state">{profile.captions ? "Captions on" : "Captions off"}</span>
            <button type="button" className="button" disabled title="No render artifact is available for this profile">
              Export unavailable
            </button>
          </div>
        ))}
      </div>
      <div className="evidence-callout">
        <FileOutput size={18} />
        <div>
          <strong>No false export</strong>
          <span>Export stays disabled until a real render artifact is associated with the active revision.</span>
        </div>
      </div>
    </div>
  );
}

function EmptyWorkspace({
  icon: Icon,
  title,
  body,
}: {
  icon: typeof Film;
  title: string;
  body: string;
}) {
  return (
    <div className="empty-workspace">
      <Icon size={24} />
      <strong>{title}</strong>
      <span>{body}</span>
    </div>
  );
}

function ProjectRail({
  project,
  selectedSceneId,
  selectScene,
  commit,
  collapsed,
  setCollapsed,
}: {
  project: Project;
  selectedSceneId: string | null;
  selectScene: (id: string) => void;
  commit: (change: Change) => Promise<void>;
  collapsed: boolean;
  setCollapsed: (value: boolean) => void;
}) {
  const [addingScene, setAddingScene] = useState(false);
  const [newName, setNewName] = useState("New scene");

  if (collapsed) {
    return (
      <aside className="project-rail collapsed">
        <IconButton label="Open project rail" onClick={() => setCollapsed(false)}>
          <PanelLeftClose size={17} className="flip-x" />
        </IconButton>
        <span className="rail-count">{project.scenes.length}</span>
      </aside>
    );
  }

  const selectedIndex = project.scenes.findIndex((scene) => scene.id === selectedSceneId);
  return (
    <aside className="project-rail">
      <div className="rail-heading">
        <span>Project</span>
        <IconButton label="Collapse project rail" onClick={() => setCollapsed(true)}>
          <PanelLeftClose size={16} />
        </IconButton>
      </div>
      <div className="rail-search">
        <Search size={14} />
        <span>Find in project</span>
        <kbd>⌘K</kbd>
      </div>

      <div className="rail-section">
        <div className="rail-section-title">
          <span>Scenes</span>
          <button type="button" className="plain-icon" aria-label="Add scene" onClick={() => setAddingScene((value) => !value)}>
            {addingScene ? <X size={14} /> : <Plus size={14} />}
          </button>
        </div>
        {addingScene && (
          <form
            className="add-scene-form"
            onSubmit={async (event) => {
              event.preventDefault();
              if (!newName.trim()) return;
              await commit({ type: "add_scene", name: newName.trim(), objective: "", duration_seconds: 6 });
              setAddingScene(false);
              setNewName("New scene");
            }}
          >
            <input value={newName} onChange={(event) => setNewName(event.target.value)} aria-label="New scene name" />
            <button className="button button-primary compact" type="submit">Add</button>
          </form>
        )}
        <div className="tree-list">
          {project.scenes.map((scene, index) => (
            <button
              key={scene.id}
              type="button"
              className={"tree-row " + (selectedSceneId === scene.id ? "selected" : "")}
              onClick={() => selectScene(scene.id)}
            >
              <span className="tree-index">{String(index + 1).padStart(2, "0")}</span>
              <span className="tree-name">{scene.name}</span>
              <span className={"scene-state-dot state-" + scene.status} />
            </button>
          ))}
        </div>
        {selectedSceneId && project.scenes.length > 1 && (
          <div className="rail-reorder">
            <span>Order</span>
            <IconButton
              label="Move selected scene earlier"
              disabled={selectedIndex <= 0}
              onClick={() => commit({ type: "move_scene", scene_id: selectedSceneId, to_index: selectedIndex - 1 })}
            >
              <ChevronUp size={14} />
            </IconButton>
            <IconButton
              label="Move selected scene later"
              disabled={selectedIndex < 0 || selectedIndex >= project.scenes.length - 1}
              onClick={() => commit({ type: "move_scene", scene_id: selectedSceneId, to_index: selectedIndex + 1 })}
            >
              <ChevronDown size={14} />
            </IconButton>
          </div>
        )}
      </div>

      <div className="rail-section">
        <div className="rail-section-title"><span>Assets</span><span>{project.assets.length}</span></div>
        <div className="asset-list">
          {project.assets.map((asset) => (
            <div className="asset-row" key={asset.id}>
              <Layers3 size={14} />
              <span>{asset.name}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="rail-section">
        <div className="rail-section-title"><span>Branches</span><span>{project.branches.length}</span></div>
        {project.branches.map((branch) => (
          <div className="asset-row" key={branch.id}>
            <GitBranch size={14} />
            <span>{branch.name}</span>
            {branch.id === project.active_branch && <span className="active-dot" />}
          </div>
        ))}
      </div>
    </aside>
  );
}

function Inspector({
  project,
  scene,
  commit,
}: {
  project: Project;
  scene: Scene | null;
  commit: (change: Change) => Promise<void>;
}) {
  const [objective, setObjective] = useState(scene?.objective ?? "");
  useEffect(() => setObjective(scene?.objective ?? ""), [scene?.id, scene?.objective]);

  if (!scene) {
    return (
      <aside className="inspector">
        <div className="inspector-heading">Inspector</div>
        <EmptyWorkspace icon={Settings2} title="Nothing selected" body="Select a scene to inspect its semantic state." />
      </aside>
    );
  }

  const resource = "scene:" + scene.id;
  const locks = project.locks.filter((lock) => lock.resource === resource);
  return (
    <aside className="inspector">
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
          value={scene.renderer}
          onChange={(event) => commit({
            type: "set_scene_renderer",
            scene_id: scene.id,
            renderer: event.target.value as RendererKind,
          })}
        >
          {Object.entries(rendererLabels).map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        <div className="renderer-note">
          <CircleDashed size={13} />
          Capability loss is evaluated before a renderer swap is executed.
        </div>
      </section>

      <section className="inspector-section">
        <div className="inspector-section-title">Objective</div>
        <textarea value={objective} onChange={(event) => setObjective(event.target.value)} rows={5} />
        <button
          type="button"
          className="button full"
          disabled={!objective.trim() || objective === scene.objective}
          onClick={() => commit({ type: "update_scene_objective", scene_id: scene.id, objective: objective.trim() })}
        >
          Commit objective
        </button>
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

function Timeline({
  project,
  selectedSceneId,
  setSelectedSceneId,
  playhead,
  setPlayhead,
}: {
  project: Project;
  selectedSceneId: string | null;
  setSelectedSceneId: (id: string) => void;
  playhead: number;
  setPlayhead: (value: number) => void;
}) {
  const duration = Math.max(
    1,
    ...project.scenes.map((scene) => seconds(scene.start) + seconds(scene.duration)),
    ...project.markers.map((marker) => seconds(marker.at)),
  );
  const ticks = Array.from({ length: Math.ceil(duration / 5) + 1 }, (_, index) => index * 5);

  return (
    <section className="timeline-panel" aria-label="Timeline">
      <div className="timeline-header">
        <div className="timeline-title">
          <Film size={15} />
          Timeline
          <span className="muted">shared clock</span>
        </div>
        <div className="timeline-transport">
          <span className="timecode">{formatTime(playhead)}</span>
        </div>
      </div>
      <div className="timeline-body">
        <div className="track-labels" aria-hidden="true">
          <div className="ruler-label">24 fps</div>
          <div><span>VO</span><span className="track-type">audio</span></div>
          <div><span>Scenes</span><span className="track-type">semantic</span></div>
          <div><span>Markers</span><span className="track-type">review</span></div>
        </div>
        <div
          className="track-area"
          onPointerDown={(event) => {
            const bounds = event.currentTarget.getBoundingClientRect();
            const ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
            setPlayhead(ratio * duration);
          }}
        >
          <div className="ruler">
            {ticks.map((tick) => (
              <span key={tick} style={{ left: (tick / duration) * 100 + "%" }}>
                {formatTime(tick).slice(0, 5)}
              </span>
            ))}
          </div>
          <div className="track voice-track">
            <div className="audio-presence" aria-label="Voice asset attached to project">
              <span>voiceover.wav</span>
              <small>audio present · waveform not measured</small>
            </div>
          </div>
          <div className="track scene-track">
            {project.scenes.map((scene, index) => (
              <button
                key={scene.id}
                type="button"
                className={"timeline-clip clip-" + (index % 4) + (selectedSceneId === scene.id ? " selected" : "")}
                style={{
                  left: (seconds(scene.start) / duration) * 100 + "%",
                  width: (seconds(scene.duration) / duration) * 100 + "%",
                }}
                onPointerDown={(event) => event.stopPropagation()}
                onClick={() => setSelectedSceneId(scene.id)}
              >
                <span>{scene.name}</span>
                <small>{rendererLabels[scene.renderer]}</small>
              </button>
            ))}
          </div>
          <div className="track marker-track">
            {project.markers.map((marker) => (
              <span
                key={marker.id}
                className="timeline-marker"
                style={{ left: (seconds(marker.at) / duration) * 100 + "%" }}
                title={marker.label}
              >
                <i />
                <b>{marker.label}</b>
              </span>
            ))}
          </div>
          <div className="splice-line" style={{ left: (playhead / duration) * 100 + "%" }}>
            <span />
          </div>
        </div>
      </div>
    </section>
  );
}

export default function App() {
  const [boot, setBoot] = useState<Bootstrap | null>(null);
  const [workspace, setWorkspace] = useState<Workspace>("Timeline");
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null);
  const [playhead, setPlayhead] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [railCollapsed, setRailCollapsed] = useState(false);

  useEffect(() => {
    bootstrap()
      .then((value) => {
        setBoot(value);
        setSelectedSceneId(value.project.scenes[0]?.id ?? null);
      })
      .catch((reason) => setError(String(reason)));
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const project = boot?.project ?? null;
  const selectedScene = useMemo(
    () => project?.scenes.find((scene) => scene.id === selectedSceneId) ?? null,
    [project, selectedSceneId],
  );

  const commit = useCallback(async (change: Change) => {
    if (!boot || busy) return;
    setBusy(true);
    setError(null);
    try {
      const next = await applyChange(boot.project, change);
      setBoot({ ...boot, project: next });
      if (selectedSceneId && !next.scenes.some((scene) => scene.id === selectedSceneId)) {
        setSelectedSceneId(next.scenes[0]?.id ?? null);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }, [boot, busy, selectedSceneId]);

  if (!project) {
    return (
      <main className="boot-screen">
        <div className="brand-mark">MW</div>
        <strong>Opening Motionwright</strong>
        <span>{error ?? "Loading the local project store…"}</span>
      </main>
    );
  }

  const renderWorkspace = () => {
    switch (workspace) {
      case "Brief":
        return <BriefView project={project} commit={commit} />;
      case "Narrative":
        return <NarrativeView scene={selectedScene} commit={commit} />;
      case "Storyboard":
        return <StoryboardView project={project} selectedSceneId={selectedSceneId} onSelect={setSelectedSceneId} />;
      case "Canvas":
      case "Timeline":
        return <PreviewSurface scene={selectedScene} project={project} playhead={playhead} />;
      case "Alternatives":
        return <AlternativesView scene={selectedScene} />;
      case "Changes":
        return <ChangesView project={project} />;
      case "Dependencies":
        return <DependenciesView project={project} />;
      case "Review":
        return <ReviewView project={project} scene={selectedScene} />;
      case "Deliver":
        return <DeliverView project={project} />;
    }
  };

  return (
    <main className="app-shell">
      <header className="command-bar">
        <div className="brand">
          <div className="brand-mark">MW</div>
          <div className="brand-copy">
            <strong>Motionwright</strong>
            <span>{project.title}</span>
          </div>
        </div>
        <div className="project-context">
          <span className="branch-chip"><GitBranch size={13} /> {project.branches.find((branch) => branch.id === project.active_branch)?.name ?? "main"}</span>
          <span className="revision-chip">r{project.revision}</span>
          <span className={"status-pill status-" + project.state}>{stateLabel(project.state)}</span>
        </div>
        <div className="command-actions">
          {busy && <span className="saving-state"><RefreshCw size={13} className="spin" /> Committing</span>}
          <span className="sdk-chip" title={boot.native_sdk.pinned_revision}>
            Native SDK · {boot.native_sdk.mode === "tauri" ? "connected" : "browser demo"}
          </span>
          <IconButton label={theme === "dark" ? "Use light theme" : "Use dark theme"} onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </IconButton>
          <IconButton label="More project information" onClick={() => setWorkspace("Brief")}>
            <MoreHorizontal size={17} />
          </IconButton>
        </div>
      </header>

      <nav className="workspace-bar" aria-label="Workspaces">
        {workspaces.map(({ name, icon: Icon }) => (
          <button
            type="button"
            key={name}
            className={workspace === name ? "active" : ""}
            onClick={() => setWorkspace(name)}
          >
            <Icon size={14} />
            <span>{name}</span>
          </button>
        ))}
      </nav>

      {error && (
        <div className="error-banner" role="alert">
          <strong>Change not committed.</strong>
          <span>{error}</span>
          <button type="button" className="plain-icon" aria-label="Dismiss error" onClick={() => setError(null)}><X size={15} /></button>
        </div>
      )}

      <div className={"editor-grid" + (railCollapsed ? " rail-collapsed" : "")}>
        <ProjectRail
          project={project}
          selectedSceneId={selectedSceneId}
          selectScene={setSelectedSceneId}
          commit={commit}
          collapsed={railCollapsed}
          setCollapsed={setRailCollapsed}
        />
        <div className="center-stack">
          <div className="workspace-stage">{renderWorkspace()}</div>
        </div>
        <Inspector project={project} scene={selectedScene} commit={commit} />
      </div>

      <Timeline
        project={project}
        selectedSceneId={selectedSceneId}
        setSelectedSceneId={setSelectedSceneId}
        playhead={playhead}
        setPlayhead={setPlayhead}
      />
    </main>
  );
}
