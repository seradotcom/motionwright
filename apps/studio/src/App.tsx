import {
  Activity,
  AlignLeft,
  AudioLines,
  Box,
  Boxes,
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
  Pause,
  Play,
  Plus,
  RefreshCw,
  SkipBack,
  SkipForward,
  Search,
  Settings2,
  SlidersHorizontal,
  Sparkles,
  Sun,
  Type,
  Unlock,
  Wand2,
  WifiOff,
  Workflow,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  applyChange,
  bootstrap,
  exportProjectBundle,
  importAssetFile,
  importProjectBundle,
  importVoiceFile,
  inspectProjectBundle,
} from "./api";
import AudioWorkspace from "./AudioWorkspace";
import CanvasWorkspace from "./CanvasWorkspace";
import DeliveryProfiles from "./DeliveryProfiles";
import IntegrationsWorkspace from "./IntegrationsWorkspace";
import ProductionJobsWorkspace from "./ProductionJobs";
import WorkflowWorkspace from "./WorkflowWorkspace";
import { RichAlternativesView, RichBriefView, RichNarrativeView } from "./CreativeWorkspaces";
import { ChangesWorkspace, ReviewWorkspace } from "./HistoryWorkspaces";
import type {
  Bootstrap,
  Change,
  PortableBundlePlan,
  Project,
  ProjectState,
  RendererKind,
  Scene,
} from "./types";
import { rationalSeconds, seconds } from "./types";

type Workspace =
  | "Brief"
  | "Narrative"
  | "Audio"
  | "Storyboard"
  | "Canvas"
  | "Timeline"
  | "Jobs"
  | "Workflows"
  | "Integrations"
  | "Alternatives"
  | "Changes"
  | "Dependencies"
  | "Review"
  | "Deliver";

type Theme = "dark" | "light";

const THEME_STORAGE_KEY = "motionwright.theme";

function preferredTheme(): Theme {
  if (typeof window === "undefined") return "dark";
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "dark" || stored === "light") return stored;
  } catch {
    // Local storage may be unavailable in hardened or private WebViews.
  }
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

