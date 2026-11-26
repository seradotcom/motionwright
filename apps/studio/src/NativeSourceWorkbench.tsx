import { useEffect, useId, useRef, useState } from "react";
import { Download, RefreshCw, Save } from "lucide-react";
import BlenderSourceInspector from "./BlenderSourceInspector";
import BlenderExecutionPanel from "./BlenderExecutionPanel";
import { BLENDER_RECIPE_MEDIA_TYPE } from "./blenderSourceApi";
import type { Project, Scene } from "./types";
import {
  FILE_IMPORT_CHANGED, FILE_IMPORT_PREFIX, assertNoFileImport, readFileImports,
  type NativeCapsuleSourceSpec7,
} from "./fileImport";
import { prepareNativeSourceSpec, useNativeSourceDraft, type NativeSourceField } from "./nativeSourceDraft";
import BlenderProductRecipeEditor from "./BlenderProductRecipeEditor";
import { blenderProductSceneIssues,type ProductCameraPreset } from "./blenderProductRecipe";

export interface NativeSourceSaveRequest {
  path: string;
  spec: NativeCapsuleSourceSpec7;
  expected_sha256?: string;
  source: { project_id: string; generation: string; revision: number; branch_id: string };
}
export interface NativeSourceExportRequest {
  path: string; scene_id: string; capsule_id: string;
  expected_source_asset_id: string; expected_source_sha256: string;
  source: { project_id: string; generation: string; revision: number; branch_id: string };
}
export interface NativeSourceExportReceipt {
  path: string; capsule_id: string; source_asset_id: string;
  source_sha256: string; size_bytes: string; editor_launched: false;
}
export interface NativeSourceServices {
  saveSource: (request: NativeSourceSaveRequest) => Promise<Project>;
  refreshProject: () => Promise<Project>;
  exportSource: (request: NativeSourceExportRequest) => Promise<NativeSourceExportReceipt>;
}
interface Props extends NativeSourceServices { project: Project; scene: Scene | null; busy: boolean; preferredCameraMotion?:ProductCameraPreset; }
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);

