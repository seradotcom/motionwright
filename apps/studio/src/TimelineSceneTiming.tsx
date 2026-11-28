import { Scissors } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { observeSceneTimingReceipt, previewSceneSplit, previewSceneTrim } from "./api";
import type { SceneSplitPreview, SceneTrimPreview } from "./api";
import { formatBeatTime } from "./sceneBeatTiming";
import { finishSceneTiming, readSceneTimingAttempts, retainSceneTiming, sameSceneTime } from "./sceneTimingDraft";
import type { SceneTimingAttempt } from "./sceneTimingDraft";
import { formatEditorTimecode, parseEditorSeekTime, projectFrameIndex } from "./timecode";
import type { TimecodeMode } from "./timecode";
import { timelineSelectionScope } from "./timelineSelection";
import { seconds } from "./types";
import type { Project, RationalTime, SceneTimingChange } from "./types";

interface Props {
  project: Project;
  sceneId: string | null;
  playhead: number;
  rate: RationalTime;
  mode: TimecodeMode;
  commit: (project: Project, change: SceneTimingChange, requestId: string) => Promise<Project>;
  refresh: (project: Project) => Promise<Project>;
  onSaved: (id: string) => void;
}

type Operation = "split" | "trim";
type Preview =
  | ({ operation: "split" } & SceneSplitPreview)
  | ({ operation: "trim" } & SceneTrimPreview);

const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const operationFor = (change: SceneTimingChange): Operation => change.type === "split_scene" ? "split" : "trim";

function confirmEdit(next: Project, preview: Preview): string | null {
  const index = next.scenes.findIndex(item => item.id === preview.scene_id);
  if (index < 0) return null;
  const scene = next.scenes[index];
  if (preview.operation === "trim") {
    return sameSceneTime(scene.duration, preview.new_duration) ? scene.id : null;
  }
  const right = next.scenes[index + 1];
  return right?.id === preview.right_scene_id && sameSceneTime(right.start, preview.at)
    && sameSceneTime(scene.duration, preview.left_duration) && sameSceneTime(right.duration, preview.right_duration)
    ? right.id : null;
}