const workspaces: Array<{ name: Workspace; icon: typeof Film }> = [
  { name: "Brief", icon: AlignLeft },
  { name: "Narrative", icon: Type },
  { name: "Audio", icon: AudioLines },
  { name: "Storyboard", icon: Columns3 },
  { name: "Canvas", icon: Box },
  { name: "Timeline", icon: Film },
  { name: "Jobs", icon: Activity },
  { name: "Workflows", icon: Wand2 },
  { name: "Integrations", icon: Settings2 },
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

const rendererAvailable = (project: Project, renderer: RendererKind) => {
  if (renderer === "remotion") {
    return project.extensions.some((extension) => extension.kind === "remotion-renderer" && extension.enabled && extension.rights_status === "cleared");
  }
  if (renderer === "manim-gl") {
    return project.extensions.some((extension) => extension.kind === "manim-gl-renderer" && extension.enabled && extension.rights_status === "cleared");
  }
  return true;
};

function formatTime(value: number) {
  const mins = Math.floor(value / 60);
  const secs = Math.floor(value % 60);
  const frames = Math.floor((value % 1) * 24);
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}:${String(frames).padStart(2, "0")}`;
}

function projectTimelineDuration(project: Project) {
  return Math.max(
    1,
    ...project.scenes.map((scene) => seconds(scene.start) + seconds(scene.duration)),
    ...project.markers.map((marker) => seconds(marker.at)),
    ...project.audio.voice_tracks.map((track) => seconds(track.measured_duration)),
    ...project.audio.transcript.map((segment) => seconds(segment.end)),
    ...project.audio.cues.map((cue) => seconds(cue.at)),
  );
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
          <span
            className="preview-revision"
            aria-label={`Preview representation is based on project revision ${project.revision}`}
          >
            PROJECT r{project.revision}
          </span>
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

function formatByteCount(value: string) {
  let bytes: bigint;
  try {
    bytes = BigInt(value);
  } catch {
    return value + " B";
  }
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let unit = 0;
  let display = bytes;
  while (display >= 1024n && unit < units.length - 1) {
    display /= 1024n;
    unit += 1;
  }
  return display.toString() + " " + units[unit];
}

function DeliverView({
  project,
  commit,
  desktopMode,
  onImported,
}: {
  project: Project;
  commit: (change: Change) => Promise<void>;
  desktopMode: boolean;
  onImported: (project: Project) => void;
}) {
  const [exportPath, setExportPath] = useState("");
  const [importPath, setImportPath] = useState("");
  const [bundlePlan, setBundlePlan] = useState<PortableBundlePlan | null>(null);
  const [portableBusy, setPortableBusy] = useState<"export" | "inspect" | "import" | null>(null);
  const [portableStatus, setPortableStatus] = useState<string | null>(null);
  const [portableError, setPortableError] = useState<string | null>(null);

  const inspectBundle = async () => {
    if (!desktopMode || !importPath.trim()) return;
    setPortableBusy("inspect");
    setPortableStatus(null);
    setPortableError(null);
    try {
      const plan = await inspectProjectBundle(importPath.trim());
      setBundlePlan(plan);
    } catch (reason) {
      setBundlePlan(null);
      setPortableError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPortableBusy(null);
    }
  };

  const exportBundle = async () => {
    if (!desktopMode || !exportPath.trim()) return;
    setPortableBusy("export");
    setPortableStatus(null);
    setPortableError(null);
    try {
      const result = await exportProjectBundle(project, exportPath.trim());
      setPortableStatus(
        "Portable bundle written to " + result.destination +
        " · " + result.blob_count + " blobs · " + formatByteCount(result.total_blob_bytes),
      );
    } catch (reason) {
      setPortableError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPortableBusy(null);
    }
  };

  const importBundle = async () => {
    if (!desktopMode || !bundlePlan || !importPath.trim()) return;
    setPortableBusy("import");
    setPortableStatus(null);
    setPortableError(null);
    try {
      const imported = await importProjectBundle(importPath.trim());
      onImported(imported);
      setPortableStatus(
        "Imported " + imported.title + " at revision " + imported.revision +
        " with a fresh execution generation.",
      );
      setBundlePlan(null);
    } catch (reason) {
      setPortableError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPortableBusy(null);
    }
  };

  return (
    <div className="workspace-scroll deliver-view">
      <header className="workspace-heading">
        <div>
          <h2>Deliver</h2>
          <p>Variant intent is versioned independently from renderer output.</p>
        </div>
        <span className="count-label">{project.deliverables.length} profiles</span>
      </header>

      <DeliveryProfiles project={project} commit={commit} desktopMode={desktopMode} />

      <section className="portable-project" aria-label="Portable project">
        <header className="portable-heading">
          <div>
            <strong>Portable project</strong>
            <span>Project state, journal and content-addressed asset bytes.</span>
          </div>
          <span className="status-pill status-current">SELF-CONTAINED</span>
        </header>

        <div className="portable-operation">
          <div className="portable-operation-copy">
            <strong>Export active project</strong>
            <span>Creates a new directory and refuses to overwrite an existing destination.</span>
          </div>
          <label>
            <span className="field-label">Destination · absolute path</span>
            <input
              aria-label="Portable export path"
              value={exportPath}
              disabled={!desktopMode || portableBusy !== null}
              placeholder="/absolute/path/project.motionwright"
              onChange={(event) => setExportPath(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="button button-primary"
            disabled={!desktopMode || !exportPath.trim() || portableBusy !== null}
            onClick={() => void exportBundle()}
          >
            {portableBusy === "export" ? "Exporting…" : "Export bundle"}
          </button>
        </div>

        <div className="portable-operation import-operation">
          <div className="portable-operation-copy">
            <strong>Inspect and import</strong>
            <span>Preflight verifies project history, manifest and every referenced blob before any project state is admitted.</span>
          </div>
          <label>
            <span className="field-label">Source · absolute path</span>
            <input
              aria-label="Portable import path"
              value={importPath}
              disabled={!desktopMode || portableBusy !== null}
              placeholder="/absolute/path/project.motionwright"
              onChange={(event) => {
                setImportPath(event.target.value);
                setBundlePlan(null);
                setPortableStatus(null);
              }}
            />
          </label>
          <div className="portable-actions">
            <button
              type="button"
              className="button"
              disabled={!desktopMode || !importPath.trim() || portableBusy !== null}
              onClick={() => void inspectBundle()}
            >
              {portableBusy === "inspect" ? "Inspecting…" : "Inspect bundle"}
            </button>
            <button
              type="button"
              className="button button-primary"
              disabled={!desktopMode || !bundlePlan || portableBusy !== null}
              onClick={() => void importBundle()}
            >
              {portableBusy === "import" ? "Importing…" : "Import verified bundle"}
            </button>
          </div>
        </div>

        {!desktopMode && (
          <div className="portable-runtime-note">
            <CircleDashed size={15} />
            <span>Desktop filesystem capability is required. Browser demo mode never fabricates a portable bundle.</span>
          </div>
        )}

        {bundlePlan && (
          <div className="portable-plan" aria-label="Portable bundle preflight">
            <div><span>Project</span><strong>{bundlePlan.title}</strong></div>
            <div><span>Revision</span><strong className="mono">r{bundlePlan.revision}</strong></div>
            <div><span>Journal</span><strong>{bundlePlan.event_count} events</strong></div>
            <div><span>Assets</span><strong>{bundlePlan.blob_count} blobs · {formatByteCount(bundlePlan.total_blob_bytes)}</strong></div>
            <div><span>Authority</span><strong>{bundlePlan.rotates_generation ? "New generation on import" : "Unchanged"}</strong></div>
          </div>
        )}

        {portableError && (
          <div className="portable-message error" role="alert">
            <strong>Portable operation blocked.</strong>
            <span>{portableError}</span>
          </div>
        )}
        {portableStatus && (
          <div className="portable-message success" role="status">
            <CircleCheck size={15} />
            <span>{portableStatus}</span>
          </div>
        )}
      </section>

      <div className="evidence-callout">
        <FileOutput size={18} />
        <div>
          <strong>No false media export</strong>
          <span>Render-profile export stays disabled until a real artifact is associated with the active revision. Portable project export is a separate, verified project-state operation.</span>
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
  ingestAsset,
  desktopMode,
  collapsed,
  setCollapsed,
}: {
  project: Project;
  selectedSceneId: string | null;
  selectScene: (id: string) => void;
  commit: (change: Change) => Promise<void>;
  ingestAsset: (path: string) => Promise<void>;
  desktopMode: boolean;
  collapsed: boolean;
  setCollapsed: (value: boolean) => void;
}) {
  const [addingScene, setAddingScene] = useState(false);
  const [newName, setNewName] = useState("New scene");
  const [addingAsset, setAddingAsset] = useState(false);
  const [assetPath, setAssetPath] = useState("");
  const [assetBusy, setAssetBusy] = useState(false);
  const [assetError, setAssetError] = useState<string | null>(null);

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
        <div className="rail-section-title">
          <span>Assets</span>
          <div className="rail-section-actions">
            <span>{project.assets.length}</span>
            <button
              type="button"
              className="plain-icon"
              aria-label="Import local asset"
              disabled={!desktopMode}
              title={desktopMode ? "Import a local file into the content-addressed project store" : "Asset import requires the desktop runtime"}
              onClick={() => {
                setAddingAsset((value) => !value);
                setAssetError(null);
              }}
            >
              {addingAsset ? <X size={14} /> : <Plus size={14} />}
            </button>
          </div>
        </div>
        {addingAsset && (
          <form
            className="asset-import-form"
            onSubmit={async (event) => {
              event.preventDefault();
              const path = assetPath.trim();
              if (!path || assetBusy) return;
              setAssetBusy(true);
              setAssetError(null);
              try {
                await ingestAsset(path);
                setAssetPath("");
                setAddingAsset(false);
              } catch (reason) {
                setAssetError(reason instanceof Error ? reason.message : String(reason));
              } finally {
                setAssetBusy(false);
              }
            }}
          >
            <input
              aria-label="Local asset path"
              value={assetPath}
              disabled={assetBusy}
              placeholder="/absolute/path/to/asset"
              onChange={(event) => setAssetPath(event.target.value)}
            />
            <button
              className="button button-primary compact"
              type="submit"
              disabled={!assetPath.trim() || assetBusy}
            >
              {assetBusy ? "Importing…" : "Import"}
            </button>
            {assetError && <span className="asset-import-error" role="alert">{assetError}</span>}
          </form>
        )}
        <div className="asset-list">
          {project.assets.map((asset) => (
            <div className="asset-row" key={asset.id}>
              <Layers3 size={14} />
              <div className="asset-row-copy">
                <span>{asset.name}</span>
                <small>{asset.media_type}</small>
              </div>
              <button
                type="button"
                className="plain-icon asset-remove"
                aria-label={"Remove asset " + asset.name}
                title="Remove project reference; immutable blob bytes are retained"
                onClick={() => void commit({ type: "remove_asset", asset_id: asset.id })}
              >
                <X size={12} />
              </button>
            </div>
          ))}
        </div>
        {!desktopMode && (
          <span className="rail-runtime-note">Local asset import is available in the desktop runtime.</span>
        )}
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
  const [durationSeconds, setDurationSeconds] = useState(scene ? seconds(scene.duration) : 0);
  useEffect(() => setObjective(scene?.objective ?? ""), [scene?.id, scene?.objective]);
  useEffect(() => setDurationSeconds(scene ? seconds(scene.duration) : 0), [scene?.id, scene?.duration.num, scene?.duration.den]);

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
  const timingLocked = locks.some((lock) => lock.kind === "timing");
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
            type="number"
            min="0.001"
            max="86400"
            step="0.1"
            value={durationSeconds}
            disabled={timingLocked}
            onChange={(event) => setDurationSeconds(Number(event.target.value))}
          />
          <button
            type="button"
            className="button compact"
            disabled={timingLocked || !Number.isFinite(durationSeconds) || durationSeconds <= 0 || durationSeconds > 86400 || Math.abs(durationSeconds - seconds(scene.duration)) < 0.0005}
            onClick={() => commit({ type: "set_scene_duration", scene_id: scene.id, duration: rationalSeconds(durationSeconds) })}
          >
            Ripple
          </button>
        </div>
        <div className="renderer-note">
          <Clock3 size={13} />
          Duration ripple reflows later scene starts. It does not stretch or infer voice timing.
        </div>
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
  selectScene,
  playhead,
  setPlayhead,
  commit,
  playing,
  togglePlayback,
  stepFrame,
}: {
  project: Project;
  selectedSceneId: string | null;
  selectScene: (id: string, seekToStart?: boolean) => void;
  playhead: number;
  setPlayhead: (value: number) => void;
  commit: (change: Change) => Promise<void>;
  playing: boolean;
  togglePlayback: () => void;
  stepFrame: (direction: -1 | 1) => void;
}) {
  const [selection, setSelection] = useState<{
    kind: "transcript" | "beat" | "cue" | "marker";
    id: string;
    label: string;
  } | null>(null);
  const duration = projectTimelineDuration(project);
  const ticks = Array.from({ length: Math.ceil(duration / 5) + 1 }, (_, index) => index * 5);
  const measuredVoice = project.audio.voice_tracks;
  const measuredAssetIds = new Set(measuredVoice.map((track) => track.asset_id));
  const pendingAudio = project.assets.filter(
    (asset) => asset.media_type.startsWith("audio/") && !measuredAssetIds.has(asset.id),
  );
  const beatEntries = project.scenes.flatMap((scene) =>
    scene.beats.map((beat) => {
      const sceneStart = seconds(scene.start);
      const sceneEnd = sceneStart + seconds(scene.duration);
      const start = Math.min(sceneEnd, sceneStart + seconds(beat.start));
      const end = Math.min(sceneEnd, start + seconds(beat.duration));
      return { scene, beat, start, end };
    }),
  );

  const sceneAtTime = (time: number) =>
    project.scenes.find((scene) => {
      const start = seconds(scene.start);
      const end = start + seconds(scene.duration);
      return time >= start && time < end;
    }) ?? null;

  const selectAt = (
    time: number,
    nextSelection: { kind: "transcript" | "beat" | "cue" | "marker"; id: string; label: string },
    sceneId?: string,
  ) => {
    const bounded = Math.max(0, Math.min(duration, time));
    setPlayhead(bounded);
    setSelection(nextSelection);
    const targetScene = sceneId
      ? project.scenes.find((scene) => scene.id === sceneId) ?? null
      : sceneAtTime(bounded);
    if (targetScene && targetScene.id !== selectedSceneId) {
      selectScene(targetScene.id, false);
    }
  };

  return (
    <section className="timeline-panel" aria-label="Timeline">
      <div className="timeline-header">
        <div className="timeline-title">
          <Film size={15} />
          Timeline
          <span className="muted">shared clock</span>
        </div>
        <div className="timeline-selection-context" aria-live="polite">
          {selection ? (
            <>
              <span>{selection.kind}</span>
              <strong>{selection.label}</strong>
            </>
          ) : (
            <>
              <span>context</span>
              <strong>{project.scenes.find((scene) => scene.id === selectedSceneId)?.name ?? "project"}</strong>
            </>
          )}
        </div>
        <div className="timeline-transport">
          <IconButton label="Step back one frame" onClick={() => stepFrame(-1)}>
            <SkipBack size={13} />
          </IconButton>
          <IconButton label={playing ? "Pause design preview" : "Play design preview"} onClick={togglePlayback}>
            {playing ? <Pause size={13} /> : <Play size={13} />}
          </IconButton>
          <IconButton label="Step forward one frame" onClick={() => stepFrame(1)}>
            <SkipForward size={13} />
          </IconButton>
          <button
            type="button"
            className="button compact"
            onClick={() => commit({
              type: "add_marker",
              at: rationalSeconds(playhead),
              label: "Marker " + (project.markers.length + 1),
            })}
          >
            <Plus size={12} /> Marker
          </button>
          <span className="timecode">{formatTime(playhead)}</span>
        </div>
      </div>
      <div className="timeline-body">
        <div className="track-labels" aria-hidden="true">
          <div className="ruler-label">time</div>
          <div><span>VO</span><span className="track-type">audio</span></div>
          <div><span>Transcript</span><span className="track-type">text</span></div>
          <div><span>Beats</span><span className="track-type">story</span></div>
          <div><span>Scenes</span><span className="track-type">semantic</span></div>
          <div><span>Review</span><span className="track-type">cues · markers</span></div>
        </div>
        <div
          className="track-area"
          onPointerDown={(event) => {
            const bounds = event.currentTarget.getBoundingClientRect();
            const ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
            const nextPlayhead = ratio * duration;
            setSelection(null);
            setPlayhead(nextPlayhead);
            const sceneAtPlayhead = sceneAtTime(nextPlayhead);
            if (sceneAtPlayhead && sceneAtPlayhead.id !== selectedSceneId) {
              selectScene(sceneAtPlayhead.id, false);
            }
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
            {measuredVoice.map((track) => (
              <div
                className="audio-presence measured"
                key={track.id}
                aria-label={"Measured voice track " + track.label}
                style={{ width: Math.min(100, (seconds(track.measured_duration) / duration) * 100) + "%" }}
              >
                <span>{track.label}</span>
                <small>{track.sample_rate_hz} Hz · {track.channels} ch · measured duration</small>
              </div>
            ))}
            {measuredVoice.length === 0 && pendingAudio.length > 0 && (
              <div className="audio-pending" aria-label="Audio source awaiting measurement">
                <span>{pendingAudio[0].name}</span>
                <small>source attached · duration unmeasured</small>
              </div>
            )}
            {measuredVoice.length === 0 && pendingAudio.length === 0 && (
              <div className="audio-pending empty" aria-label="No measured voice track">
                <span>No measured voice</span>
                <small>timeline duration is not inferred from text</small>
              </div>
            )}
          </div>
          <div className="track transcript-track">
            {project.audio.transcript.length === 0 ? (
              <span className="timeline-empty-lane">No measured or manually aligned transcript</span>
            ) : (
              project.audio.transcript.map((segment) => {
                const start = seconds(segment.start);
                const end = seconds(segment.end);
                const selected = selection?.kind === "transcript" && selection.id === segment.id;
                return (
                  <button
                    key={segment.id}
                    type="button"
                    className={"timeline-transcript" + (selected ? " selected" : "")}
                    style={{
                      left: (start / duration) * 100 + "%",
                      width: (Math.max(0.1, end - start) / duration) * 100 + "%",
                    }}
                    aria-label={"Transcript: " + segment.text}
                    aria-pressed={selected}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={() => selectAt(start, { kind: "transcript", id: segment.id, label: segment.text })}
                    title={segment.text}
                  >
                    {segment.text}
                  </button>
                );
              })
            )}
          </div>
          <div className="track beat-track">
            {beatEntries.length === 0 ? (
              <span className="timeline-empty-lane">No scene beats authored</span>
            ) : (
              beatEntries.map(({ scene, beat, start, end }) => {
                const selected = selection?.kind === "beat" && selection.id === beat.id;
                return (
                  <button
                    key={beat.id}
                    type="button"
                    className={"timeline-beat" + (selected ? " selected" : "")}
                    style={{
                      left: (start / duration) * 100 + "%",
                      width: (Math.max(0.1, end - start) / duration) * 100 + "%",
                    }}
                    aria-label={"Beat: " + beat.label}
                    aria-pressed={selected}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={() => selectAt(
                      start,
                      { kind: "beat", id: beat.id, label: beat.label },
                      scene.id,
                    )}
                    title={beat.objective}
                  >
                    <span>{beat.label}</span>
                  </button>
                );
              })
            )}
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
                onClick={() => {
                  setSelection(null);
                  selectScene(scene.id);
                }}
              >
                <span>{scene.name}</span>
                <small>{rendererLabels[scene.renderer]}</small>
              </button>
            ))}
          </div>
          <div className="track review-track">
            {project.audio.cues.map((cue) => {
              const at = seconds(cue.at);
              const selected = selection?.kind === "cue" && selection.id === cue.id;
              return (
                <button
                  key={cue.id}
                  type="button"
                  className={"timeline-cue" + (selected ? " selected" : "")}
                  style={{ left: (at / duration) * 100 + "%" }}
                  aria-label={"Cue: " + cue.label}
                  aria-pressed={selected}
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={() => selectAt(at, { kind: "cue", id: cue.id, label: cue.label })}
                  title={cue.label + " · " + cue.evidence}
                >
                  <i />
                  <b>{cue.label}</b>
                </button>
              );
            })}
            {project.markers.map((marker) => {
              const at = seconds(marker.at);
              const selected = selection?.kind === "marker" && selection.id === marker.id;
              return (
                <button
                  key={marker.id}
                  type="button"
                  className={"timeline-marker" + (selected ? " selected" : "")}
                  style={{ left: (at / duration) * 100 + "%" }}
                  aria-label={"Marker: " + marker.label}
                  aria-pressed={selected}
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={() => selectAt(at, { kind: "marker", id: marker.id, label: marker.label })}
                  title={marker.label}
                >
                  <i />
                  <b>{marker.label}</b>
                </button>
              );
            })}
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
  const [playing, setPlaying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>(preferredTheme);
  const [online, setOnline] = useState(() => typeof navigator === "undefined" ? true : navigator.onLine);
  const [railCollapsed, setRailCollapsed] = useState(false);

  const loadProject = useCallback(async () => {
    setBootError(null);
    try {
      const value = await bootstrap();
      setBoot(value);
      setSelectedSceneId(value.project.scenes[0]?.id ?? null);
      setPlayhead(value.project.scenes[0] ? seconds(value.project.scenes[0].start) : 0);
    } catch (reason) {
      setBootError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);

  useEffect(() => {
    void loadProject();
  }, [loadProject]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Theme remains active for this session when persistence is unavailable.
    }
  }, [theme]);

  useEffect(() => {
    const markOnline = () => setOnline(true);
    const markOffline = () => setOnline(false);
    window.addEventListener("online", markOnline);
    window.addEventListener("offline", markOffline);
    return () => {
      window.removeEventListener("online", markOnline);
      window.removeEventListener("offline", markOffline);
    };
  }, []);

  const project = boot?.project ?? null;
  const selectedScene = useMemo(
    () => project?.scenes.find((scene) => scene.id === selectedSceneId) ?? null,
    [project, selectedSceneId],
  );

  const selectScene = useCallback((id: string, seekToStart = true) => {
    setSelectedSceneId(id);
    if (!seekToStart) return;
    const scene = project?.scenes.find((candidate) => candidate.id === id);
    if (scene) setPlayhead(seconds(scene.start));
  }, [project]);

  const stepFrame = useCallback((direction: -1 | 1) => {
    if (!project) return;
    setPlaying(false);
    const next = Math.max(
      0,
      Math.min(projectTimelineDuration(project), playhead + direction * (1 / 24)),
    );
    setPlayhead(next);
    const scene = project.scenes.find((candidate) => {
      const start = seconds(candidate.start);
      return next >= start && next < start + seconds(candidate.duration);
    });
    if (scene) setSelectedSceneId(scene.id);
  }, [playhead, project]);

  useEffect(() => {
    if (!playing || !project) return;
    const duration = projectTimelineDuration(project);
    const timer = window.setInterval(() => {
      setPlayhead((current) => {
        const next = Math.min(duration, current + 0.1);
        const scene = project.scenes.find((candidate) => {
          const start = seconds(candidate.start);
          return next >= start && next < start + seconds(candidate.duration);
        });
        if (scene) setSelectedSceneId(scene.id);
        if (next >= duration) setPlaying(false);
        return next;
      });
    }, 100);
    return () => window.clearInterval(timer);
  }, [playing, project]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (
        target &&
        (target.isContentEditable ||
          target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT")
      ) {
        return;
      }
      if (event.code === "Space") {
        event.preventDefault();
        setPlaying((current) => !current);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        stepFrame(-1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        stepFrame(1);
      } else if (event.key.toLowerCase() === "k") {
        setPlaying(false);
      } else if (event.key.toLowerCase() === "l") {
        setPlaying(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [stepFrame]);

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

  const ingestAsset = useCallback(async (path: string) => {
    if (!boot) throw new Error("Project is not ready.");
    if (busy) throw new Error("Finish the current project change before importing an asset.");
    const next = await importAssetFile(boot.project, path);
    setBoot({ ...boot, project: next });
  }, [boot, busy]);

  const ingestVoice = useCallback(async (path: string, label?: string) => {
    if (!boot) throw new Error("Project is not ready.");
    if (busy) throw new Error("Finish the current project change before importing a voice take.");
    const next = await importVoiceFile(boot.project, path, label);
    setBoot({ ...boot, project: next });
  }, [boot, busy]);

  if (!project || !boot) {
    return (
      <main className="boot-screen" aria-busy={!bootError}>
        <div className="brand-mark">MW</div>
        {bootError ? (
          <section className="boot-error" role="alert" aria-label="Project open error">
            <strong>Couldn’t open the local project</strong>
            <span>{bootError}</span>
            <button type="button" className="button button-primary" onClick={() => void loadProject()}>
              Retry opening project
            </button>
          </section>
        ) : (
          <section className="boot-loading" role="status" aria-live="polite">
            <strong>Opening Motionwright</strong>
            <span>Loading the local project store…</span>
            <div className="boot-skeleton" aria-hidden="true">
              <i />
              <i />
              <i />
            </div>
          </section>
        )}
      </main>
    );
  }

  const renderWorkspace = () => {
    switch (workspace) {
      case "Brief":
        return <RichBriefView project={project} commit={commit} />;
      case "Narrative":
        return <RichNarrativeView project={project} scene={selectedScene} commit={commit} />;
      case "Audio":
        return (
          <AudioWorkspace
            project={project}
            commit={commit}
            desktopMode={boot.native_sdk.mode === "tauri"}
            importVoice={ingestVoice}
          />
        );
      case "Storyboard":
        return <StoryboardView project={project} selectedSceneId={selectedSceneId} onSelect={selectScene} />;
      case "Canvas":
        return <CanvasWorkspace project={project} scene={selectedScene} commit={commit} />;
      case "Timeline":
        return <PreviewSurface scene={selectedScene} project={project} playhead={playhead} />;
      case "Jobs":
        return (
          <ProductionJobsWorkspace
            project={project}
            desktopMode={boot.native_sdk.mode === "tauri"}
          />
        );
      case "Workflows":
        return <WorkflowWorkspace project={project} />;
      case "Integrations":
        return <IntegrationsWorkspace project={project} nativeSdk={boot.native_sdk} commit={commit} />;
      case "Alternatives":
        return <RichAlternativesView project={project} scene={selectedScene} commit={commit} />;
      case "Changes":
        return <ChangesWorkspace project={project} commit={commit} />;
      case "Dependencies":
        return <DependenciesView project={project} />;
      case "Review":
        return <ReviewWorkspace project={project} scene={selectedScene} commit={commit} />;
      case "Deliver":
        return (
          <DeliverView
            project={project}
            commit={commit}
            desktopMode={boot.native_sdk.mode === "tauri"}
            onImported={(imported) => {
              setBoot({ ...boot, project: imported });
              setSelectedSceneId(imported.scenes[0]?.id ?? null);
              setPlayhead(0);
              setPlaying(false);
            }}
          />
        );
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
          {busy && <span className="saving-state" role="status" aria-live="polite"><RefreshCw size={13} className="spin" /> Committing</span>}
          {!online && (
            <span className="offline-chip" title="Network unavailable. Local project editing remains available.">
              <WifiOff size={13} aria-hidden="true" /> OFFLINE · LOCAL
            </span>
          )}
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
            aria-current={workspace === name ? "page" : undefined}
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
          selectScene={selectScene}
          commit={commit}
          ingestAsset={ingestAsset}
          desktopMode={boot.native_sdk.mode === "tauri"}
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
        selectScene={selectScene}
        playhead={playhead}
        setPlayhead={setPlayhead}
        commit={commit}
        playing={playing}
        togglePlayback={() => setPlaying((current) => !current)}
        stepFrame={stepFrame}
      />
    </main>
  );
}
