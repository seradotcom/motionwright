import "./audioDraft.css";
import {
  AudioLines,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleDashed,
  FileAudio,
  LocateFixed,
  Plus,
  Save,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import SemanticSfxWorkbench from "./SemanticSfxWorkbench";
import NarrationTakeReview from "./NarrationTakeReview";
import {narrationCorrectionFocus,type NarrationCorrectionTarget} from "./narrationRevisionReview";
import NativeOriginalMixReview from "./NativeOriginalMixReview";
import type {
  AudioCue,
  Change,
  Project,
  RationalTime,
  TranscriptSegment,
  WaveformPage,
} from "./types";
import {
  activeVoiceGroup, activeVoiceIsUnconfirmed, activeVoiceReviewMatches, inspectActiveVoiceOutcome,
  prepareActiveVoiceSelection, prepareSeparateActiveVoiceIntent, retainActiveVoiceRecord, sendRetainedActiveVoiceSelection,
  AUDIO_COPY_LIMIT, AUDIO_DRAFT_PREFIX, audioCanonicalFields, audioConflicts,
  audioDraftHasWork, audioDraftScope, audioFieldLabels, audioFields, audioGroupName,
  audioRecoveryKey, createAudioDraft, decodeAudioRecovery, editAudioField,
  observeAudioDraft, prepareAudioSubmission, readAudioRecoveries,
  reconcileAudioDraft, resolveAudioField, retainAudioSubmission, writeAudioRecovery,
} from "./audioDraft";
import type { AudioDraft, AudioDraftGroup, AudioRecovery, AudioRecoveryCopy, AudioSubmission, ActiveVoiceOutcomeReview } from "./audioDraft";
import { bootstrap, projectHistory, waveformPage as loadWaveformPage } from "./api";

type Commit = (change: Change) => Promise<void>;

const WAVEFORM_PAGE_SIZE = 192;

const valueOf = (time: RationalTime) => Number(time.num) / Number(time.den);
const secondsLabel = (time: RationalTime) => {
  const value = valueOf(time);
  return Number.isFinite(value) ? value.toFixed(3) + " s" : "UNKNOWN";
};


const audioDocumentWriter = crypto.randomUUID();
const audioSessionPointer = "motionwright.audio-session.v1:";
type AudioLiveDraft = { draft: AudioDraft; writer: string; warning: string | null; blocked: boolean; blockedKey: string | null; attemptInFlight?: boolean; custodyPending?: boolean; notice?: string | null };
const liveAudioDrafts = new Map<string, AudioLiveDraft>();
const messageOf = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const audioProjectIdentity = (project: Project) => JSON.stringify([project.id, project.generation, project.active_branch]);

function useAudioEditor(project: Project, group: AudioDraftGroup, commit: Commit) {
  const scope = audioDraftScope(project, group);
  const [initial] = useState(() => {
    const cached = liveAudioDrafts.get(scope);
    let copies: AudioRecoveryCopy[] = [], limited = false, warning: string | null = cached?.warning ?? null;
    let previous: string | null = null;
    try { ({ copies, limited } = readAudioRecoveries(window.localStorage, scope, group)); }
    catch (reason) { warning = messageOf(reason); }
    try {
      // A duplicated tab may inherit sessionStorage. Only this document's reload
      // may resume its prior writer; other documents choose copies explicitly.
      if (!window.opener && performance.getEntriesByType("navigation").some(entry => (entry as PerformanceNavigationTiming).type === "reload")) {
        previous = window.sessionStorage.getItem(audioSessionPointer + scope);
      }
    } catch { /* A fresh writer can still preserve input in this document. */ }
    const own = copies.find(copy => copy.writer === previous);
    const restored = cached?.draft ?? own?.value?.draft;
    const blocked = cached?.blocked ?? Boolean(own && !own.value);
    const draft = restored ? observeAudioDraft(restored, project).draft : createAudioDraft(project, group);
    return { draft, writer: cached?.writer ?? (own?.value ? own.writer : audioDocumentWriter), copies, limited, warning, blocked,
      blockedKey: cached?.blockedKey ?? (own && !own.value ? own.key : null),
      notice: restored && audioDraftHasWork(draft) ? "Recovered local audio inputs. Compare them with the saved project before committing." : null };
  });
  const [draft, setDraft] = useState(initial.draft), draftRef = useRef(draft), projectRef = useRef(project);
  projectRef.current = project;
  const writer = useRef(initial.writer), mounted = useRef(true), inFlight = useRef(false);
  const [copies, setCopies] = useState(initial.copies), [limited, setLimited] = useState(initial.limited);
  const [warning, setWarning] = useState(initial.warning), warningRef = useRef(initial.warning);
  const [blocked, setBlocked] = useState(initial.blocked), blockedRef = useRef(initial.blocked);
  const blockedKey = useRef(initial.blockedKey);
  const [notice, setNotice] = useState<string | null>(initial.notice), [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  function remember(next: AudioDraft) {
    draftRef.current = next;
    if (audioDraftHasWork(next) || blockedRef.current) liveAudioDrafts.set(scope,
      { draft: next, writer: writer.current, warning: warningRef.current, blocked: blockedRef.current, blockedKey: blockedKey.current });
    else liveAudioDrafts.delete(scope);
  }
  function refresh() {
    try { const result = readAudioRecoveries(window.localStorage, scope, group); setCopies(result.copies); setLimited(result.limited); }
    catch (reason) { warningRef.current = messageOf(reason); setWarning(warningRef.current); remember(draftRef.current); }
  }
  function backup(next: AudioDraft) {
    if (blockedRef.current) return false;
    try {
      writeAudioRecovery(window.localStorage, { schema: "motionwright.audio-recovery/1", scope, writer: writer.current, updatedAt: Date.now(), draft: next });
      try { window.sessionStorage.setItem(audioSessionPointer + scope, writer.current); } catch { /* Copies remain explicit recovery choices. */ }
      warningRef.current = null; setWarning(null); remember(next); return true;
    } catch (reason) {
      warningRef.current = messageOf(reason) + " Current inputs remain in this session; the latest edit may not survive closing or reloading.";
      setWarning(warningRef.current); remember(next); return false;
    }
  }
  function replace(next: AudioDraft) { remember(next); setDraft(next); backup(next); refresh(); window.dispatchEvent(new Event("motionwright-audio-draft")); }
  function observe(current: Project) {
    const result = observeAudioDraft(draftRef.current, current);
    if (result.acknowledged) { setError(null); setNotice("Submitted audio values match saved revision r" + current.revision + ". Newer inputs are retained."); }
    if (JSON.stringify(result.draft) !== JSON.stringify(draftRef.current)) replace(result.draft);
    return result.acknowledged;
  }
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; remember(draftRef.current); };
    // Each project/generation/branch/source owns a keyed component instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { observe(project); }, [project]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const listener = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(AUDIO_DRAFT_PREFIX)) refresh(); };
    window.addEventListener("storage", listener); return () => window.removeEventListener("storage", listener);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);
  const conflicts = audioConflicts(draft, project);
  const contentLock = project.locks.some(lock => lock.resource === "project:" + project.id && lock.kind === "content");
  const timingLock = project.locks.some(lock => lock.resource === "project:" + project.id && lock.kind === "timing");
  const locked = group.kind === "mix" ? contentLock : !!project.production_design?.narration_take_lock || timingLock
    || ((group.kind === "transcript" || group.kind === "new-transcript") && contentLock);
  function edit(field: string, raw: string) { setError(null); setNotice(null); replace(editAudioField(draftRef.current, field, raw)); }
  async function save() {
    if (inFlight.current || locked || blockedRef.current) return;
    let attempt: AudioSubmission;
    try { attempt = prepareAudioSubmission(draftRef.current, projectRef.current); }
    catch (reason) { setError(messageOf(reason)); return; }
    inFlight.current = true; setPending(true); setError(null); setNotice(null);
    replace(retainAudioSubmission(reconcileAudioDraft(draftRef.current, projectRef.current), attempt));
    try {
      await commit(attempt.change);
      if (mounted.current && draftRef.current.awaiting && !observe(projectRef.current)) {
        setError("Saving is not confirmed by a matching project revision. Your input and submitted snapshot are retained.");
      }
    } catch (reason) {
      if (mounted.current) setError(messageOf(reason) + " Your input and submitted snapshot are retained.");
    } finally { inFlight.current = false; if (mounted.current) setPending(false); }
  }
  async function remove(change: Change) {
    if (inFlight.current || locked || blockedRef.current) return;
    if (audioDraftHasWork(draftRef.current) && !backup(draftRef.current)) { setError("Keep this editor open until its draft can be backed up before removing the saved item."); return; }
    inFlight.current = true; setPending(true); setError(null);
    try { await commit(change); if (mounted.current && audioCanonicalFields(projectRef.current, group)) setError("Removal has not been confirmed. Your draft is retained."); }
    catch (reason) { if (mounted.current) setError(messageOf(reason)); }
    finally { inFlight.current = false; if (mounted.current) setPending(false); }
  }
  function resolve(field: string, choice: "saved" | "draft", focusId: string) {
    if (inFlight.current) return;
    replace(resolveAudioField(draftRef.current, projectRef.current, field, choice)); setError(null);
    setNotice(choice === "saved" ? "Kept this saved field. Other inputs are unchanged." : "Kept this draft against the current revision. Save explicitly to apply it.");
    document.getElementById(focusId)?.focus();
  }
  function restore(copy: AudioRecoveryCopy) {
    if (inFlight.current || !copy.value) return;
    try {
      if (window.localStorage.getItem(copy.key) !== copy.raw) { refresh(); setError("This recovery copy changed. Choose its refreshed version before restoring."); return; }
    } catch (reason) { setError(messageOf(reason)); return; }
    if (audioDraftHasWork(draftRef.current) && !backup(draftRef.current)) { setError("Back up the current input before restoring another copy."); return; }
    writer.current = crypto.randomUUID(); blockedRef.current = false; blockedKey.current = null; setBlocked(false);
    replace(observeAudioDraft(copy.value.draft, projectRef.current).draft); setError(null);
    setNotice("Restored a separate local audio copy. Saving remains explicit."); refresh();
  }
  function discardCopy(copy: AudioRecoveryCopy) {
    if (inFlight.current) return;
    try {
      if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("This recovery copy changed. Refresh the list before discarding it.");
      window.localStorage.removeItem(copy.key);
      if (blockedRef.current && copy.key === blockedKey.current) {
        blockedRef.current = false; blockedKey.current = null; setBlocked(false); writer.current = crypto.randomUUID(); backup(draftRef.current);
      }
      refresh();
    } catch (reason) { setError(messageOf(reason)); }
  }
  let invalid: string | null = null;
  try { if (!draft.awaiting) prepareAudioSubmission(draft, project); } catch (reason) { invalid = messageOf(reason); }
  return { draft, fields: draft.fields, scope, pending, locked, conflicts, copies, limited, warning, blocked, notice, error, invalid,
    sourceProtected: group.kind !== "mix" && !!project.production_design?.narration_take_lock,
    canSave: !pending && !locked && !blocked && !invalid && !draft.awaiting,
    edit, save, remove, resolve, restore, discardCopy, refresh,
    retryBackup: () => { backup(draftRef.current); refresh(); },
    reviewUnconfirmed: () => {
      if (inFlight.current) return;
      replace(reconcileAudioDraft({ ...draftRef.current, awaiting: null }, projectRef.current));
      setError(null); setNotice("Retained all inputs against the observed project. Resolve changed fields before retrying; no request was replayed.");
    },
    discardDraft: () => {
      if (inFlight.current || blockedRef.current) return;
      replace(createAudioDraft(projectRef.current, group)); setError(null); setNotice("Discarded this local audio draft. The saved project is unchanged.");
    },
  };
}
type AudioEditor = ReturnType<typeof useAudioEditor>;
function AudioDraftFeedback({ editor, group, fieldId }: { editor: AudioEditor; group: AudioDraftGroup; fieldId: (field: string) => string }) {
  const label = audioGroupName(group);
  return <div className="audio-draft-feedback" style={{ gridColumn: "1 / -1", minWidth: 0 }} aria-label={label + " draft status"}>
    {editor.notice && <p className="production-notice" role="status">{editor.notice}</p>}
    {editor.error && <p className="production-error" role="alert">{editor.error}</p>}
    {editor.invalid && audioDraftHasWork(editor.draft) && !editor.draft.awaiting && !editor.conflicts.length && <p role="status">{editor.invalid}</p>}
    {editor.locked && <p role="status">{editor.sourceProtected ? "The original narration is protected. Retained inputs and recovery remain available; editing resumes after the explicit recorded-source release."
      : "The saved audio is locked. Local drafting and recovery remain available; saving requires the existing lock to be released."}</p>}
    {editor.warning && <div className="production-notice" role="alert"><p>{editor.warning}</p>
      <button className="button compact" type="button" onClick={editor.retryBackup}>Retry {label.toLowerCase()} backup</button></div>}
    {editor.blocked && <p className="production-error" role="alert">An unreadable copy from this editor was preserved. Discard that copy explicitly before saving; new typing is still retained.</p>}
    {editor.draft.awaiting && !editor.pending && <div className="production-notice"><p>A submitted snapshot is still unconfirmed. Reload the saved project to check its result.</p>
      <button className="button compact" type="button" onClick={editor.reviewUnconfirmed}>Keep {label.toLowerCase()} draft for review</button></div>}
    {editor.conflicts.length > 0 && <section className="audio-draft-conflicts" aria-label={label + " conflicts"}>
      <strong>Review overlapping audio changes</strong>
      {editor.conflicts.map(conflict => <section className="audio-draft-conflict" key={conflict.field} aria-label={audioFieldLabels[conflict.field] + " conflict"}>
        <h4>{audioFieldLabels[conflict.field]}</h4>
        <dl><dt>Draft started from</dt><dd style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{conflict.before || "(empty)"}</dd>
          <dt>Saved now</dt><dd style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{conflict.current || "(empty)"}</dd>
          <dt>Your draft</dt><dd style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{conflict.proposed || "(empty)"}</dd></dl>
        {conflict.futureRevision && <p>This copy began at a later revision. Choose its new base explicitly.</p>}
        <div className="audio-draft-actions">
          <button className="button compact" type="button" disabled={editor.pending} onClick={() => editor.resolve(conflict.field, "saved", fieldId(conflict.field))}>Keep saved {audioFieldLabels[conflict.field]}</button>
          <button className="button compact" type="button" disabled={editor.pending} onClick={() => editor.resolve(conflict.field, "draft", fieldId(conflict.field))}>Keep my draft {audioFieldLabels[conflict.field]}</button>
        </div>
      </section>)}
    </section>}
    {(editor.copies.length > 0 || editor.limited) && <details><summary>Local {label.toLowerCase()} recovery copies</summary>
      {editor.limited && <p role="status">Only the first 128 copies were inspected. Nothing was removed automatically.</p>}
      {editor.copies.map((copy, index) => <div className="audio-draft-recovery-copy" role="group" aria-label={"Local audio copy " + (index + 1)} key={copy.key}>
        <span>{copy.value ? "Saved locally " + new Date(copy.value.updatedAt).toLocaleString() : "Unreadable copy, original bytes preserved"}</span>
        <div className="audio-draft-actions">
          {copy.value && <button className="button compact" type="button" disabled={editor.pending} onClick={() => editor.restore(copy)}>Restore copy</button>}
          <button className="button compact" type="button" disabled={editor.pending} onClick={() => editor.discardCopy(copy)}>Discard copy</button>
        </div>
      </div>)}
    </details>}
    {audioDraftHasWork(editor.draft) && <button className="button compact" type="button" disabled={editor.pending || editor.blocked} onClick={editor.discardDraft}>Discard {label.toLowerCase()} draft</button>}
  </div>;
}

