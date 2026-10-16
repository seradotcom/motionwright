import { useEffect, useState } from "react";
import { AudioLines, Plus, Trash2 } from "lucide-react";
import type { Change, Project } from "./types";
import {
  SFX_CUE_SCHEMA, previewSemanticSfxEdit, sourceChangeRightsDeclaration,
  validateSemanticSfx,
} from "./semanticSfx";
import type { SemanticSfxCue, SfxEvent } from "./semanticSfx";
import { openOriginalSfxReviewHandoffs } from "./sfxReviewHandoff";

type Props = {
  project: Project;
  commit: (change: Change) => Promise<void>;
  onSeek: (seconds: number) => void;
};
const eventNames:SfxEvent[] = [
  "scene_reveal", "beat_emphasis", "ui_focus", "confirmation", "impact", "transition",
];
function describe(e:unknown){return e instanceof Error ? e.message : String(e);}
export default function SemanticSfxWorkbench({project,commit,onSeek}:Props) {
  const assets=project.assets.filter(a=>
    ["audio/wav","audio/x-wav","audio/wave"].includes(a.media_type) &&
    /^[0-9a-f]{64}$/.test(a.content_sha256??""));
  const cues=project.audio.sfx_cues??[];
  const openListeningIssues=openOriginalSfxReviewHandoffs(project);
  const [sceneId,setSceneId]=useState(project.scenes[0]?.id??"");
  const [event,setEvent]=useState<SfxEvent>("scene_reveal");
  const [beatId,setBeatId]=useState("");
  const [sourceId,setSourceId]=useState("");
  const [offset,setOffset]=useState(0);
  const [sourceFrames,setSourceFrames]=useState(30);
  const [gain,setGain]=useState(-12000);
  const [attack,setAttack]=useState(3);
  const [release,setRelease]=useState(12);
  const [attestation,setAttestation]=useState("");
  const [editingId,setEditingId]=useState<string|null>(null);
  const [base,setBase]=useState(project.revision);
  const [generation,setGeneration]=useState(project.generation);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const stale=base!==project.revision || generation!==project.generation;
  useEffect(()=>{
    setBase(project.revision);setGeneration(project.generation);setError(null);
    // Preserve unsaved draft on same-generation remote project revision.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[project.generation]);
  const scene=project.scenes.find(s=>s.id===sceneId);
  const source=assets.find(a=>a.id===sourceId);
  const editingCue=editingId===null?null:cues.find(c=>c.id===editingId)??null;
  const sourceChangedDuringEdit=editingCue!==null
    && editingCue.source_asset_id!==sourceId;
  const cue:SemanticSfxCue={
    schema:SFX_CUE_SCHEMA,id:editingId??"00000000-0000-4000-8000-000000000005",
    scene_id:sceneId,event,
    anchor:beatId?{kind:"beat_start",beat_id:beatId}:{kind:"scene_start"},
    offset_frames:offset,source_asset_id:sourceId,source_sha256:source?.content_sha256??"",
    source_frames:sourceFrames,gain_milli_db:gain,attack_frames:attack,
    release_frames:release,original_source_attestation:attestation,
  };
  const preview=(()=>{
    try {
      const scheduled=previewSemanticSfxEdit(project,cue,editingId);
      return {scheduled,error:null as string|null};
    } catch(e){return {scheduled:null,error:describe(e)};}
  })();
  const lockedFor=(id:string)=>project.locks.some(lock=>
    ["content","timing"].includes(lock.kind) &&
    ["project:"+project.id,"scene:"+id].includes(lock.resource));
  const locked=lockedFor(sceneId);
  function clearEdit() {
    setEditingId(null);
    setBeatId("");setOffset(0);setSourceFrames(30);
    setGain(-12000);setAttack(3);setRelease(12);
    setEvent("scene_reveal");setAttestation("");
    setSourceId("");
  }
  function edit(existing:SemanticSfxCue) {
    if(stale||busy||lockedFor(existing.scene_id))return;
    setEditingId(existing.id);
    setSceneId(existing.scene_id);
    setEvent(existing.event);
    setBeatId(existing.anchor.kind==="beat_start"?existing.anchor.beat_id:"");
    setSourceId(existing.source_asset_id);
    setOffset(existing.offset_frames);
    setSourceFrames(existing.source_frames);
    setGain(existing.gain_milli_db);
    setAttack(existing.attack_frames);
    setRelease(existing.release_frames);
    setAttestation(existing.original_source_attestation);
    setError(null);
  }
  async function add(){
    if(stale||busy||locked||!preview.scheduled)return;
    setBusy(true);setError(null);
    try{
      await commit({type:"upsert_semantic_sfx",cue:{...cue,
        id:editingId??crypto.randomUUID()}});
      setBase(project.revision+1);
      if(editingId!==null)clearEdit();
    }catch(e){setError(describe(e));}finally{setBusy(false);}
  }
  async function remove(id:string){
    const target=cues.find(c=>c.id===id);
    if(stale||busy||!target||lockedFor(target.scene_id))return;
    setBusy(true);setError(null);
    try{
      await commit({type:"remove_semantic_sfx",cue_id:id});
      setBase(project.revision+1);
      if(editingId===id)clearEdit();
    }catch(e){setError(describe(e));}finally{setBusy(false);}
  }
  return <section className="audio-editor-section" aria-label="Semantic sound choreography">
    <header><div><strong>Semantic SFX choreography</strong><span>
      Original WAV / exact 30 fps / stereo 48 kHz / native evidence pending</span></div>
      <span>{cues.length}/64</span>
    </header>
    <p className="audio-truth-line"><AudioLines size={15}/>
      This editor authors only source-bound placements. It does not fabricate sounds,
      rights clearance, measured loudness or an already-rendered mix.
    </p>
    {stale && <div className="portable-message" role="status">
      SFX draft from revision {base}; project revision is {project.revision}.
      Unsaved input remains in the editor.
      <button className="button" onClick={()=>{
        setBase(project.revision);setGeneration(project.generation);setError(null);
      }}>Reload revision</button>
    </div>}
    {openListeningIssues.length>0 && (
      <section className="sfx-open-listening-issues" role="region"
        aria-label="Open original SFX listening issues">
        <header>
          <strong>{openListeningIssues.length} open listening issue
            {openListeningIssues.length===1?"":"s"}</strong>
          <span>Real creative review threads from an earlier verified MP4.
            Corrections never resolve these findings automatically.</span>
        </header>
        {openListeningIssues.map(issue=>{
          const target=cues.find(item=>item.id===issue.cueId);
          const editable=Boolean(target&&issue.sameOriginalWav
            && !lockedFor(issue.sceneId)&&!stale&&!busy);
          return (
            <div className="sfx-open-issue" key={issue.reviewId}>
              <div>
                <strong>{issue.sceneName} · source r{issue.reviewRevision}</strong>
                <span>Frame {issue.fromFrame}–{issue.toFrame}
                  {" · "}cue {issue.cueId.slice(0,8)}
                  {" · "}{issue.status==="open"?"OPEN":"NEEDS RECHECK"}
                </span>
              </div>
              <p>{issue.observation}</p>
              {!issue.sameOriginalWav && (
                <p className="delivery-truth-note warning">
                  The imported WAV changed since this listening review.
                  Inspect historical evidence in History before editing.
                </p>
              )}
              <button type="button" className="button compact"
                aria-label={"Edit SFX cue from listening review "+issue.reviewId}
                disabled={!editable}
                onClick={()=>{if(target)edit(target);}}>
                Edit original cue
              </button>
            </div>
          );
        })}
        <p className="audio-truth-line">
          Saving an original cue edits the project and invalidates previous
          native results. Resolve each open review manually in History
          after a separately verified new MP4 and human listening.
        </p>
      </section>
    )}
    {!assets.length && <p role="status">No content-hashed original WAV Asset is imported yet.</p>}
    {editingId!==null && (
      <div className="portable-message" role="status" aria-label="Editing original semantic SFX cue">
        Editing original cue <span className="mono">{editingId}</span>.
        Its identity and source scene remain fixed; gain, sound, envelope and
        anchor edits will invalidate the previous native render.
        <button type="button" className="button compact" disabled={busy}
          onClick={()=>{clearEdit();setError(null);}}>Cancel edit</button>
      </div>
    )}
    <div className="audio-time-pair">
      <label>Scene
        <select aria-label="SFX source scene" value={sceneId}
          disabled={editingId!==null} onChange={e=>{
          setSceneId(e.target.value);setBeatId("");
        }}>
          <option value="">Select authored scene</option>
          {project.scenes.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </label>
      <label>Event
        <select aria-label="SFX semantic event" value={event}
          onChange={e=>setEvent(e.target.value as SfxEvent)}>
          {eventNames.map(kind=><option key={kind} value={kind}>{kind.replaceAll("_"," ")}</option>)}
        </select>
      </label>
      <label>Anchor
        <select aria-label="SFX event anchor" value={beatId}
          onChange={e=>setBeatId(e.target.value)}>
          <option value="">Scene start</option>
          {scene?.beats.map(b=><option key={b.id} value={b.id}>{b.label} / beat start</option>)}
        </select>
      </label>
      <label>Imported WAV
        <select aria-label="SFX imported WAV" value={sourceId}
          onChange={e=>{
            const nextId=e.target.value;
            setAttestation(previous=>sourceChangeRightsDeclaration(
              sourceId,nextId,previous,
            ));
            setSourceId(nextId);
          }}>
          <option value="">Select immutable original WAV</option>
          {assets.map(a=><option key={a.id} value={a.id}>
            {a.name} / {a.content_sha256?.slice(0,12)}</option>)}
        </select>
      </label>
    </div>
    <div className="audio-time-pair">
      {([
        ["Offset frames",offset,setOffset,-15,30],
        ["Source frames",sourceFrames,setSourceFrames,1,180],
        ["Gain milli-dB",gain,setGain,-36000,0],
        ["Attack frames",attack,setAttack,0,30],
        ["Release frames",release,setRelease,0,90],
      ] as const).map(([label,value,update,min,max])=><label key={label}>{label}
        <input type="number" aria-label={"SFX "+label} min={min} max={max} step={1}
          value={value} onChange={e=>update(Number(e.target.value))}/>
      </label>)}
    </div>
    {sourceChangedDuringEdit && (
      <p className="audio-truth-line" role="status">
        Original WAV source changed. The previous declaration does not
        transfer to a different asset. Enter a new source-specific authorship
        statement before saving. This remains an owner declaration, not
        independent copyright or distribution rights verification.
      </p>
    )}
    <label>Original source authorship declaration
      <textarea aria-label="SFX source rights attestation" maxLength={160} rows={2}
        value={attestation} onChange={e=>setAttestation(e.target.value)}
        placeholder="State why you are authorized to publish this original WAV"/>
    </label>
    <p className="audio-truth-line" role="status">
      {preview.scheduled
        ? "Starts at frame "+preview.scheduled.absolute_start_frame+
          "; PCM sample "+preview.scheduled.first_sample+
          "; source samples "+preview.scheduled.sample_count
        : "Admission blocked: "+preview.error}
    </p>
    {locked && <p role="status">Project or scene timing/content lock prevents sound edits.</p>}
    {error && <p role="alert">{error}</p>}
    <button className="button button-primary" type="button"
      disabled={busy||stale||locked||!preview.scheduled||
        (editingId===null&&cues.length>=64)}
      onClick={()=>void add()}><Plus size={14}/>
      {editingId===null?"Add original SFX event":"Save original SFX changes"}</button>
    <div className="audio-cue-list">
      {cues.map(c=><article className="audio-cue-row" key={c.id}>
        <button className="button" onClick={()=>{
          try {onSeek(validateSemanticSfx(project,c).absolute_start_frame/30);}
          catch(e){setError(describe(e));}
        }}>{c.event.replaceAll("_"," ")} / {c.source_frames} frames</button>
        <span className="mono">{c.source_sha256.slice(0,12)} / {c.gain_milli_db/1000} dB</span>
        <button className="button compact" type="button"
          aria-label={"Edit original SFX "+c.id}
          disabled={stale||busy||lockedFor(c.scene_id)}
          onClick={()=>edit(c)}>Edit</button>
        <button className="icon-action" aria-label={"Remove SFX "+c.id}
          disabled={stale||busy||lockedFor(c.scene_id)}
          onClick={()=>void remove(c.id)}><Trash2 size={14}/></button>
      </article>)}
      {!cues.length && <p className="audio-truth-line">No original semantic SFX placements recorded.</p>}
    </div>
  </section>;
}
