import {test,expect,type Page} from './support/selectedSyntheticProject';
import {fixtureBootstrap} from '../src/fixture';
import {emptyProductionDesign} from '../src/creativeProduction';
import type {Change,Project} from '../src/types';

// Actual Studio controls with an explicitly synthetic Tauri data transport.
// These are UI/interaction oracles, not native command execution, measured
// media, StudioService/SQLite or installed E2E evidence. The existing Rust
// approved_narration_cas test and native command implementation remain intact.
const voiceA='00000000-0000-4000-8000-000000000912',voiceB='00000000-0000-4000-8000-000000000915';
const segmentA='00000000-0000-4000-8000-000000000913',segmentB='00000000-0000-4000-8000-000000000916';
const cueA='00000000-0000-4000-8000-000000000914';
declare global {interface Window {__NARRATION_UI__:{project():Project;changes():Change[];failImpact:boolean}}}
const panel=(page:Page)=>page.locator('.audio-narration-source-review');
const comparison=(page:Page)=>page.getByRole('region',{name:'Narration words and timing comparison',exact:true});
async function setup(page:Page){
 const boot=structuredClone(fixtureBootstrap),p=boot.project;boot.native_sdk.mode='tauri';p.schema_version=2;p.locks=[];p.production_design=emptyProductionDesign();
 const first={...p.assets[0],id:'00000000-0000-4000-8000-000000000911',media_type:'audio/wav',content_sha256:'a'.repeat(64)};
 const second={...first,id:'00000000-0000-4000-8000-000000000917',content_sha256:'e'.repeat(64)};
 p.assets.push(first,second);p.audio.voice_tracks=[{id:voiceA,asset_id:first.id,label:'Original UI source',sample_rate_hz:48000,channels:1,
  measured_duration:{num:'5',den:'1'},source_sha256:first.content_sha256,loudness_lufs:null,true_peak_dbfs:null},
  {id:voiceB,asset_id:second.id,label:'Second UI source',sample_rate_hz:48000,channels:1,measured_duration:{num:'5',den:'1'},source_sha256:second.content_sha256,loudness_lufs:null,true_peak_dbfs:null}];
 p.audio.active_voice_track_id=voiceA;
 p.audio.transcript=[{id:segmentA,voice_track_id:voiceA,start:{num:'1001',den:'30000'},end:{num:'50051',den:'30000'},text:'  Original words with retained spaces  ',speaker:'Original speaker',alignment:{kind:'manual'}},
  {id:segmentB,voice_track_id:voiceB,start:{num:'2002',den:'30000'},end:{num:'60061',den:'30000'},text:'Second take original words',speaker:null,alignment:{kind:'manual'}}];
 p.audio.cues=[{id:cueA,label:'Original source cue',at:{num:'1001',den:'30000'},source_segment_id:segmentA,evidence:'manual'}];
 await page.addInitScript(initial=>{
  let project=structuredClone(initial.project);const changes:Change[]=[];
  const currentSource=()=>{
   const voice=project.audio.voice_tracks.find(track=>track.id===project.audio.active_voice_track_id)!;
   const transcript=project.audio.transcript.filter(segment=>segment.voice_track_id===voice.id),ids=new Set(transcript.map(segment=>segment.id));
   return {voice,transcript,cues:project.audio.cues.filter(cue=>cue.source_segment_id!==null&&ids.has(cue.source_segment_id))};
  };
  window.__NARRATION_UI__={project:()=>structuredClone(project),changes:()=>structuredClone(changes),failImpact:false};
  const runtime=window as unknown as {__TAURI_INTERNALS__:{invoke:(command:string,args?:Record<string,unknown>)=>Promise<unknown>}};
  runtime.__TAURI_INTERNALS__={invoke:async(command,args)=>{
   if(command==='bootstrap'||command==='project_projection_head'||command==='open_project')
    return window.__SYNTHETIC_PROJECT_READ__(command,args,{...initial,project:structuredClone(project)});
   if(command==='production_runtime_status')return {status:'missing',reason:'Synthetic UI test has no native runtime',expected_version:'1.0.0',observed_version:null,version_compatible:false,executable_sha256:null,connection_identity:null,pinned_revision:null};
   if(command==='waveform_page')throw new Error('Synthetic UI test supplies no measured waveform');
   if(command==='issue_effect_grant')return {token:'synthetic-ui-project-edit'};
   const request=args?.request as Record<string,unknown>|undefined;
   if(command==='apply_change'){
    if(!request||request.project_id!==project.id||request.generation!==project.generation||request.revision!==project.revision)throw new Error('Synthetic UI CAS rejected stale editor source');
    const change=request.change as Change;
    if(change.type==='upsert_transcript_segment'){
     const index=project.audio.transcript.findIndex(segment=>segment.id===change.segment.id);if(index<0)throw new Error('Synthetic UI source segment missing');
     project.audio.transcript[index]=structuredClone(change.segment);
    }else if(change.type==='upsert_audio_cue'){
     const index=project.audio.cues.findIndex(cue=>cue.id===change.cue.id);if(index<0)throw new Error('Synthetic UI source cue missing');
     project.audio.cues[index]=structuredClone(change.cue);
    }else if(change.type==='set_active_voice_track')project.audio.active_voice_track_id=change.track_id;
    else throw new Error('Unexpected synthetic UI mutation: '+change.type);
    changes.push(structuredClone(change));project.revision++;
    project.branches=project.branches.map(branch=>branch.id===project.active_branch?{...branch,head_revision:project.revision}:branch);
    return structuredClone(project);
   }
   if(!request||request.project_id!==project.id||request.generation!==project.generation||request.revision!==project.revision)throw new Error('Synthetic narration read scope changed');
   const source=currentSource();
   if(command==='native_narration_take_snapshot')return {schema:'motionwright.narration-source-preview/1',project_revision:project.revision,
    source:{schema:'motionwright.narration-source-snapshot/1',project_id:project.id,generation:project.generation,revision:project.revision,
     voice_track:structuredClone(source.voice),transcript:structuredClone(source.transcript),cues:structuredClone(source.cues),exact_content_sha256:'b'.repeat(64),
     source_classification:'EXISTING_PROJECT_AUDIO_NOT_IMPORTED_ASR_TRUTH',timing_review:'SOURCE_MANUAL_OR_MEASURED_HIGH_CONFIDENCE_ONLY',
     human_owner_lock_authenticated:false,audio_media_bytes_decoded:false,publication_approved:false},
    recordable_content_sha256:'c'.repeat(64),recordable_source_verified:true,recorded_take:null,read_only:true,lock_authenticated:false,media_decoded:false,creative_approval:'REQUIRES_HUMAN_REVIEW'};
   if(command==='native_narration_replacement_impact'){
    if(window.__NARRATION_UI__.failImpact)throw new Error('Controlled source impact read failure');
    const original=request.original as {revision:number;exact_content_sha256:string;voice_track:typeof source.voice;transcript:typeof source.transcript;cues:typeof source.cues};
    const ids=[...new Set([...original.transcript,...source.transcript].map(segment=>segment.id))].sort();
    const cues=[...new Set([...original.cues,...source.cues].map(cue=>cue.id))].sort();
    return {schema:'motionwright.narration-impact-preview/1',impact:{schema:'motionwright.narration-impact-preview/1',previous_project_revision:original.revision,next_project_revision:project.revision,
     previous_source_sha256:original.exact_content_sha256,candidate_source_sha256:'d'.repeat(64),changed_segment_ids:ids,changed_cue_ids:cues,
     affected_spans:original.transcript.map(segment=>({source_segment_id:segment.id,start:segment.start,end:segment.end})),voice_media_replaced:source.voice.id!==original.voice_track.id,
     caption_timing_invalidation:'CAPTIONS_MUST_REGENERATE_AND_HUMAN_REVIEW',cuts_broll_invalidation:'CUTS_AND_BROLL_REQUIRE_REEVALUATION',sound_mix_invalidation:'MIX_MUST_REVIEW_CUE_AND_TIMING_CONSEQUENCES',
     scene_dependency_validation:'NOT_RUN',origin_locked_source_unchanged:true,candidate_committed:false,human_owner_approved:false},
     timeline_review:{schema:'motionwright.narration-timeline-dependency-preview/1',project_id:project.id,generation:project.generation,source_revision:original.revision,current_revision:project.revision,
      exact_source_sha256:'d'.repeat(64),locally_overlapping_scene_ids:[],scene_ids_requiring_cut_review:project.scenes.map(scene=>scene.id),scene_ids_requiring_broll_review:project.scenes.map(scene=>scene.id),
      deliverable_ids_requiring_caption_review:project.deliverables.filter(profile=>profile.captions||profile.burn_in_captions).map(profile=>profile.id),source_scene_count:project.scenes.length,
      dependency_scope:'CONSERVATIVE_WHOLE_PROJECT_CUT_BROLL_REVIEW',impact_reason:'Explicit synthetic UI transport',original_content_preserved:true,project_mutated:false,media_rendered:false,audio_regenerated:false,human_approved:false},
     applied:false,source_locked:false,owner_approval:'REQUIRED',media_or_captions_rendered:false};
   }
   throw new Error('Unexpected synthetic narration UI command: '+command);
  }};
 },boot);
 await page.goto('/');await page.getByRole('button',{name:'Audio',exact:true}).click();await panel(page).locator('summary').click();
 await panel(page).getByRole('button',{name:'Review measured source baseline',exact:true}).click();
 await expect(panel(page).getByRole('region',{name:'Original narration source snapshot',exact:true})).toBeVisible();
 return structuredClone(p);
}
async function saveWords(page:Page,text:string){
 const field=page.getByLabel('Transcript '+segmentA,{exact:true});await field.fill(text);
 await field.locator('..').getByRole('button',{name:'Save transcript segment as explicit manual alignment',exact:true}).click();
 await expect.poll(async()=>page.evaluate(id=>window.__NARRATION_UI__.project().audio.transcript.find(segment=>segment.id===id)?.text,segmentA)).toBe(text);
}
async function compare(page:Page){await panel(page).getByRole('button',{name:'Compare newer revision',exact:true}).click();await expect(comparison(page)).toBeVisible();}