export default function TimelineSceneTiming({ project, sceneId, playhead, rate, mode, commit, refresh, onSaved }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const running = useRef(false);
  const scope = timelineSelectionScope(project);
  const [operation, setOperation] = useState<Operation>("split");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [attempts, setAttempts] = useState<SceneTimingAttempt[]>([]);
  const [fault, setFault] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [canSeparate, setCanSeparate] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const scene = project.scenes.find(item => item.id === sceneId);
  const current = preview && preview.revision === project.revision && scene?.id === preview.scene_id;
  const title = operation === "split" ? "Split scene" : "Trim scene end";

  const load = () => {
    try {
      const records = readSceneTimingAttempts(window.localStorage, project);
      setAttempts(records);
      return records;
    } catch (reason) {
      setFault(message(reason));
      return null;
    }
  };

  useEffect(() => {
    load();
    const changed = () => load();
    window.addEventListener("storage", changed);
    return () => window.removeEventListener("storage", changed);
    // The timeline remounts on project, generation or branch changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  const perform = async (action: () => Promise<void>) => {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setFault(null);
    try { await action(); }
    catch (reason) { setFault(message(reason)); }
    finally {
      running.current = false;
      setBusy(false);
      load();
    }
  };

  const open = (requested: Operation) => {
    if (running.current) return;
    setPreview(null);
    setCanSeparate(null);
    setFault(null);
    setNotice("");
    const records = load();
    setOperation(records?.length ? operationFor(records[0].change) : requested);
    dialog.current?.showModal();
    if (!scene || !records || records.length) return;
    void perform(async () => {
      const at = parseEditorSeekTime(projectFrameIndex(playhead, rate) + "f", rate, "ndf");
      if (requested === "split") {
        const change = { type: "split_scene", scene_id: scene.id, right_scene_id: crypto.randomUUID(), at } as const;
        setPreview({ operation: requested, ...await previewSceneSplit(project, change) });
      } else {
        const change = { type: "trim_scene_end", scene_id: scene.id, at } as const;
        setPreview({ operation: requested, ...await previewSceneTrim(project, change) });
      }
    });
  };

  const save = () => {
    if (!preview || !current || attempts.length) return;
    void perform(async () => {
      const change: SceneTimingChange = preview.operation === "split"
        ? { type: "split_scene", scene_id: preview.scene_id, right_scene_id: preview.right_scene_id, at: preview.at }
        : { type: "trim_scene_end", scene_id: preview.scene_id, at: preview.at };
      const attempt = { requestId: crypto.randomUUID(), revision: project.revision, change };
      retainSceneTiming(window.localStorage, project, attempt);
      setAttempts([attempt]);
      const next = await commit(project, change, attempt.requestId);
      const selected = confirmEdit(next, preview);
      if (timelineSelectionScope(next) !== scope || next.revision !== attempt.revision + 1 || !selected) {
        throw new Error("The original " + preview.operation + " response could not be confirmed. Check its saved receipt.");
      }
      finishSceneTiming(window.localStorage, project, attempt);
      onSaved(selected);
      setNotice(preview.operation === "split" ? "Scene split saved." : "Scene trim saved.");
      dialog.current?.close();
    });
  };

  const check = (attempt: SceneTimingAttempt, keepOriginal = false) => {
    setCanSeparate(null);
    void perform(async () => {
      const original = operationFor(attempt.change);
      const observe = () => observeSceneTimingReceipt(project, attempt.change, attempt.revision, attempt.requestId);
      let known = await observe();
      const saved = await refresh(project);
      if (timelineSelectionScope(saved) !== scope) throw new Error("Open the original project and branch to finish recovery.");
      const advanced = saved.revision > attempt.revision;
      if (!known && advanced) known = await observe();
      if (known) {
        if (!advanced) throw new Error("Refresh the saved project before using the original " + original + " receipt.");
        finishSceneTiming(window.localStorage, project, attempt);
        const selected = attempt.change.type === "split_scene" ? attempt.change.right_scene_id : attempt.change.scene_id;
        if (saved.scenes.some(item => item.id === selected)) onSaved(selected);
        setNotice("The original " + original + " was confirmed.");
        setPreview(null);
        dialog.current?.close();
      } else if (keepOriginal && advanced) {
        finishSceneTiming(window.localStorage, project, attempt, true);
        setPreview(null);
        setNotice("The original request was retained. You can prepare a " + original + " on the newer revision.");
        dialog.current?.close();
      } else {
        if (advanced) setCanSeparate(attempt.requestId);
        setFault("The original request has no verified " + original + " receipt.");
      }
    });
  };

  return <>
    {attempts.length > 0 ? <button type="button" className="button compact" onClick={() => open(operationFor(attempts[0].change))}>
      Check scene {operationFor(attempts[0].change)}
    </button> : scene && <>
      <button type="button" className="button compact" onClick={() => open("split")}>
        <Scissors size={13} aria-hidden="true" /> Split scene
      </button>
      <button type="button" className="button compact" onClick={() => open("trim")}>Trim scene end</button>
    </>}
    {notice && <span className="timeline-view-warning" role="status">{notice}</span>}
    <dialog ref={dialog} className="timeline-marker-dialog" aria-labelledby="timeline-scene-timing-title"
      onCancel={event => { if (busy) event.preventDefault(); }}>
      <h2 id="timeline-scene-timing-title">{title}</h2>
      {busy && <p role="status">Checking the saved scene…</p>}
      {fault && <p role="alert">{fault}</p>}
      {attempts.map(attempt => <div key={attempt.requestId} className="marker-recovery-list">
        <span>Original scene {operationFor(attempt.change)} from revision {attempt.revision}</span>
        <button type="button" className="button compact" disabled={busy}
          onClick={() => check(attempt)}>Check original {operationFor(attempt.change)}</button>
        {canSeparate === attempt.requestId && <button type="button" className="button compact" disabled={busy}
          onClick={() => check(attempt, true)}>Keep original and close</button>}
      </div>)}
      {preview && attempts.length === 0 && <div className="marker-recovery-list">
        <p>{scene?.name} · {formatEditorTimecode(seconds(preview.at), rate, mode)}</p>
        {preview.operation === "split" ? <>
          <p>{seconds(preview.left_duration).toFixed(3)} s before · {seconds(preview.right_duration).toFixed(3)} s after.</p>
          <p>Both scenes keep their editable objects and the absolute positions of their beats. The cut keeps later scenes and audio on the same clock.</p>
          {preview.animated_objects > 0 && <p>The continuation starts with the pose at the cut. Active motion keeps its timing, and constant portions become static poses.</p>}
        </> : <>
          <p>{formatBeatTime(preview.old_duration)} s → {formatBeatTime(preview.new_duration)} s</p>
          <p>{preview.shifted_scenes === 1 ? "1 following scene moves earlier"
            : preview.shifted_scenes > 1 ? preview.shifted_scenes + " following scenes move earlier"
            : "The scene sequence ends here."}</p>
          <p>Audio and markers keep their absolute positions.</p>
        </>}
        {!current && <p role="alert">The saved scene changed. Close this preview and prepare a new {preview.operation}.</p>}
        <button type="button" className="button button-primary compact" disabled={busy || !current}
          onClick={save}>Save scene {preview.operation}</button>
      </div>}
      <button type="button" className="button compact" disabled={busy} onClick={() => dialog.current?.close()}>Close</button>
    </dialog>
  </>;
}