function TranscriptRow({ segment, project, commit, playhead, onSeek }: {
  segment: TranscriptSegment; project: Project; commit: Commit; playhead: number; onSeek: (seconds: number) => void;
}) {
  const group: AudioDraftGroup = { kind: "transcript", id: segment.id, voiceId: segment.voice_track_id };
  const editor = useAudioEditor(project, group, commit);
  const fieldId = (field: string) => "audio-transcript-" + segment.id + "-" + field;
  const control = (field: string) => ({ id: fieldId(field), value: editor.fields[field].raw, disabled: editor.sourceProtected,
    "aria-invalid": editor.conflicts.some(conflict => conflict.field === field),
    onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => editor.edit(field, event.target.value) });
  const current = playhead >= valueOf(segment.start) && playhead < valueOf(segment.end);
  return <div className={"audio-transcript-row" + (current ? " playback-current" : "")} aria-current={current ? "true" : undefined}>
    <div className="audio-time-pair"><input aria-label={"Start " + segment.id} inputMode="decimal" {...control("start")}/><span>→</span>
      <input aria-label={"End " + segment.id} inputMode="decimal" {...control("end")}/></div>
    <input aria-label={"Speaker " + segment.id} maxLength={256} placeholder="Speaker" {...control("speaker")}/>
    <textarea aria-label={"Transcript " + segment.id} maxLength={8000} {...control("text")}/>
    <span className={"evidence-chip evidence-" + segment.alignment.kind}>{segment.alignment.kind.toUpperCase()}</span>
    <div className="audio-row-actions">
      <button type="button" className="icon-action" title="Seek project timeline to transcript start" aria-label={"Seek to transcript at " + secondsLabel(segment.start)} onClick={() => onSeek(valueOf(segment.start))}><LocateFixed size={14}/></button>
      <button type="button" className="icon-action" title="Save transcript segment as explicit manual alignment" disabled={!editor.canSave} onClick={() => void editor.save()}><Save size={14}/></button>
      <button type="button" className="icon-action" title="Remove transcript segment" disabled={editor.locked || editor.pending || editor.blocked} onClick={() => void editor.remove({ type: "remove_transcript_segment", segment_id: segment.id })}><Trash2 size={14}/></button>
    </div>
    <AudioDraftFeedback editor={editor} group={group} fieldId={fieldId}/>
  </div>;
}