test('Narration UI compares exact original words and rational times, then opens the existing transcript without saving',async({page})=>{
 const original=await setup(page);await saveWords(page,'A separately authored current sentence');await compare(page);
 const row=comparison(page).getByRole('region',{name:'Narration segment comparison '+segmentA,exact:true});
 await expect(row).toContainText('Original words with retained spaces');await expect(row).toContainText('A separately authored current sentence');
 await expect(row).toContainText('1001/30000 s');await expect(row).toContainText('50051/30000 s');
 const before=await page.evaluate(()=>({project:window.__NARRATION_UI__.project(),changes:window.__NARRATION_UI__.changes()}));
 await row.getByRole('button',{name:'Open current transcript '+segmentA,exact:true}).click();
 await expect(page.getByLabel('Transcript '+segmentA,{exact:true})).toBeFocused();
 expect(await page.evaluate(()=>({project:window.__NARRATION_UI__.project(),changes:window.__NARRATION_UI__.changes()}))).toEqual(before);
 await saveWords(page,'A second explicit correction from the existing editor');
 await expect(comparison(page)).toHaveCount(0);await expect(panel(page)).toContainText('earlier narration comparison is stale');await expect(panel(page)).toContainText(segmentA);
 const after=await page.evaluate(()=>window.__NARRATION_UI__.project());expect(after.revision).toBe(original.revision+2);
 expect(after.audio.voice_tracks).toEqual(original.audio.voice_tracks);expect(after.audio.cues).toEqual(original.audio.cues);
 expect(after.audio.transcript[0].id).toBe(segmentA);expect(after.audio.transcript[1]).toEqual(original.audio.transcript[1]);
});
test('Narration UI opens the current cue at its stable field and changes no Project data by navigation',async({page})=>{
 await setup(page);await saveWords(page,'Current words require the linked cue to be reviewed');await compare(page);
 const before=await page.evaluate(()=>({project:window.__NARRATION_UI__.project(),changes:window.__NARRATION_UI__.changes()}));
 await comparison(page).getByRole('button',{name:'Open current cue '+cueA,exact:true}).click();
 await expect(page.getByLabel('Cue label '+cueA,{exact:true})).toBeFocused();
 expect(await page.evaluate(()=>({project:window.__NARRATION_UI__.project(),changes:window.__NARRATION_UI__.changes()}))).toEqual(before);
});
test('Narration UI preserves removed-take words and never activates that take while inspecting the current comparison',async({page})=>{
 await setup(page);await page.getByRole('button',{name:'Activate voice Second UI source',exact:true}).click();
 await expect(page.getByLabel('Transcript '+segmentB,{exact:true})).toBeVisible();await compare(page);
 const old=comparison(page).getByRole('region',{name:'Narration segment comparison '+segmentA,exact:true});
 await expect(old).toContainText('Original words with retained spaces');await expect(old).toContainText('removed');
 await expect(old.getByRole('button',{name:/Open current transcript/})).toHaveCount(0);
 const before=await page.evaluate(()=>({project:window.__NARRATION_UI__.project(),changes:window.__NARRATION_UI__.changes()}));
 await comparison(page).getByRole('button',{name:'Open current transcript '+segmentB,exact:true}).click();
 await expect(page.getByLabel('Transcript '+segmentB,{exact:true})).toBeFocused();
 expect(await page.evaluate(()=>({project:window.__NARRATION_UI__.project(),changes:window.__NARRATION_UI__.changes()}))).toEqual(before);
});
test('Narration UI exposes a failed source read and retained original IDs without offering stale correction actions',async({page})=>{
 await setup(page);await saveWords(page,'Current source for failure inspection');await compare(page);
 const before=await page.evaluate(()=>window.__NARRATION_UI__.changes());await page.evaluate(()=>{window.__NARRATION_UI__.failImpact=true;});
 await panel(page).getByRole('button',{name:'Compare newer revision',exact:true}).click();
 await expect(panel(page).getByRole('alert')).toContainText('Controlled source impact read failure');
 await expect(comparison(page)).toHaveCount(0);await expect(panel(page)).toContainText(segmentA);await expect(panel(page)).toContainText(cueA);
 expect(await page.evaluate(()=>window.__NARRATION_UI__.changes())).toEqual(before);
 await page.evaluate(()=>{window.__NARRATION_UI__.failImpact=false;});await compare(page);
 await expect(comparison(page).getByRole('button',{name:'Open current transcript '+segmentA,exact:true})).toBeEnabled();
});