function download(content: string, filename: string) {
  const url = URL.createObjectURL(new Blob([content], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url; link.download = filename; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

/** Authored local input only. The existing App coordinator publishes every save. */
function SourceEditor({ project, scene, target, busy, saveSource, refreshProject, exportSource,preferredCameraMotion }: Omit<Props, "scene"> & { scene: Scene; target: string }) {
  const editor = useNativeSourceDraft(project, scene.id, target);
  const heading = useId();
  const [imports, setImports] = useState(() => readFileImports(project.id));
  const [sending, setSending] = useState(false);
  const [reading, setReading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const exportInFlight = useRef(false);
  const [exportPath, setExportPath] = useState("");
  const [exportStatus, setExportStatus] = useState<string | null>(null);
  const [lastExport, setLastExport] = useState<{ path: string; sourceKey: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const desktop = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
  const capsule = project.production_design?.capsules.find(value => value.id === editor.capsuleId);
  const sourceAsset = project.assets.find(value => value.id === capsule?.source_asset_id);
  // The last confirmed file can be reused as draft input only for exactly the
  // same saved source authority; a new branch/revision/source cannot inherit it.
  const sourceKey = JSON.stringify([
    project.id, project.generation, project.active_branch, project.revision,
    scene.id, capsule?.id, capsule?.source_asset_id, capsule?.source_sha256,
  ]);
  const reusableExport = lastExport?.sourceKey === sourceKey ? lastExport : null;
  const pendingImport = imports.error !== null || imports.limited || imports.copies.length !== 0;
  const pending = busy || sending || reading || exporting;
  const unavailable = target !== "new" && (!capsule || capsule.scene_id !== scene.id);
  const edit = (field: NativeSourceField, value: string) => {
    setNotice(null); setError(null); editor.setField(field, value);
  };

  useEffect(() => {
    const refresh = () => setImports(readFileImports(project.id));
    const changed = (event: Event) => { if ((event as CustomEvent<unknown>).detail === project.id) refresh(); };
    const storage = (event: StorageEvent) => {
      if (event.key === null || event.key === FILE_IMPORT_PREFIX + project.id
          || event.key.startsWith(FILE_IMPORT_PREFIX + project.id + ":")) refresh();
    };
    refresh();
    window.addEventListener(FILE_IMPORT_CHANGED, changed);
    window.addEventListener("storage", storage);
    return () => { window.removeEventListener(FILE_IMPORT_CHANGED, changed); window.removeEventListener("storage", storage); };
  }, [project.id]);

  const save = async () => {
    if (pending || !desktop || unavailable) return;
    setError(null); setNotice(null);
    try {
      assertNoFileImport(project.id);
      if (editor.fields.blenderRecipeExport) {
        const issues = blenderProductSceneIssues(project,scene,editor.capsuleId);
        if (issues.length) throw new Error(issues.join(" "));
      }
      if (!editor.assertReady()) return;
      const spec = prepareNativeSourceSpec(editor.fields, editor.capsuleId, scene.id);
      const sequence = editor.sequence;
      const request: NativeSourceSaveRequest = {
        path: editor.fields.path, spec,
        ...(editor.fields.blenderRecipeExport ? { expected_sha256: editor.fields.blenderRecipeExport } : {}),
        source: { project_id: project.id, generation: project.generation, revision: editor.baseRevision, branch_id: project.active_branch },
      };
      setSending(true);
      const current = await saveSource(request);
      // Later keystrokes belong to the author, even when this earlier save succeeds.
      if (!editor.acknowledge(sequence, current)) {
        setError("The original save returned, but this editor could not confirm the matching authored capsule. Read the saved project to reconcile this result; do not submit the same operation again automatically.");
        return;
      }
      setNotice("The original source save is confirmed and the latest project is open. Earlier source versions remain available; realization and fidelity need new evidence.");
    } catch (reason) { setError(message(reason)); }
    finally { setSending(false); setImports(readFileImports(project.id)); }
  };
  const exportOriginal = async () => {
    if (exportInFlight.current || pending || pendingImport || !desktop || !capsule || !sourceAsset || unavailable || !exportPath.trim()) return;
    setError(null); setExportStatus(null); setLastExport(null);
    if (sourceAsset.content_sha256 !== capsule.source_sha256 || capsule.scene_id !== scene.id) {
      setError("The saved capsule and original asset do not match. Read the saved project before exporting.");
      return;
    }
    const submittedPath = exportPath.trim();
    exportInFlight.current = true; setExporting(true);
    try {
      const result = await exportSource({
        path: submittedPath, scene_id: scene.id, capsule_id: capsule.id,
        expected_source_asset_id: sourceAsset.id,
        expected_source_sha256: capsule.source_sha256,
        source: { project_id: project.id, generation: project.generation,
          revision: project.revision, branch_id: project.active_branch },
      });
      if (result.capsule_id !== capsule.id || result.source_asset_id !== sourceAsset.id
          || result.source_sha256 !== capsule.source_sha256 || result.editor_launched !== false
          || result.path !== submittedPath
          || !/^[1-9][0-9]*$/.test(result.size_bytes)) {
        throw new Error("The export response does not match the selected saved source. Inspect the destination; no operation is retried.");
      }
      setLastExport({ path: result.path, sourceKey });
      setExportStatus("Original source exported to " + result.path
        + ". Open that exact file manually in its trusted native editor. No editor or source code was launched.");
    } catch (reason) { setError(message(reason)); }
    finally { exportInFlight.current = false; setExporting(false); }
  };
  const useExportedSourceAsNextInput = () => {
    if (!reusableExport || pending || pendingImport || !desktop || unavailable
        || editor.conflict || editor.recoveryProblem || editor.backupError) return;
    setError(null); setNotice(null);
    try {
      assertNoFileImport(project.id);
      if (editor.baseRevision !== project.revision) {
        throw new Error("The native source draft is based on an earlier revision. Review and rebase it before choosing this exported file.");
      }
      // This changes authored, retained input only. It never opens an editor,
      // reads the exported file, issues a grant or replays a source import.
      editor.setField("path", reusableExport.path);
      if (!editor.retryBackup()) {
        throw new Error("The exported path is visible as unsaved input but could not be retained safely. Recover your source input before attempting another Save.");
      }
      setNotice("The exported file path is retained as unsaved next-version input. Edit it in your trusted native editor, then explicitly choose Save source and capsule. No source was imported or executed here.");
    } catch (reason) { setError(message(reason)); }
  };
  const readCurrent = async () => {
    if (pending || !desktop) return;
    setError(null); setNotice(null); setReading(true);
    try {
      assertNoFileImport(project.id);
      await refreshProject();
      assertNoFileImport(project.id);
      // Reading does not rebase an authored input or acknowledge an import.
      setNotice("The latest saved project is open. Review its source below before choosing a new base for your input.");
    } catch (reason) { setError(message(reason)); }
    finally { setReading(false); setImports(readFileImports(project.id)); }
  };
  const rebase = () => {
    if (pending || unavailable) return;
    setError(null); setNotice(null);
    try { assertNoFileImport(project.id); editor.rebase(project); }
    catch (reason) { setError(message(reason)); }
  };
  const startAnother = () => {
    if (pending || target !== "new" || !capsule || editor.dirty) return;
    setError(null); setNotice(null);
    try { assertNoFileImport(project.id); editor.startAnother(project); }
    catch (reason) { setError(message(reason)); }
  };

  return <section className="production-shot native-source-editor" aria-labelledby={heading}>
    <div className="production-section-title"><Save size={18} aria-hidden="true"/><div>
      <h3 id={heading}>{capsule ? "Save a new source version" : "Save an authored source"}</h3>
      <p>Choose the file saved by your native editor. Its bytes and capsule metadata are saved in one project revision.</p>
    </div></div>
    <p className="production-help">Input based on r{editor.baseRevision} · saved project r{project.revision}. Source code is preserved without running it.</p>
    {capsule && <div className="production-notice" aria-label="Current saved native source">
      <strong>{capsule.label}</strong>
      <span>{sourceAsset?.name ?? "Source asset unavailable"} · {sourceAsset?.media_type ?? "Unknown media type"}</span>
      <span className="mono" style={{ overflowWrap: "anywhere" }}>sha256:{capsule.source_sha256}</span>
      <span>Replacing this source clears its previous realization and fidelity evidence.</span>
    </div>}
    {capsule && sourceAsset && (sourceAsset.media_type === BLENDER_RECIPE_MEDIA_TYPE
      || (sourceAsset.media_type === "application/x-blender" && capsule.editable_parameters.includes("product.annotation.body"))) &&
      <BlenderSourceInspector key={sourceKey} project={project} sceneId={scene.id}
        capsuleId={capsule.id} busy={pending || pendingImport} refreshProject={refreshProject}
        executionBlocked={editor.dirty || editor.conflict || !!editor.recoveryProblem || !!editor.backupError}/>}
    {capsule && <section className="production-notice" aria-label="Recover saved native source">
      <strong>Export the editable original</strong>
      <p className="production-help">Recover the saved source bytes without exporting the entire project. Choose a new absolute file path with the original extension. The export does not run an editor or execute imported content.</p>
      <label className="production-field"><span>Original source export path</span>
        <input aria-label="Native source export destination" value={exportPath} maxLength={1700}
          disabled={pending || !desktop} autoComplete="off" spellCheck={false}
          placeholder="Absolute destination with original filename and extension"
          onChange={event => { setExportPath(event.target.value); setExportStatus(null); setLastExport(null); setError(null); }}/>
      </label>
      {!sourceAsset || sourceAsset.content_sha256 !== capsule.source_sha256
        ? <p className="production-error" role="status">The saved original asset needs reconciliation before export.</p> : null}
      <button type="button" className="secondary-button" onClick={() => void exportOriginal()}
        disabled={!desktop || pending || pendingImport || unavailable || !exportPath.trim()
          || !sourceAsset || sourceAsset.content_sha256 !== capsule.source_sha256}>
        <Download size={14} aria-hidden="true"/>{exporting ? "Exporting original…" : "Export saved original source"}
      </button>
      {exportStatus && <p className="production-help" role="status">{exportStatus}</p>}
      {reusableExport && <button type="button" className="secondary-button"
        disabled={!desktop || pending || pendingImport || unavailable || editor.conflict
          || !!editor.recoveryProblem || !!editor.backupError || editor.baseRevision !== project.revision}
        onClick={useExportedSourceAsNextInput}>Use exported file as next version input</button>}
    </section>}
    {unavailable && <p role="alert" className="production-error">This capsule is no longer available in the selected scene. Its authored input has been kept.</p>}
    {(scene.renderer === "blender" || !!editor.fields.blenderRecipe) && <BlenderProductRecipeEditor key={editor.capsuleId}
      project={project} scene={scene} capsuleId={editor.capsuleId} fields={editor.fields} preferredCameraMotion={preferredCameraMotion}
      blocked={pending || pendingImport || unavailable || editor.conflict || !!editor.recoveryProblem || !!editor.backupError}
      change={patch => { setNotice(null); setError(null); return editor.setFields(patch); }}/>}
    <label className="production-field"><span>Source file path</span>
      <input value={editor.fields.path} onChange={event => edit("path", event.target.value)} autoComplete="off" spellCheck={false}/>
    </label>
    <div className="production-plan-grid">
      <label className="production-field"><span>Source file name</span><input value={editor.fields.name} onChange={event => edit("name", event.target.value)}/></label>
      <label className="production-field"><span>Source media type</span><input value={editor.fields.mediaType} onChange={event => edit("mediaType", event.target.value)} spellCheck={false}/></label>
      <label className="production-field"><span>Capsule label</span><input value={editor.fields.label} onChange={event => edit("label", event.target.value)}/></label>
      <label className="production-field"><span>Original editor</span><input value={editor.fields.editorHint} onChange={event => edit("editorHint", event.target.value)}/></label>
    </div>
    <details className="production-diff-details">
      <summary>Declared editable parameters</summary>
      <label className="production-field"><span>Editable parameters (JSON array)</span>
        <textarea rows={4} value={editor.fields.parameters} onChange={event => edit("parameters", event.target.value)} spellCheck={false}/>
      </label>
      <p className="production-help">Use an array of parameter names, such as ["headline", "accent"]. These declarations describe the source; they do not establish native editing support.</p>
    </details>
    {!desktop && <p className="production-help">Local source saving is available in Motionwright desktop. Your input can still be kept and downloaded here.</p>}
    {pendingImport && <p role="alert" className="production-error">{imports.error ?? (imports.limited
      ? "The retained-import list is incomplete. Read its recovery copies before saving another source."
      : "A saved import request needs review. Check its original result in Import recovery before saving another source.")}</p>}
    {editor.conflict && <div className="production-notice" role="status">
      <strong>The saved project changed while you were editing.</strong>
      <span>Your input remains based on r{editor.baseRevision}. Review the current source, then keep your input on r{project.revision} explicitly.</span>
      <button className="secondary-button" type="button" disabled={pending || pendingImport || unavailable || !!editor.recoveryProblem || !!editor.backupError} onClick={rebase}>Keep input on current revision</button>
    </div>}
    {(editor.recoveryProblem || editor.backupError) && <div className="production-notice" role="alert">
      <strong>Source input recovery needs attention</strong>
      <span>{editor.recoveryProblem ?? editor.backupError}</span>
      <button className="secondary-button" type="button" disabled={pending} onClick={() => { setError(null); editor.retryBackup(); }}>Read source input again</button>
    </div>}
    {error && <p className="production-error" role="alert">{error}</p>}
    {notice && <p className="production-help" role="status">{notice}</p>}
    {editor.notice && <p className="production-help" role="status">{editor.notice}</p>}
    {editor.dirty && <p className="production-help">Your authored input is kept locally and has not been confirmed as the saved source.</p>}
    <div className="production-button-row">
      <button type="button" className="primary-button" disabled={pending || pendingImport || !desktop || unavailable || editor.conflict || !!editor.recoveryProblem || !!editor.backupError}
        onClick={() => void save()}><Save size={14} aria-hidden="true"/>{sending ? "Saving source…" : "Save source and capsule"}</button>
      <button type="button" className="secondary-button" disabled={pending || pendingImport || !desktop} onClick={() => void readCurrent()}><RefreshCw size={14} aria-hidden="true"/>Read latest saved project</button>
      {target === "new" && capsule && <button type="button" className="secondary-button"
        disabled={pending || pendingImport || editor.dirty || editor.conflict || !!editor.recoveryProblem || !!editor.backupError}
        onClick={startAnother}>Start another source capsule</button>}
      <button type="button" className="secondary-button" onClick={() => { try { download(editor.downloadableInput, "motionwright-native-source-input.json"); } catch (reason) { setError(message(reason)); } }}><Download size={14} aria-hidden="true"/>Download source input</button>
      {editor.downloadableOriginals.length > 0 && <button type="button" className="secondary-button" onClick={() => { try {
        download(JSON.stringify({ schema: "motionwright.native-source-original-copies/1", copies: editor.downloadableOriginals }, null, 2) + "\n", "motionwright-native-source-originals.json");
      } catch (reason) { setError(message(reason)); } }}>Download original source copies</button>}
    </div>
  </section>;
}

function SceneSources(props: Props & { scene: Scene }) {
  const { project, scene } = props;
  const selectionKey = "motionwright.native-source-selection.v1:" + JSON.stringify([project.id, project.generation, project.active_branch, scene.id]);
  const [selection, setSelection] = useState(() => {
    try { return { target: window.sessionStorage.getItem(selectionKey) ?? "new", error: null as string | null }; }
    catch (reason) { return { target: "new", error: "The earlier source selection could not be read: " + message(reason) }; }
  });
  const capsules = project.production_design?.capsules.filter(capsule => capsule.scene_id === scene.id) ?? [];
  const validTarget = selection.target === "new" || (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(selection.target)
    && selection.target !== "00000000-0000-0000-0000-000000000000");
  const missingSelection = selection.target !== "new" && !capsules.some(capsule => capsule.id === selection.target);
  return <>
    {scene.renderer === "blender" && <BlenderExecutionPanel mode="lit" project={project} sceneId={scene.id}
      busy={props.busy} refreshProject={props.refreshProject}/>}
    <label className="production-field"><span>Source capsule to save</span>
      <select value={selection.target} onChange={event => {
        const target = event.target.value;
        try {
          window.sessionStorage.setItem(selectionKey, target);
          if (window.sessionStorage.getItem(selectionKey) !== target) throw new Error("The selected capsule was not retained.");
          setSelection({ target, error: null });
        } catch (reason) { setSelection({ target, error: "This selection may not survive reload: " + message(reason) }); }
      }}>
        <option value="new">New source capsule</option>
        {missingSelection && <option value={selection.target}>Earlier capsule unavailable</option>}
        {capsules.map(capsule => <option key={capsule.id} value={capsule.id}>{capsule.label}</option>)}
      </select>
    </label>
    {selection.error && <p role="alert" className="production-error">{selection.error}</p>}
    {validTarget ? <SourceEditor key={selection.target} {...props} scene={scene} target={selection.target}/>
      : <p role="alert" className="production-error">The earlier capsule selection is invalid. Choose a capsule above; its stored source inputs have not been changed.</p>}
  </>;
}

export default function NativeSourceWorkbench(props: Props) {
  if (!props.scene) return <p className="production-help">Choose a scene before saving an authored source.</p>;
  return <SceneSources key={JSON.stringify([props.project.id, props.project.generation, props.project.active_branch, props.scene.id])} {...props} scene={props.scene}/>;
}
