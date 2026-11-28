import { Scissors } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { observeSceneSplitReceipt, previewSceneSplit } from "./api";
import type { SceneSplitPreview } from "./api";
import { finishSceneSplit, readSceneSplitAttempts, retainSceneSplit, sameSplitTime } from "./sceneSplitDraft";
import type { SceneSplitAttempt } from "./sceneSplitDraft";
import { formatEditorTimecode, parseEditorSeekTime, projectFrameIndex } from "./timecode";
import type { TimecodeMode } from "./timecode";
import { timelineSelectionScope } from "./timelineSelection";
import { seconds } from "./types";
import type { Project, RationalTime, SceneSplitChange } from "./types";

interface Props {
  project: Project;
  sceneId: string | null;
  playhead: number;
  rate: RationalTime;
  mode: TimecodeMode;
  commit: (change: SceneSplitChange, requestId: string) => Promise<Project>;
  refresh: (project: Project) => Promise<Project>;
  onSaved: (id: string) => void;
}

const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);

export default function TimelineSceneSplit({ project, sceneId, playhead, rate, mode, commit, refresh, onSaved }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const running = useRef(false);
  const scope = timelineSelectionScope(project);
  const [preview, setPreview] = useState<SceneSplitPreview | null>(null);
  const [attempts, setAttempts] = useState<SceneSplitAttempt[]>([]);
  const [fault, setFault] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [canSeparate, setCanSeparate] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const scene = project.scenes.find(item => item.id === sceneId);
  const current = preview && preview.revision === project.revision && scene?.id === preview.scene_id;

  const load = () => {
    try {
      const records = readSceneSplitAttempts(window.localStorage, project);
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

  const open = () => {
    if (running.current) return;
    setPreview(null);
    setCanSeparate(null);
    setFault(null);
    setNotice("");
    dialog.current?.showModal();
    const records = load();
    if (!scene || !records || records.length) return;
    void perform(async () => {
      const at = parseEditorSeekTime(projectFrameIndex(playhead, rate) + "f", rate, "ndf");
      const change = { type: "split_scene", scene_id: scene.id, right_scene_id: crypto.randomUUID(), at } as const;
      setPreview(await previewSceneSplit(project, change));
    });
  };

  const save = () => {
    if (!preview || !current || attempts.length) return;
    void perform(async () => {
      const change = { type: "split_scene", scene_id: preview.scene_id, right_scene_id: preview.right_scene_id, at: preview.at } as const;
      const attempt = { requestId: crypto.randomUUID(), revision: project.revision, change };
      retainSceneSplit(window.localStorage, project, attempt);
      setAttempts([attempt]);
      const next = await commit(change, attempt.requestId);
      const left = next.scenes.findIndex(item => item.id === change.scene_id);
      const right = next.scenes[left + 1];
      if (timelineSelectionScope(next) !== scope || next.revision !== attempt.revision + 1 || left < 0
          || right?.id !== change.right_scene_id || !sameSplitTime(right.start, change.at)
          || !sameSplitTime(next.scenes[left].duration, preview.left_duration)
          || !sameSplitTime(right.duration, preview.right_duration)) {
        throw new Error("The original split response could not be confirmed. Check its saved receipt.");
      }
      finishSceneSplit(window.localStorage, project, attempt);
      onSaved(right.id);
      setNotice("Scene split saved.");
      dialog.current?.close();
    });
  };

  const check = (attempt: SceneSplitAttempt, keepOriginal = false) => {
    setCanSeparate(null);
    void perform(async () => {
      const observe = () => observeSceneSplitReceipt(project, attempt.change, attempt.revision, attempt.requestId);
      let known = await observe();
      const saved = await refresh(project);
      if (timelineSelectionScope(saved) !== scope) throw new Error("Open the original project and branch to finish recovery.");
      const advanced = saved.revision > attempt.revision;
      if (!known && advanced) known = await observe();
      if (known) {
        if (!advanced) throw new Error("Refresh the saved project before using the original split receipt.");
        finishSceneSplit(window.localStorage, project, attempt);
        if (saved.scenes.some(item => item.id === attempt.change.right_scene_id)) onSaved(attempt.change.right_scene_id);
        setNotice("The original split was confirmed.");
        setPreview(null);
        dialog.current?.close();
      } else if (keepOriginal && advanced) {
        finishSceneSplit(window.localStorage, project, attempt, true);
        setPreview(null);
        setNotice("The original request was retained. You can prepare a split on the newer revision.");
        dialog.current?.close();
      } else {
        if (advanced) setCanSeparate(attempt.requestId);
        setFault("The original request has no verified split receipt.");
      }
    });
  };

  return <>
    {(scene || attempts.length > 0) && <button type="button" className="button compact" onClick={open}>
      <Scissors size={13} aria-hidden="true" /> {attempts.length ? "Check scene split" : "Split scene"}
    </button>}
    {notice && <span className="timeline-view-warning" role="status">{notice}</span>}
    <dialog ref={dialog} className="timeline-marker-dialog" aria-label="Split scene"
      onCancel={event => { if (busy) event.preventDefault(); }}>
      <h2>Split scene</h2>
      {busy && <p role="status">Checking the saved scene…</p>}
      {fault && <p role="alert">{fault}</p>}
      {attempts.map(attempt => <div key={attempt.requestId} className="marker-recovery-list">
        <span>Original scene split from revision {attempt.revision}</span>
        <button type="button" className="button compact" disabled={busy} onClick={() => check(attempt)}>Check original split</button>
        {canSeparate === attempt.requestId && <button type="button" className="button compact" disabled={busy}
          onClick={() => check(attempt, true)}>Keep original and close</button>}
      </div>)}
      {preview && attempts.length === 0 && <div className="marker-recovery-list">
        <p>{scene?.name} · {formatEditorTimecode(seconds(preview.at), rate, mode)}</p>
        <p>{seconds(preview.left_duration).toFixed(3)} s before · {seconds(preview.right_duration).toFixed(3)} s after.</p>
        <p>Both scenes keep their editable objects and the absolute positions of their beats. The cut keeps later scenes and audio on the same clock.</p>
        {!current && <p role="alert">The saved scene changed. Close this preview and prepare a new split.</p>}
        <button type="button" className="button button-primary compact" disabled={busy || !current} onClick={save}>Save scene split</button>
      </div>}
      <button type="button" className="button compact" disabled={busy} onClick={() => dialog.current?.close()}>Close</button>
    </dialog>
  </>;
}