function CueRow({ cue, project, commit, playhead, onSeek }: {
  cue: AudioCue; project: Project; commit: Commit; playhead: number; onSeek: (seconds: number) => void;
}) {
  const group: AudioDraftGroup = { kind: "cue", id: cue.id }, editor = useAudioEditor(project, group, commit);
  const fieldId = (field: string) => "audio-cue-" + cue.id + "-" + field;
  const current = Math.abs(playhead - valueOf(cue.at)) < 1 / 24;
  return <div className={"audio-cue-row" + (current ? " playback-current" : "")} aria-current={current ? "true" : undefined}>
    <input id={fieldId("at")} aria-label={"Cue time " + cue.id} disabled={editor.sourceProtected} value={editor.fields.at.raw} inputMode="decimal" onChange={event => editor.edit("at", event.target.value)}/>
    <input id={fieldId("label")} aria-label={"Cue label " + cue.id} disabled={editor.sourceProtected} value={editor.fields.label.raw} maxLength={512} onChange={event => editor.edit("label", event.target.value)}/>
    <span className={"evidence-chip evidence-" + cue.evidence}>{cue.evidence.toUpperCase()}</span>
    <button type="button" className="icon-action" title="Seek project timeline to cue" aria-label={"Seek to cue " + cue.label} onClick={() => onSeek(valueOf(cue.at))}><LocateFixed size={14}/></button>
    <button type="button" className="icon-action" title="Save cue" disabled={!editor.canSave} onClick={() => void editor.save()}><Save size={14}/></button>
    <button type="button" className="icon-action" title="Remove cue" disabled={editor.locked || editor.pending || editor.blocked} onClick={() => void editor.remove({ type: "remove_audio_cue", cue_id: cue.id })}><Trash2 size={14}/></button>
    <AudioDraftFeedback editor={editor} group={group} fieldId={fieldId}/>
  </div>;
}

