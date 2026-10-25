import { useEffect, useId, useState } from "react";
import { Download, RefreshCw, Save } from "lucide-react";
import type { Project, Scene } from "./types";
import {
  FILE_IMPORT_CHANGED, FILE_IMPORT_PREFIX, assertNoFileImport, readFileImports,
  type NativeCapsuleSourceSpec7,
} from "./fileImport";
import { prepareNativeSourceSpec, useNativeSourceDraft, type NativeSourceField } from "./nativeSourceDraft";

export interface NativeSourceSaveRequest {
  path: string;
  spec: NativeCapsuleSourceSpec7;
  source: { project_id: string; generation: string; revision: number; branch_id: string };
}
export interface NativeSourceServices {
  saveSource: (request: NativeSourceSaveRequest) => Promise<Project>;
  refreshProject: () => Promise<Project>;
}
interface Props extends NativeSourceServices { project: Project; scene: Scene | null; busy: boolean; }
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);

function download(content: string, filename: string) {
  const url = URL.createObjectURL(new Blob([content], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url; link.download = filename; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

/** Authored local input only. The existing App coordinator publishes every save. */
function SourceEditor({ project, scene, target, busy, saveSource, refreshProject }: Omit<Props, "scene"> & { scene: Scene; target: string }) {
  const editor = useNativeSourceDraft(project, scene.id, target);
  const heading = useId();
  const [imports, setImports] = useState(() => readFileImports(project.id));
  const [sending, setSending] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const desktop = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
  const capsule = project.production_design?.capsules.find(value => value.id === editor.capsuleId);
  const sourceAsset = project.assets.find(value => value.id === capsule?.source_asset_id);
  const pendingImport = imports.error !== null || imports.limited || imports.copies.length !== 0;
  const pending = busy || sending || reading;
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
      if (!editor.assertReady()) return;
      const spec = prepareNativeSourceSpec(editor.fields, editor.capsuleId, scene.id);
      const sequence = editor.sequence;
      const request: NativeSourceSaveRequest = {
        path: editor.fields.path, spec,
        source: { project_id: project.id, generation: project.generation, revision: editor.baseRevision, branch_id: project.active_branch },
      };
      setSending(true);
      const current = await saveSource(request);
      // Later keystrokes belong to the author, even when this earlier save succeeds.
      editor.acknowledge(sequence, current);
      setNotice("The original source save is confirmed and the latest project is open. Earlier source versions remain available; realization and fidelity need new evidence.");
    } catch (reason) { setError(message(reason)); }
    finally { setSending(false); setImports(readFileImports(project.id)); }
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
    {unavailable && <p role="alert" className="production-error">This capsule is no longer available in the selected scene. Its authored input has been kept.</p>}
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
