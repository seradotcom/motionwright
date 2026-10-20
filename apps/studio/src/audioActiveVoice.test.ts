import { describe, expect, it } from "vitest";
import { fixtureBootstrap } from "./fixture";
import type { Project, ProjectEvent } from "./types";
import {
  activeVoiceGroup, activeVoiceIsUnconfirmed, activeVoiceReviewMatches, audioDraftScope, audioRecoveryKey,
  decodeAudioRecovery, inspectActiveVoiceOutcome, observeAudioDraft, prepareActiveVoiceSelection,
  prepareSeparateActiveVoiceIntent, sendRetainedActiveVoiceSelection, settleActiveVoiceSelection, writeAudioRecovery,
} from "./audioDraft";
import type { AudioRecovery, ActiveVoiceOutcomeTransport } from "./audioDraft";

const first = "11111111-1111-4111-8111-111111111111", second = "22222222-2222-4222-8222-222222222222";
const writer = "33333333-3333-4333-8333-333333333333", intent = "44444444-4444-4444-8444-444444444444";
class MemoryStorage implements Storage {
  data = new Map<string,string>();
  get length() { return this.data.size; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key,value); }
  removeItem(key: string) { this.data.delete(key); }
  clear() { this.data.clear(); }
}
function project() {
  const p = structuredClone(fixtureBootstrap.project); p.locks = []; p.production_design = undefined; p.revision = 12;
  p.audio.voice_tracks = [first,second].map((id,index)=>({id,asset_id:id,label:"Authored take "+index,
    sample_rate_hz:48000,channels:1,measured_duration:{num:"2",den:"1"},source_sha256:String(index).repeat(64),
    loudness_lufs:null,true_peak_dbfs:null}));
  p.audio.active_voice_track_id=first; return p;
}
function record(p=project()): AudioRecovery {
  const draft=prepareActiveVoiceSelection(p,second,intent);
  return {schema:"motionwright.audio-recovery/1",scope:draft.scope,writer,updatedAt:1000,draft};
}
function event(revision:number,change:Record<string,unknown>): ProjectEvent {
  return {revision,created_at:"2026-10-20T00:00:00Z",change} as ProjectEvent;
}
function transport(p:Project,events:ProjectEvent[]):ActiveVoiceOutcomeTransport {
  return {readProject:async()=>structuredClone(p),readHistory:async(_p,cursor,limit)=>structuredClone(events.filter(e=>e.revision>cursor).slice(0,limit))};
}
const pointer="selection-pointer";