function NewTranscript({ project, commit, voiceId }: { project: Project; commit: Commit; voiceId: string | null }) {
  const group: AudioDraftGroup = { kind: "new-transcript", voiceId }, editor = useAudioEditor(project, group, commit);
  const fieldId = (field: string) => "new-transcript-" + field;
  return <div className="audio-transcript-new" aria-label="New transcript draft">
    <div className="audio-time-pair"><input id={fieldId("start")} aria-label="New transcript start" disabled={editor.sourceProtected} value={editor.fields.start.raw} inputMode="decimal" placeholder="start s" onChange={event => editor.edit("start", event.target.value)}/><span>→</span>
      <input id={fieldId("end")} aria-label="New transcript end" disabled={editor.sourceProtected} value={editor.fields.end.raw} inputMode="decimal" placeholder="end s" onChange={event => editor.edit("end", event.target.value)}/></div>
    <input id={fieldId("speaker")} aria-label="New transcript speaker" disabled={editor.sourceProtected} value={editor.fields.speaker.raw} maxLength={256} placeholder="Speaker" onChange={event => editor.edit("speaker", event.target.value)}/>
    <textarea id={fieldId("text")} aria-label="New transcript text" disabled={editor.sourceProtected} value={editor.fields.text.raw} maxLength={8000} placeholder={voiceId ? "Enter transcript text" : "Select a measured take before saving"} onChange={event => editor.edit("text", event.target.value)}/>
    <button type="button" className="button" disabled={!editor.canSave} onClick={() => void editor.save()}><Plus size={14}/> Add manual segment</button>
    <span>Edited times use milliseconds. Existing unedited rational timings remain exact.</span>
    <AudioDraftFeedback editor={editor} group={group} fieldId={fieldId}/>
  </div>;
}

function NewCue({ project, commit }: { project: Project; commit: Commit }) {
  const group: AudioDraftGroup = { kind: "new-cue" }, editor = useAudioEditor(project, group, commit);
  const fieldId = (field: string) => "new-cue-" + field;
  return <div className="audio-cue-new" aria-label="New cue draft">
    <input id={fieldId("at")} aria-label="New cue time" disabled={editor.sourceProtected} value={editor.fields.at.raw} inputMode="decimal" placeholder="time s" onChange={event => editor.edit("at", event.target.value)}/>
    <input id={fieldId("label")} aria-label="New cue label" disabled={editor.sourceProtected} value={editor.fields.label.raw} maxLength={512} placeholder="Cue label" onChange={event => editor.edit("label", event.target.value)}/>
    <button type="button" className="button" disabled={!editor.canSave} onClick={() => void editor.save()}><Plus size={14}/> Add cue</button>
    <AudioDraftFeedback editor={editor} group={group} fieldId={fieldId}/>
  </div>;
}

function MixEditor({ project, commit }: { project: Project; commit: Commit }) {
  const group: AudioDraftGroup = { kind: "mix" }, editor = useAudioEditor(project, group, commit);
  const fieldId = (field: string) => "audio-mix-" + field;
  return <section className="audio-editor-section audio-mix-section" aria-label="Global mix intent">
    <header><div><strong>Mix intent</strong><span>Targets are intent; they are not measured output evidence.</span></div></header>
    <div className="audio-mix-grid">{audioFields(group).map(field => <label key={field}><span className="field-label">{audioFieldLabels[field]}</span>
      <input id={fieldId(field)} aria-label={audioFieldLabels[field]} aria-invalid={editor.conflicts.some(conflict => conflict.field === field)} value={editor.fields[field].raw} inputMode="decimal" placeholder={field.startsWith("target") ? "unset" : undefined} onChange={event => editor.edit(field, event.target.value)}/></label>)}
      <button type="button" className="button" disabled={!editor.canSave} onClick={() => void editor.save()}><Save size={14}/> Save mix intent</button></div>
    <AudioDraftFeedback editor={editor} group={group} fieldId={fieldId}/>
  </section>;
}

type AudioWorkspaceProps = { project: Project; commit: Commit; commitRequired: Commit; commitRecipe: (change: Change) => Promise<Project>; desktopMode: boolean; importVoice: (path: string, label?: string) => Promise<void>; playhead: number; onSeek: (seconds: number) => void };

function AudioRetainedDrafts({ project }: { project: Project }) {
  const [copies, setCopies] = useState<AudioRecoveryCopy[]>([]), [error, setError] = useState<string | null>(null);
  const [limit, setLimit] = useState(12);
  const identity = audioProjectIdentity(project);
  function refresh() {
    const collected = new Map<string, AudioRecoveryCopy>();
    const accepts = (scope: string, group: AudioDraftGroup) => {
      if (scope !== audioDraftScope(project, group)) return false;
      return !audioCanonicalFields(project, group) || (group.kind === "new-transcript" && group.voiceId !== project.audio.active_voice_track_id)
        || (group.kind === "transcript" && group.voiceId !== project.audio.active_voice_track_id);
    };
    try {
      const storage = window.localStorage;
      let inspected = 0;
      for (let index = 0; index < storage.length && inspected < AUDIO_COPY_LIMIT; index++) {
        const key = storage.key(index);
        if (!key?.startsWith(AUDIO_DRAFT_PREFIX)) continue;
        inspected++;
        try {
          const tail = key.slice(AUDIO_DRAFT_PREFIX.length), separator = tail.lastIndexOf(":"), writer = tail.slice(separator + 1);
          const scope = decodeURIComponent(tail.slice(0, separator));
          const tuple = JSON.parse(scope) as unknown[];
          if (!Array.isArray(tuple) || tuple.length !== 4 || JSON.stringify(tuple.slice(0, 3)) !== identity) continue;
          const group = tuple[3] as AudioDraftGroup;
          if (!group || !["mix", "transcript", "cue", "new-transcript", "new-cue"].includes(group.kind) || !accepts(scope, group)) continue;
          const raw = storage.getItem(key); if (raw === null) continue;
          const value = decodeAudioRecovery(raw, scope, group, writer);
          if (audioDraftHasWork(value.draft)) collected.set(key, { key, raw, writer, value });
        } catch { /* Corrupt copies stay untouched and are surfaced by their matching editor. */ }
      }
    } catch (reason) { setError(messageOf(reason)); }
    // Unsaved input survives workspace navigation even when durable storage failed.
    for (const [scope, cached] of liveAudioDrafts) if (accepts(scope, cached.draft.group) && audioDraftHasWork(cached.draft)) {
      const value: AudioRecovery = { schema: "motionwright.audio-recovery/1", scope, writer: cached.writer, updatedAt: Date.now(), draft: cached.draft };
      const key = audioRecoveryKey(scope, cached.writer);
      collected.set(key, { key, raw: JSON.stringify(value), writer: cached.writer, value });
    }
    setCopies([...collected.values()]);
  }
  useEffect(() => {
    refresh();
    const listener = () => refresh();
    window.addEventListener("storage", listener); window.addEventListener("motionwright-audio-draft", listener);
    return () => { window.removeEventListener("storage", listener); window.removeEventListener("motionwright-audio-draft", listener); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project]);
  if (!copies.length && !error) return null;
  return <section className="audio-editor-section" aria-label="Retained audio drafts">
    <header><div><strong>Retained audio drafts</strong><span>Inputs from removed items or another take remain available. They never recreate a removed source automatically.</span></div></header>
    {error && <p className="production-notice" role="alert">Local recovery could not be fully read: {error}</p>}
    {copies.slice(0, limit).map(copy => <details key={copy.key}><summary>{audioGroupName(copy.value!.draft.group)} · retained input</summary>
      <dl>{Object.entries(copy.value!.draft.fields).map(([field, value]) => <div key={field}><dt>{audioFieldLabels[field]}</dt>
        <dd style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", userSelect: "text" }}>{value.raw || "(empty)"}</dd></div>)}</dl>
      <button className="button compact" type="button" onClick={() => {
        const url = URL.createObjectURL(new Blob([copy.raw], { type: "application/json" }));
        const anchor = document.createElement("a"); anchor.href = url; anchor.download = "motionwright-audio-recovery-" + copy.writer + ".json";
        anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 0);
      }}>Download retained audio copy</button>
    </details>)}
    {copies.length > limit && <button className="button compact" type="button" onClick={() => setLimit(value => value + 12)}>Show more retained audio drafts</button>}
  </section>;
}

export default function AudioWorkspace(props: AudioWorkspaceProps) {
  return <AudioWorkspaceEditor key={audioProjectIdentity(props.project)} {...props}/>;
}

function AudioWorkspaceEditor({
  project,
  commit,
  commitRequired,
  commitRecipe,
  desktopMode,
  importVoice,
  playhead,
  onSeek,
}: {
  project: Project;
  commit: Commit;
  commitRequired: Commit;
  commitRecipe: (change: Change) => Promise<Project>;
  desktopMode: boolean;
  importVoice: (path: string, label?: string) => Promise<void>;
  playhead: number;
  onSeek: (absoluteSeconds: number) => void;
}) {
  // A recorded original voice protects the media, transcript and timed cues.
  // The domain/SQLite CAS enforce authority; disabled controls explain it.
  const sourceLocked = !!project.production_design?.narration_take_lock;
  const activeId = project.audio.active_voice_track_id;
  const active = project.audio.voice_tracks.find((track) => track.id === activeId) ?? null;
  const activeSegments = useMemo(
    () => project.audio.transcript
      .filter((segment) => segment.voice_track_id === activeId)
      .sort((left, right) => valueOf(left.start) - valueOf(right.start)),
    [project.audio.transcript, activeId],
  );

  const [path, setPath] = useState("");
  const [takeLabel, setTakeLabel] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [waveform, setWaveform] = useState<WaveformPage | null>(null);
  const [waveformPageIndex, setWaveformPageIndex] = useState(0);
  const [waveformBusy, setWaveformBusy] = useState(false);
  const [waveformError, setWaveformError] = useState<string | null>(null);

  useEffect(() => {
    setWaveformPageIndex(0);
    setWaveform(null);
    setWaveformError(null);
  }, [active?.id]);

  useEffect(() => {
    if (!desktopMode || !active) {
      setWaveform(null);
      setWaveformBusy(false);
      return;
    }
    let cancelled = false;
    setWaveformBusy(true);
    setWaveformError(null);
    void loadWaveformPage(project, active.id, waveformPageIndex, WAVEFORM_PAGE_SIZE)
      .then((page) => {
        if (cancelled) return;
        setWaveform(page);
      })
      .catch((reason) => {
        if (cancelled) return;
        setWaveform(null);
        setWaveformError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!cancelled) setWaveformBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [desktopMode, active?.id, waveformPageIndex, project.id]);

  const doImport = async () => {
    if (!path.trim() || !desktopMode || sourceLocked) return;
    setImportBusy(true);
    setImportError(null);
    try {
      await importVoice(path.trim(), takeLabel.trim() || undefined);
      setPath("");
      setTakeLabel("");
    } catch (reason) {
      setImportError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setImportBusy(false);
    }
  };

  const openNarrationCorrection = (target: NarrationCorrectionTarget) => {
    const destination = narrationCorrectionFocus(project, target);
    const field = document.getElementById(destination.fieldId);
    if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement))
      throw new Error("The existing source editor is unavailable. No take or saved value was changed.");
    if (field.disabled) throw new Error("The current source is protected. Review its explicit release before editing.");
    field.focus({ preventScroll: true });
    field.scrollIntoView({ block: "center", behavior: "auto" });
    onSeek(destination.seconds);
  };

  const waveformPageCount = waveform
    ? Math.max(1, Math.ceil(waveform.peak_count / waveform.page_size))
    : 0;
  const waveformStartSeconds = waveform
    ? (waveform.start_peak * waveform.frames_per_peak) / waveform.sample_rate_hz
    : 0;
  const waveformEndSeconds = waveform
    ? Math.min(
        waveform.total_frames / waveform.sample_rate_hz,
        ((waveform.start_peak + waveform.peaks.length) * waveform.frames_per_peak)
          / waveform.sample_rate_hz,
      )
    : 0;

  return (
    <section className="audio-workspace" aria-label="Audio workspace" onKeyDown={event => {
      if (event.code === "Space" && !event.ctrlKey && !event.metaKey && !event.altKey
        && event.target instanceof HTMLElement && event.target.closest("button, summary")) event.stopPropagation();
    }}>
      <header className="audio-workspace-head">
        <div>
          <span className="section-kicker">SOURCE AUDIO / TAKE LEDGER</span>
          <h2>Voice, transcript and cue evidence</h2>
          <p>Real files are measured before admission. Transcript timing is never inferred from speaking speed.</p>
        </div>
        <div className="audio-playhead-chip">
          <span className="section-kicker">SHARED EDITORIAL TIME</span>
          <strong className="mono">{playhead.toFixed(2)} s</strong>
          <small>{project.audio.voice_tracks.length} measured take{project.audio.voice_tracks.length === 1 ? "" : "s"} · not source playback</small>
        </div>
      </header>

      {sourceLocked && <div className="audio-truth-line" role="status">
        <strong>Original narration is protected.</strong>
        Use the explicit recorded-source release before importing another voice or editing words and timed cues.
        Timeline seeking and existing mix intent remain available.
      </div>}

      <div className="audio-import-strip">
        <FileAudio size={17} />
        <label className="audio-path-field">
          <span className="field-label">Voice file · absolute path</span>
          <input
            aria-label="Voice file absolute path"
            value={path}
            disabled={!desktopMode || importBusy || sourceLocked}
            placeholder="/absolute/path/voice.wav"
            onChange={(event) => setPath(event.target.value)}
          />
        </label>
        <label>
          <span className="field-label">Take label</span>
          <input
            aria-label="Voice take label"
            value={takeLabel}
            disabled={!desktopMode || importBusy || sourceLocked}
            placeholder="Narrator take 03"
            maxLength={256}
            onChange={(event) => setTakeLabel(event.target.value)}
          />
        </label>
        <button type="button" className="button button-primary" disabled={!desktopMode || !path.trim() || importBusy || sourceLocked} onClick={() => void doImport()}>
          <Plus size={14} /> {importBusy ? "Measuring…" : "Import + measure"}
        </button>
      </div>
      {!desktopMode && <div className="audio-truth-line"><CircleDashed size={14} /> Desktop runtime required for measured file import.</div>}
      {importError && <div className="portable-message error" role="alert"><strong>Voice import blocked.</strong><span>{importError}</span></div>}

      <div className="audio-main-grid">
        <ActiveVoiceSelection project={project} commit={commitRequired} />

        <div className="audio-evidence-editor">
          <section className="audio-meter-ledger" aria-label="Audio measurements">
            <div><span>Active take</span><strong>{active?.label ?? "NONE"}</strong></div>
            <div><span>Loudness</span><strong>{active?.loudness_lufs == null ? "UNKNOWN" : active.loudness_lufs.toFixed(1) + " LUFS"}</strong></div>
            <div><span>True peak</span><strong>{active?.true_peak_dbfs == null ? "UNKNOWN" : active.true_peak_dbfs.toFixed(2) + " dBFS"}</strong></div>
            <div><span>Alignment</span><strong>{activeSegments.length ? activeSegments.length + " SEGMENTS" : "NONE"}</strong></div>
          </section>

          <section className="waveform-proxy-section" aria-label="Measured sample peak waveform">
            <header>
              <div>
                <strong>Measured waveform proxy</strong>
                <span>Sample-peak max-abs from immutable source bytes · not LUFS or true-peak evidence.</span>
              </div>
              {waveform && (
                <span className="mono">
                  {waveformStartSeconds.toFixed(2)}–{waveformEndSeconds.toFixed(2)} s
                </span>
              )}
            </header>
            {!active && (
              <div className="waveform-proxy-state">
                <AudioLines size={18} aria-hidden="true" />
                <span>No active measured take. No waveform is synthesized.</span>
              </div>
            )}
            {active && !desktopMode && (
              <div className="waveform-proxy-state">
                <CircleDashed size={18} aria-hidden="true" />
                <span>Desktop runtime required for measured waveform proxy.</span>
              </div>
            )}
            {active && desktopMode && waveformBusy && !waveform && (
              <div className="waveform-proxy-state" role="status" aria-live="polite">
                <CircleDashed size={18} aria-hidden="true" />
                <span>Decoding immutable source into a paged proxy…</span>
              </div>
            )}
            {active && desktopMode && waveformError && (
              <div className="waveform-proxy-state error" role="alert">
                <CircleDashed size={18} />
                <span>Waveform evidence unavailable: {waveformError}</span>
              </div>
            )}
            {active && desktopMode && waveform && (
              <>
                <div
                  className="waveform-proxy-bars"
                  role="img"
                  aria-label={"Measured sample-peak waveform page " + (waveform.page_index + 1) + " of " + waveformPageCount}
                >
                  {waveform.peaks.map((peak, index) => (
                    <span
                      key={waveform.start_peak + index}
                      style={{ height: Math.max(2, Math.min(100, peak * 100)) + "%" }}
                      title={(((waveform.start_peak + index) * waveform.frames_per_peak / waveform.sample_rate_hz).toFixed(3)) + " s · " + (peak * 100).toFixed(1) + "% sample peak"}
                    />
                  ))}
                </div>
                <footer className="waveform-proxy-footer">
                  <span>
                    {waveform.peaks.length.toLocaleString()} peaks loaded · {waveform.frames_per_peak.toLocaleString()} frames/peak
                  </span>
                  <div className="waveform-page-controls" aria-label="Waveform page controls">
                    <button
                      type="button"
                      className="icon-action"
                      aria-label="Previous waveform page"
                      disabled={waveformBusy || !waveform.has_previous}
                      onClick={() => setWaveformPageIndex((value) => Math.max(0, value - 1))}
                    >
                      <ChevronLeft size={14} />
                    </button>
                    <span className="mono">Page {waveform.page_index + 1} / {waveformPageCount}</span>
                    <button
                      type="button"
                      className="icon-action"
                      aria-label="Next waveform page"
                      disabled={waveformBusy || !waveform.has_next}
                      onClick={() => setWaveformPageIndex((value) => value + 1)}
                    >
                      <ChevronRight size={14} />
                    </button>
                  </div>
                </footer>
              </>
            )}
          </section>

          <section className="audio-editor-section">
            <header>
              <div><strong>Transcript alignment</strong><span>Saving edited timing records manual evidence.</span></div>
              <span className="mono">{active ? "take:" + active.id.slice(0, 8) : "no-active-take"}</span>
            </header>
            {activeSegments.map(segment => <TranscriptRow key={audioDraftScope(project, { kind: "transcript", id: segment.id, voiceId: segment.voice_track_id })} segment={segment} project={project} commit={commit} playhead={playhead} onSeek={onSeek}/>)}
            <NewTranscript key={activeId ?? "no-voice"} project={project} commit={commit} voiceId={activeId}/>
          </section>

          <section className="audio-editor-section">
            <header><div><strong>Stable cues</strong><span>Cue IDs survive text and label edits.</span></div><span>{project.audio.cues.length}</span></header>
            {project.audio.cues.map(cue => <CueRow key={audioDraftScope(project, { kind: "cue", id: cue.id })} cue={cue} project={project} commit={commit} playhead={playhead} onSeek={onSeek}/>)}
            <NewCue project={project} commit={commit}/>
          </section>

          <NarrationTakeReview project={project} desktopMode={desktopMode} commit={commit} onCorrect={openNarrationCorrection}/>
          <NativeOriginalMixReview project={project} desktopMode={desktopMode} commit={commitRecipe}/>
          <SemanticSfxWorkbench project={project} commit={commit} onSeek={onSeek}/>
          <MixEditor project={project} commit={commit}/>
          <AudioRetainedDrafts project={project}/>
        </div>
      </div>
    </section>
  );
}

function ActiveVoiceSelection({ project, commit }: { project: Project; commit: Commit }) {
  const scope = audioDraftScope(project, activeVoiceGroup);
  const projectRef = useRef(project); projectRef.current = project;
  const [entry] = useState(() => {
    const cached = liveAudioDrafts.get(scope);
    if (cached) return cached;
    let draft = createAudioDraft(project, activeVoiceGroup);
    let writer: string = crypto.randomUUID();
    let warning: string | null = null, blocked = false, blockedKey: string | null = null;
    try {
      const { copies, limited } = readAudioRecoveries(window.localStorage, scope, activeVoiceGroup);
      let previous: string | null = null;
      if (!window.opener && performance.getEntriesByType("navigation").some(item => (item as PerformanceNavigationTiming).type === "reload")) {
        previous = window.sessionStorage.getItem(audioSessionPointer + scope);
      }
      const own = copies.find(copy => copy.writer === previous);
      if (own?.value) { draft = own.value.draft; writer = own.writer; }
      else if (own) { blocked = true; blockedKey = own.key; warning = "This tab's selection record is unreadable and has been kept."; }
      if (limited || copies.some(copy => copy.writer !== writer
        && (!copy.value || activeVoiceIsUnconfirmed(copy.value.draft)))) {
        blocked = true; warning ??= "Inspect a retained selection record before starting another selection.";
      }
    } catch (reason) { blocked = true; warning = messageOf(reason); }
    const created: AudioLiveDraft = { draft, writer, warning, blocked, blockedKey };
    liveAudioDrafts.set(scope, created); return created;
  });
  const [, render] = useState(0);
  const [copies, setCopies] = useState<AudioRecoveryCopy[]>([]);
  const [review, setReview] = useState<ActiveVoiceOutcomeReview | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const mounted = useRef(true);
  function notify() { liveAudioDrafts.set(scope, entry); window.dispatchEvent(new Event("motionwright-audio-draft")); }
  function refresh() {
    try {
      const result = readAudioRecoveries(window.localStorage, scope, activeVoiceGroup); setCopies(result.copies);
      if (result.limited) entry.warning = "The local selection record list exceeds its bound. No copies were removed.";
    } catch (reason) { entry.warning = messageOf(reason); }
    render(value => value + 1);
  }
  useEffect(() => {
    mounted.current = true; refresh();
    const receive = () => refresh();
    window.addEventListener("motionwright-audio-draft", receive);
    const storage = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(AUDIO_DRAFT_PREFIX)) refresh(); };
    window.addEventListener("storage", storage);
    return () => { mounted.current = false; window.removeEventListener("motionwright-audio-draft", receive); window.removeEventListener("storage", storage); };
    // The keyed Audio workspace owns one project/generation/branch scope.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);
  const locked = !!project.production_design?.narration_take_lock
    || project.locks.some(lock => lock.resource === "project:" + project.id && lock.kind === "content");
  const attempt = entry.draft.awaiting, selection = attempt?.selection;
  const unknown = activeVoiceIsUnconfirmed(entry.draft);
  const pending = entry.attemptInFlight === true;
  function record(): AudioRecovery {
    return { schema: "motionwright.audio-recovery/1", scope, writer: entry.writer, updatedAt: Date.now(), draft: entry.draft };
  }
  function retainLocal() {
    try {
      retainActiveVoiceRecord(window.localStorage, window.sessionStorage, audioSessionPointer + scope, record());
      entry.warning = null; entry.custodyPending = false; notify();
    } catch (reason) { entry.warning = messageOf(reason); notify(); }
  }
  async function select(trackId: string) {
    if (entry.attemptInFlight || entry.custodyPending || locked || entry.blocked || activeVoiceIsUnconfirmed(entry.draft)) return;
    let next: AudioDraft;
    try { next = prepareActiveVoiceSelection(projectRef.current, trackId); }
    catch (reason) { entry.warning = messageOf(reason); notify(); return; }
    const original: AudioRecovery = { schema: "motionwright.audio-recovery/1", scope, writer: crypto.randomUUID(), updatedAt: Date.now(), draft: next };
    entry.writer = original.writer; entry.draft = next; entry.attemptInFlight = true; entry.warning = null; entry.notice = null;
    setReview(null); notify();
    const belongs = () => entry.writer === original.writer
      && entry.draft.awaiting?.selection?.intentId === original.draft.awaiting?.selection?.intentId;
    let local: Storage, session: Storage;
    try { local = window.localStorage; session = window.sessionStorage; }
    catch (reason) {
      entry.attemptInFlight = false; entry.notice = "NOT_SENT: " + messageOf(reason) + " No Project change was invoked."; notify(); return;
    }
    const result = await sendRetainedActiveVoiceSelection(local, session, audioSessionPointer + scope,
      original, commit, retained => { if (belongs()) { entry.draft = retained.draft; notify(); } });
    if (belongs()) {
      entry.draft = result.record.draft; entry.attemptInFlight = false;
      entry.warning = result.custodyError; entry.custodyPending = result.custodyError !== null;
      entry.notice = result.outcome === "NOT_SENT"
        ? "NOT_SENT: " + result.diagnostic + " The local intent is retained; no Project change was invoked."
        : result.outcome === "ACKNOWLEDGED"
          ? "The original selection response was acknowledged. The active marker below follows the current saved Project."
          : "The selection outcome remains UNKNOWN. No retry was sent.";
      notify();
    }
  }
  async function inspect() {
    if (entry.attemptInFlight || reviewing || !activeVoiceIsUnconfirmed(entry.draft)) return;
    setReviewing(true); setReview(null);
    try {
      const observed = await inspectActiveVoiceOutcome(entry.draft, projectRef.current, {
        readProject: async id => {
          const saved = await bootstrap(id);
          if (!saved.project || saved.project_selection.status !== "selected") throw new Error("The selected saved Project is unavailable.");
          return saved.project;
        }, readHistory: projectHistory,
      });
      if (!mounted.current) return;
      if (!activeVoiceReviewMatches(entry.draft, projectRef.current, observed)) throw new Error("The selection or Project changed during inspection.");
      setReview(observed); entry.warning = null; notify();
    } catch (reason) { if (mounted.current) { entry.warning = messageOf(reason); notify(); } }
    finally { if (mounted.current) setReviewing(false); }
  }
  function continueSelection() {
    if (entry.attemptInFlight || !review) return;
    try {
      const next = prepareSeparateActiveVoiceIntent(window.localStorage, record(), projectRef.current, review);
      entry.draft = next; entry.writer = crypto.randomUUID(); entry.blocked = false; entry.blockedKey = null;
      entry.warning = null; entry.notice = "The earlier UNKNOWN record is kept. Choose a take explicitly to create another intent; nothing was retried.";
      setReview(null); notify();
    } catch (reason) { entry.warning = messageOf(reason); notify(); }
  }
  function inspectCopy(copy: AudioRecoveryCopy) {
    if (entry.attemptInFlight || !copy.value || activeVoiceIsUnconfirmed(entry.draft)) return;
    try {
      if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("This retained record changed. Read its current version first.");
      if (entry.draft.awaiting) retainActiveVoiceRecord(window.localStorage, window.sessionStorage, audioSessionPointer + scope, record());
      const restored: AudioRecovery = { ...copy.value, writer: crypto.randomUUID(), updatedAt: Date.now() };
      retainActiveVoiceRecord(window.localStorage, window.sessionStorage, audioSessionPointer + scope, restored);
      entry.writer = restored.writer; entry.draft = restored.draft;
      entry.blocked = entry.blocked && !activeVoiceIsUnconfirmed(restored.draft);
      if (!entry.blocked) entry.blockedKey = null;
      entry.warning = null; entry.notice = "Retained selection opened for inspection. No change was replayed."; setReview(null); notify();
    } catch (reason) { entry.warning = messageOf(reason); notify(); }
  }
  function discardCopy(copy: AudioRecoveryCopy) {
    if (entry.attemptInFlight || (copy.value && copy.value.draft.awaiting?.selection?.outcome !== "ACKNOWLEDGED")) return;
    try {
      if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("This retained record changed before discard.");
      window.localStorage.removeItem(copy.key);
      if (window.localStorage.getItem(copy.key) !== null) throw new Error("The selected local record was not removed.");
      if (copy.writer === entry.writer || entry.blockedKey === copy.key) {
        entry.draft = createAudioDraft(projectRef.current, activeVoiceGroup); entry.writer = crypto.randomUUID(); entry.blockedKey = null;
      }
      const remaining = readAudioRecoveries(window.localStorage, scope, activeVoiceGroup);
      entry.blocked = remaining.limited || remaining.copies.some(item => !item.value || activeVoiceIsUnconfirmed(item.value.draft));
      entry.warning = null; entry.notice = "Only the selected local record was discarded. The saved Project is unchanged."; notify();
    } catch (reason) { entry.warning = messageOf(reason); notify(); }
  }
  return <aside className="voice-take-ledger" aria-label="Voice takes" aria-busy={pending}>
    <header><strong>Voice takes</strong><span>Byte-bound measurements</span></header>
    {project.audio.voice_tracks.length === 0 && <div className="audio-empty"><AudioLines size={20} /><strong>No measured take</strong><span>No waveform or timing evidence is synthesized.</span></div>}
    {project.audio.voice_tracks.map(track => {
      const selected = track.id === project.audio.active_voice_track_id;
      return <button type="button" key={track.id} aria-label={"Activate voice " + track.label} aria-pressed={selected}
        disabled={pending || reviewing || unknown || entry.blocked || entry.custodyPending || (locked && !selected)}
        className={selected ? "voice-take-row selected" : "voice-take-row"}
        onClick={() => { if (!selected) void select(track.id); }}>
        <span className="take-state">{selected ? <Check size={14} /> : <span />}</span>
        <span className="voice-take-copy"><strong>{track.label}</strong>
          <span>{track.sample_rate_hz.toLocaleString()} Hz · {track.channels} ch · {secondsLabel(track.measured_duration)}</span>
          <span className="mono">sha256:{track.source_sha256.slice(0, 12)}</span></span>
      </button>;
    })}
    {locked && <p role="status">Voice selection is protected by the recorded-source or project content lock.</p>}
    {pending && <p role="status">Waiting for the original selection response. Another selection is disabled.</p>}
    {entry.notice && <p role="status">{entry.notice}</p>}
    {entry.warning && <p role="alert">{entry.warning}</p>}
    {selection && <section aria-label="Retained voice selection" className="audio-recovery">
      <strong>{selection.outcome === "UNKNOWN" ? "Unconfirmed voice selection" : "Acknowledged selection response"}</strong>
      <p>Intent {selection.intentId} · from r{attempt!.revision} · requested {attempt!.change.type === "set_active_voice_track" ? attempt!.change.track_id : "UNKNOWN"}</p>
      {selection.diagnostic && <p role="alert">{selection.diagnostic}</p>}
      <button type="button" className="button compact" disabled={pending} onClick={retainLocal}>Retain local selection record</button>
      {unknown && <button type="button" className="button compact" disabled={pending || reviewing} onClick={() => void inspect()}>
        {reviewing ? "Reading saved selection…" : "Read saved selection and history"}
      </button>}
      {activeVoiceReviewMatches(entry.draft, project, review) && <section aria-label="Saved voice selection review">
        <p>Read saved revision r{review!.project.revision}. This observation does not identify or acknowledge the earlier request.</p>
        <p>{review!.matchingValueRevisions.length ? "History contains matching values at " + review!.matchingValueRevisions.map(value => "r" + value).join(", ") : "No matching change values in the observed history."}</p>
        <button type="button" className="button compact" disabled={pending || reviewing} onClick={continueSelection}>Keep earlier record and choose another selection</button>
      </section>}
    </section>}
    {copies.length > 0 && <details className="audio-recovery"><summary>Retained selection records ({copies.length})</summary>
      {copies.map(copy => <div key={copy.key}>
        <span>{copy.value ? copy.value.draft.awaiting?.selection?.outcome + " · r" + copy.value.draft.awaiting?.revision : "Unreadable local record"}</span>
        {copy.value && <button type="button" className="button compact" disabled={pending || reviewing || unknown} onClick={() => inspectCopy(copy)}>Inspect retained selection {copy.writer.slice(0, 8)}</button>}
        {(!copy.value || copy.value.draft.awaiting?.selection?.outcome === "ACKNOWLEDGED") && <button type="button" className="button compact" disabled={pending || reviewing} onClick={() => discardCopy(copy)}>
          {copy.value ? "Discard acknowledged local record " : "Discard unreadable local record "}{copy.writer.slice(0, 8)}
        </button>}
      </div>)}
    </details>}
  </aside>;
}