describe("active voice continuity in the existing audio recovery model (unit scope)",()=>{
  it("selects only an existing measured take and keeps every source object unchanged",()=>{
    const p=project(),before=structuredClone(p),copy=record(p);
    expect(copy.draft.group).toEqual(activeVoiceGroup);
    expect(copy.draft.awaiting?.change).toEqual({type:"set_active_voice_track",track_id:second});
    expect(p).toEqual(before);
    expect(()=>prepareActiveVoiceSelection(p,first,intent)).toThrow(/already active/);
    expect(()=>prepareActiveVoiceSelection(p,writer,intent)).toThrow(/no longer/);
    expect(()=>prepareActiveVoiceSelection(p,second,"not-a-uuid")).toThrow(/identity/);
  });
  it("respects project content and recorded-source locks before preparing an intent",()=>{
    const p=project(); p.locks=[{id:writer,resource:"project:"+p.id,kind:"content",note:"Original content"}];
    expect(()=>record(p)).toThrow(/protected/);
    p.locks=[];p.production_design={narration_take_lock:{}} as Project["production_design"];
    expect(()=>record(p)).toThrow(/protected/);
  });
  it.each([1,2])("keeps UNKNOWN when saved values match at r+%i",increment=>{
    const p=project(),copy=record(p),observed=structuredClone(p);observed.revision+=increment;observed.audio.active_voice_track_id=second;
    const next=observeAudioDraft(copy.draft,observed);
    expect(next.acknowledged).toBe(false);expect(next.draft.awaiting).toEqual(copy.draft.awaiting);
    const raw=JSON.stringify({...copy,draft:next.draft});
    expect(decodeAudioRecovery(raw,copy.scope,activeVoiceGroup,writer).draft.awaiting?.selection?.outcome).toBe("UNKNOWN");
  });
  it("a reply for a different local intent cannot settle this record",()=>{
    const copy=record(),other=structuredClone(copy.draft.awaiting!);other.selection!.intentId=first;
    expect(settleActiveVoiceSelection(copy.draft,other,"ACKNOWLEDGED",null)).toBe(copy.draft);
  });
  it.each(["record","pointer","pointer-readback","record-readback"] as const)("does not invoke a Project change after failed %s retention",async fault=>{
    const local=new MemoryStorage(),session=new MemoryStorage(),copy=record();let calls=0;
    if(fault==="record")local.setItem=()=>{throw new Error("original record failure");};
    if(fault==="pointer")session.setItem=()=>{throw new Error("original pointer failure");};
    if(fault==="pointer-readback")session.getItem=()=>null;
    if(fault==="record-readback"){
      const set=local.setItem.bind(local);let written=false;
      local.setItem=(key,value)=>{set(key,value);written=true;};
      const get=local.getItem.bind(local);local.getItem=key=>{if(written)throw new Error("persisted bytes, lost readback");return get(key);};
    }
    const result=await sendRetainedActiveVoiceSelection(local,session,pointer,copy,async()=>{calls++;},()=>{});
    expect(result.outcome).toBe("NOT_SENT");expect(calls).toBe(0);expect(activeVoiceIsUnconfirmed(result.record.draft)).toBe(true);
    expect(result.record.draft.awaiting?.selection?.diagnostic).toContain("NOT_SENT:");
    const retained=local.data.get(audioRecoveryKey(copy.scope,writer));
    if(fault!=="record")expect(JSON.parse(retained!).draft.awaiting.selection.outcome).toBe("UNKNOWN");
  });
  it("retains the original ACK before a later recovery write fails",async()=>{
    const local=new MemoryStorage(),session=new MemoryStorage(),copy=record();let calls=0,preserved:AudioRecovery|undefined;
    const result=await sendRetainedActiveVoiceSelection(local,session,pointer,copy,async()=>{
      calls++;local.setItem=()=>{throw new Error("post-response custody failure");};
    },retained=>{preserved=retained;});
    expect(calls).toBe(1);expect(result.outcome).toBe("ACKNOWLEDGED");expect(preserved?.draft.awaiting?.selection?.outcome).toBe("ACKNOWLEDGED");
    expect(result.custodyError).toContain("post-response custody failure");
    const disk=JSON.parse(local.getItem(audioRecoveryKey(copy.scope,writer))!);
    expect(disk.draft.awaiting.selection.outcome).toBe("UNKNOWN");
  });
  it("preserves the exact thrown diagnostic and UNKNOWN when later custody also fails",async()=>{
    const local=new MemoryStorage(),session=new MemoryStorage(),copy=record();let calls=0,preserved:AudioRecovery|undefined;
    const original="Request outcome unavailable: original response lost";
    const result=await sendRetainedActiveVoiceSelection(local,session,pointer,copy,async()=>{
      calls++;local.setItem=()=>{throw new Error("secondary storage error");};throw new Error(original);
    },retained=>{preserved=retained;});
    expect(calls).toBe(1);expect(result.outcome).toBe("UNKNOWN");expect(result.diagnostic).toBe(original);
    expect(preserved?.draft.awaiting?.selection?.diagnostic).toBe(original);expect(result.custodyError).toContain("secondary storage error");
  });
  it("an outcome notification failure cannot erase the ACK or authorize a replay",async()=>{
    const local=new MemoryStorage(),session=new MemoryStorage(),copy=record();let calls=0;
    const result=await sendRetainedActiveVoiceSelection(local,session,pointer,copy,async()=>{calls++;},()=>{throw new Error("notification failed");});
    expect(result.outcome).toBe("ACKNOWLEDGED");expect(result.custodyError).toBe("notification failed");
    const again=await sendRetainedActiveVoiceSelection(local,session,pointer,result.record,async()=>{calls++;},()=>{});
    expect(again.outcome).toBe("NOT_SENT");expect(calls).toBe(1);
    expect(again.record.draft.awaiting?.selection?.outcome).toBe("ACKNOWLEDGED");
    expect(JSON.parse(local.getItem(audioRecoveryKey(copy.scope,writer))!).draft.awaiting.selection.outcome).toBe("ACKNOWLEDGED");
  });
  it("reviews an unrelated r+1 without acknowledging or mutating the old UNKNOWN",async()=>{
    const p=project(),copy=record(p),observed=structuredClone(p);observed.revision++;observed.title="Independent change";
    const local=new MemoryStorage();writeAudioRecovery(local,copy);
    const key=audioRecoveryKey(copy.scope,writer),before=local.getItem(key);
    const review=await inspectActiveVoiceOutcome(copy.draft,observed,transport(observed,[event(observed.revision,{type:"rename_project",title:observed.title})]));
    expect(review.matchingValueRevisions).toEqual([]);expect(activeVoiceIsUnconfirmed(copy.draft)).toBe(true);
    const separate=prepareSeparateActiveVoiceIntent(local,copy,observed,review);
    expect(separate.awaiting).toBeNull();expect(local.getItem(key)).toBe(before);
    expect(separate.fields.trackId.raw).toBe(first);
  });
  it("reviews a matching selection followed by r+2 as values, never request authorship",async()=>{
    const p=project(),copy=record(p),observed=structuredClone(p);observed.revision+=2;observed.audio.active_voice_track_id=second;
    const events=[event(p.revision+1,{type:"set_active_voice_track",track_id:second}),event(p.revision+2,{type:"rename_project",title:p.title})];
    const review=await inspectActiveVoiceOutcome(copy.draft,observed,transport(observed,events));
    expect(review.matchingValueRevisions).toEqual([p.revision+1]);expect(copy.draft.awaiting?.selection?.outcome).toBe("UNKNOWN");
    expect(activeVoiceReviewMatches(copy.draft,observed,review)).toBe(true);
    observed.revision++;expect(activeVoiceReviewMatches(copy.draft,observed,review)).toBe(false);
  });
  it("requires a contiguous complete journal and stable before/after Project",async()=>{
    const p=project(),copy=record(p),observed=structuredClone(p);observed.revision+=2;
    await expect(inspectActiveVoiceOutcome(copy.draft,observed,transport(observed,[]))).rejects.toThrow(/incomplete/);
    await expect(inspectActiveVoiceOutcome(copy.draft,observed,transport(observed,[event(observed.revision,{type:"rename_project"})]))).rejects.toThrow(/contiguous/);
    let reads=0;const unstable=transport(p,[]);unstable.readProject=async()=>{reads++;return reads===1?structuredClone(p):{...p,revision:p.revision+1};};
    await expect(inspectActiveVoiceOutcome(copy.draft,p,unstable)).rejects.toThrow(/changed during/);
  });
  it("rejects foreign scope, future source and oversized history without releasing UNKNOWN",async()=>{
    const p=project(),copy=record(p),foreign={...p,generation:writer};
    await expect(inspectActiveVoiceOutcome(copy.draft,foreign,transport(foreign,[]))).rejects.toThrow(/another project/);
    const future={...p,revision:p.revision+2049};
    await expect(inspectActiveVoiceOutcome(copy.draft,future,transport(future,[]))).rejects.toThrow(/review bound/);
    const old={...p,revision:p.revision-1};
    await expect(inspectActiveVoiceOutcome(copy.draft,old,transport(old,[]))).rejects.toThrow(/review bound/);
  });
  it("a changed or missing retained copy prevents a separate selection intent",async()=>{
    const p=project(),copy=record(p),review=await inspectActiveVoiceOutcome(copy.draft,p,transport(p,[])),local=new MemoryStorage();
    expect(()=>prepareSeparateActiveVoiceIntent(local,copy,p,review)).toThrow(/Retain/);
    const different=structuredClone(copy);different.draft.awaiting!.selection!.diagnostic="Changed by another writer";
    writeAudioRecovery(local,different);
    expect(()=>prepareSeparateActiveVoiceIntent(local,copy,p,review)).toThrow(/changed/);
    expect(activeVoiceIsUnconfirmed(copy.draft)).toBe(true);
  });
  it("rejects forged retained track, outcome, identity and cross-group selections",()=>{
    const copy=record();
    for(const mutate of [
      (v:AudioRecovery)=>{(v.draft.awaiting!.change as {track_id:string}).track_id=first;},
      (v:AudioRecovery)=>{v.draft.awaiting!.selection!.intentId="bad";},
      (v:AudioRecovery)=>{v.draft.awaiting!.selection!.outcome="PASS" as "UNKNOWN";},
      (v:AudioRecovery)=>{v.draft.group={kind:"mix"};},
    ]){const bad=structuredClone(copy);mutate(bad);expect(()=>decodeAudioRecovery(JSON.stringify(bad),copy.scope,activeVoiceGroup,writer)).toThrow();}
    expect(audioDraftScope(project(),activeVoiceGroup)).toBe(copy.scope);
  });

  it.each(["coercion", "message"] as const)("an unprintable %s rejection retains UNKNOWN and completes the original return", async kind => {
    const local=new MemoryStorage(),session=new MemoryStorage(),copy=record();let calls=0,preserved:AudioRecovery|undefined;
    const original=kind==="coercion"
      ? {toString(){throw new Error("Diagnostic coercion failed");}}
      : Object.defineProperty(new Error("Unreadable message"),"message",{get(){throw new Error("Diagnostic message getter failed");}});
    const result=await sendRetainedActiveVoiceSelection(local,session,pointer,copy,async()=>{calls++;throw original;},retained=>{preserved=retained;});
    expect(calls).toBe(1);expect(result.outcome).toBe("UNKNOWN");
    expect(result.diagnostic).toBe("The original diagnostic could not be displayed.");
    expect(result.record.writer).toBe(copy.writer);expect(result.record.draft.awaiting?.selection?.intentId).toBe(intent);
    expect(preserved?.draft.awaiting?.selection?.outcome).toBe("UNKNOWN");
    expect(JSON.parse(local.getItem(audioRecoveryKey(copy.scope,writer))!).draft.awaiting.selection).toEqual(result.record.draft.awaiting?.selection);
  });
  it.each(["notification", "storage"] as const)("an unprintable %s failure cannot replace an original ACK", async fault => {
    const local=new MemoryStorage(),session=new MemoryStorage(),copy=record();let calls=0,preserved:AudioRecovery|undefined;
    const original=Object.defineProperty(new Error("Unreadable secondary message"),"message",{get(){throw new Error("Secondary diagnostic getter failed");}});
    const result=await sendRetainedActiveVoiceSelection(local,session,pointer,copy,async()=>{
      calls++;if(fault==="storage")local.setItem=()=>{throw original;};
    },retained=>{preserved=retained;if(fault==="notification")throw original;});
    expect(calls).toBe(1);expect(result.outcome).toBe("ACKNOWLEDGED");expect(result.diagnostic).toBeNull();
    expect(preserved?.draft.awaiting?.selection?.outcome).toBe("ACKNOWLEDGED");
    expect(result.custodyError).toBe("The original diagnostic could not be displayed.");
    const disk=JSON.parse(local.getItem(audioRecoveryKey(copy.scope,writer))!);
    expect(disk.draft.awaiting.selection.outcome).toBe(fault==="storage"?"UNKNOWN":"ACKNOWLEDGED");
  });
  it("an unprintable pre-send custody error remains NOT_SENT without a Project invocation", async()=>{
    const local=new MemoryStorage(),session=new MemoryStorage(),copy=record();let calls=0,preserved:AudioRecovery|undefined;
    local.setItem=()=>{throw {toString(){throw new Error("Original custody diagnostic cannot be displayed");}};};
    const result=await sendRetainedActiveVoiceSelection(local,session,pointer,copy,async()=>{calls++;},retained=>{preserved=retained;});
    expect(calls).toBe(0);expect(result.outcome).toBe("NOT_SENT");
    expect(result.diagnostic).toBe("The original diagnostic could not be displayed.");
    expect(preserved?.draft.awaiting?.selection?.outcome).toBe("UNKNOWN");
    expect(preserved?.draft.awaiting?.selection?.diagnostic).toBe("NOT_SENT: The original diagnostic could not be displayed.");
  });
});
