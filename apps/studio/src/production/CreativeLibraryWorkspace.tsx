import {useEffect,useMemo,useState} from 'react';
import {Check,FilePlus2,Shapes,SlidersHorizontal} from 'lucide-react';
import {creativeComponentCatalog,creativeComponentProposal,nativeCreativeAvailable,creativeDataNormalize,creativeSoundAudition} from '../api';
import type {Change,Project,Scene,DeliverableProfile,Asset} from '../types';
import type {CreativeCatalog,Locale,CopyPack,BrandProfile,TasteProfile,DataSeries,ComponentRequest,CreativeComponentProposal} from './recipeTypes';
import type {NativeAsset} from './nativeTypes';
import CreativeDistillationWorkbench from './CreativeDistillationWorkbench';
import CreativeDirectionWorkbench from './CreativeDirectionWorkbench';
import './native-editor.css';

const reasons=(error:unknown)=>error instanceof Error?error.message:String(error);
function countFrames(scene:Scene,profile:DeliverableProfile):number {
  const n=BigInt(scene.duration.num)*BigInt(profile.frame_rate.num);
  const d=BigInt(scene.duration.den)*BigInt(profile.frame_rate.den);
  if(n<=0n||d<=0n||n%d!==0n)throw new Error('This cut cannot represent an exact number of native output frames; create a compatible deliverable first.');
  const frames=Number(n/d);
  if(!Number.isSafeInteger(frames)||frames<30||frames>3600)throw new Error('Native recipe output supports only 30–3600 frames per shot.');
  return frames;
}
function admittedAsset(asset:Asset|undefined,owner:string,license:string,authorized:boolean):NativeAsset|null {
  if(!asset?.content_sha256)return null;
  const kind=asset.media_type==='image/png'?'png':asset.media_type==='image/jpeg'?'jpeg':null;
  if(!kind)throw new Error('This creative component requires a digest-bound PNG or JPEG source, not an inferred product screenshot.');
  if(!owner.trim()||!license.trim()||!authorized)throw new Error('Declare the source owner, usage terms and explicit authorization for each media input.');
  return {id:asset.id,sha256:asset.content_sha256,kind,rights:{owner,license,attribution:asset.name,use_authorized:authorized,redistribute:false}};
}
function emphasis(classification:string){return classification.replaceAll('_',' ');}
export default function CreativeLibraryWorkspace({project,scene,displayProfile,commit,busy,onOpenNative}:{project:Project;scene:Scene|null;displayProfile:DeliverableProfile|null;commit:(change:Change)=>Promise<void>;busy:boolean;onOpenNative:()=>void}){
  const available=nativeCreativeAvailable();
  const [catalog,setCatalog]=useState<CreativeCatalog|null>(null),[error,setError]=useState<string|null>(null),[working,setWorking]=useState(false);
  const [kitId,setKitId]=useState<string>('editorial-precision'),[recipeId,setRecipeId]=useState<string>('hero-focus');
  const [locale,setLocale]=useState<Locale>('en'),[copy,setCopy]=useState<CopyPack|null>(null);
  const [brand,setBrand]=useState<BrandProfile|null>(null),[taste,setTaste]=useState<TasteProfile|null>(null);
  const [seed,setSeed]=useState(123),[motion,setMotion]=useState(true);
  const [sourceId,setSourceId]=useState(''),[secondId,setSecondId]=useState('');
  const [owner,setOwner]=useState(''),[license,setLicense]=useState(''),[authorized,setAuthorized]=useState(false);
  const [dataJson,setDataJson]=useState(''),[preview,setPreview]=useState<CreativeComponentProposal|null>(null);
  const [draftBasis,setDraftBasis]=useState<{component:ComponentRequest;brand:BrandProfile;taste:TasteProfile}|null>(null);
  const [instanceId,setInstanceId]=useState<string>(()=>crypto.randomUUID());
  const [auditionUrl,setAuditionUrl]=useState<string|null>(null);
  const [auditioning,setAuditioning]=useState(false);
  useEffect(()=>{
    let active=true;
    if(!available)return;
    creativeComponentCatalog().then(result=>{
      if(!active)return;
      setCatalog(result);setBrand(result.default_brand);setTaste(result.default_taste);
      setCopy(result.default_copy.en);setDataJson(JSON.stringify(result.default_data,null,2));
      setKitId(result.kits[0]?.id??'editorial-precision');setRecipeId(result.kits[0]?.recipes[0]??'hero-focus');
    }).catch(err=>{if(active)setError(reasons(err));});
    return()=>{active=false;};
  },[available,project.id,project.generation]);
  const kit=useMemo(()=>catalog?.kits.find(item=>item.id===kitId),[catalog,kitId]);
  const definition=catalog?.recipes.find(item=>item.id===recipeId);
  const current=project.production_design?.workspace?.native_scenes.find(doc=>doc.scene_id===scene?.id&&doc.profile_id===displayProfile?.id);
  useEffect(()=>{setInstanceId(current?.id??crypto.randomUUID());setPreview(null);},[current?.id,scene?.id,displayProfile?.id,project.generation]);
  useEffect(()=>{setPreview(null);},[project.revision]);
  useEffect(()=>()=>{if(auditionUrl)URL.revokeObjectURL(auditionUrl);},[auditionUrl]);
  const updateBrandColor=(role:string,color:string)=>{
    if(!brand)return;
    setBrand({...brand,colors:brand.colors.map(entry=>entry.role===role?{...entry,value:color}:entry)});setPreview(null);
  };
  const generate=async()=>{
    if(!catalog||!brand||!taste||!copy||!definition||!scene||!displayProfile)throw new Error('Select an actual editable scene and output.');
    setWorking(true);setError(null);setPreview(null);
    try{
      const frames=countFrames(scene,displayProfile);
      const asset=project.assets.find(a=>a.id===sourceId);
      const secondary=project.assets.find(a=>a.id===secondId);
      const source=sourceId?admittedAsset(asset,owner,license,authorized):null;
      const second=secondId?admittedAsset(secondary,owner,license,authorized):null;
      if(definition.requires_media&&!source)throw new Error('Select an imported, explicitly authorized original media asset.');
      if(['flow-bridge','evidence-pair'].includes(recipeId)&&!second)throw new Error('This comparison requires two distinct authorized source captures.');
      if(source&&second&&source.sha256===second.sha256)throw new Error('Comparison sources must have distinct content digests.');
      let data:DataSeries|null=null;
      if(definition.requires_data){
        const parsed=JSON.parse(dataJson) as DataSeries;
        if(parsed.origin==='synthetic_fixture'&&!parsed.source_title.toLowerCase().includes('synthetic'))throw new Error('Synthetic values must be explicitly labeled as synthetic, not product evidence.');
        data=await creativeDataNormalize(parsed);
      }
      const component:ComponentRequest={
        instance_id:current?.id??instanceId,recipe:recipeId,version:1,
        output:{width:displayProfile.width,height:displayProfile.height,
          rate:{num:Number(profileRateNum(displayProfile)),den:Number(profileRateDen(displayProfile))},
          frames,background:brand.colors.find(color=>color.role==='background')?.value ?? '#111922'},
        copy,locale,seed,motion,data,primary_asset:source,secondary_asset:second,
        procedural:{count:24,columns:6,influence_x:.62,influence_y:.38,amplitude:.32,falloff:.62,spacing:.15,
          path:[{x:0,y:.75},{x:.27,y:.16},{x:.65,y:.85},{x:1,y:.25}]},
      };
      const result=await creativeComponentProposal(project,scene.id,displayProfile.id,component,brand,taste);
      setDraftBasis({component:structuredClone(component),brand:structuredClone(brand),taste:structuredClone(taste)});
      setPreview(result);
    }catch(err){setError(reasons(err));}
    finally{setWorking(false);}
  };
  const audition=async()=>{
    if(!preview||preview.renderer_plan.backend!=='audio_score')return;
    setAuditioning(true);setError(null);
    try{
      const source=preview.renderer_plan.source as {applies_to_existing_bus:boolean};
      if(source.applies_to_existing_bus)throw new Error('Silence release requires a real authorized audio bus, not a fabricated preview.');
      const wav=await creativeSoundAudition(project,source,preview.source_sha256);
      if(wav.byteLength>3*1024*1024)throw new Error('Original sound audition exceeded the bounded transfer.');
      setAuditionUrl(URL.createObjectURL(new Blob([Uint8Array.from(wav).buffer],{type:'audio/wav'})));
    }catch(reason){setError(reasons(reason));}finally{setAuditioning(false);}
  };
  const save=async()=>{
    if(!preview?.normalized_edit)return;
    setWorking(true);setError(null);
    try{await commit({type:'edit_creative_workspace',edit:preview.normalized_edit});setPreview(null);onOpenNative();}
    catch(err){setError(reasons(err));}finally{setWorking(false);}
  };
  const chooseKit=(id:string)=>{const selected=catalog?.kits.find(k=>k.id===id);setKitId(id);setRecipeId(selected?.recipes[0]??recipeId);setPreview(null);};
  const chooseLocale=(next:Locale)=>{setLocale(next);if(catalog)setCopy(catalog.default_copy[next]);setPreview(null);};
  const canEdit=available&&!busy&&!working;
  return <div className="native-creative-editor" aria-label="Creative component library">
    <header className="native-editor-heading"><div><span className="eyebrow">ORIGINAL FIRST-PARTY LIBRARY</span><h2>Design with native recipes</h2>
      <p>36 candidate recipes · six art-direction kits · explicit brand, evidence and authored source. No generic screenshot automation.</p></div>
      <span className="production-status">REVISIONED SOURCE · NO IMPLICIT RENDER</span></header>
    {!available&&<div className="production-notice"><strong>Open in Motionwright desktop</strong><p>This browser preview cannot assert native recipe realization or create verified render frames.</p></div>}
    {error&&<p className="production-error" role="alert">{error}</p>}
    <CreativeDirectionWorkbench project={project} scene={scene} available={available}
      busy={busy||working} commit={commit}/>
    {catalog&&brand&&taste&&copy&&<div className="native-editor-layout">
      <aside className="native-object-list" aria-label="Art direction kits"><h3>Art direction</h3>
        {catalog.kits.map(item=><button key={item.id} aria-pressed={item.id===kitId} onClick={()=>chooseKit(item.id)}><strong>{emphasis(item.id)}</strong><small>{item.intent}</small></button>)}
        <p className="production-help">{kit?.constraints.join(' · ')}</p>
      </aside>
      <section className="native-editing-surface">
        <div className="native-creative-controls">
          <label className="production-field"><span>Component</span><select value={recipeId} onChange={e=>{setRecipeId(e.target.value);setPreview(null);}}>
            {catalog.recipes.filter(r=>kit?.recipes.includes(r.id)).map(r=><option key={r.id} value={r.id}>{r.title}</option>)}
          </select></label>
          <p className="production-help">{definition?.purpose}</p>
          <p className="production-help">Native backend: {emphasis(definition?.backend??'')} · {definition?.status.replaceAll('_',' ')}</p>
          <div className="native-transform-fields"><label className="production-field"><span>Locale</span><select value={locale} onChange={e=>chooseLocale(e.target.value as Locale)}><option value="en">English</option><option value="es">Español</option><option value="de">Deutsch</option></select></label>
            <label className="production-field"><span>Seed</span><input type="number" min={0} max={4294967295} value={seed} onChange={e=>setSeed(Number(e.target.value))}/></label>
            <label className="production-check"><input type="checkbox" checked={motion} onChange={e=>setMotion(e.target.checked)}/>Animate</label>
          </div>
          <details open><summary>Written argument and framing</summary>{(['eyebrow','headline','body','label_a','label_b','disclosure'] as const).map(field=><label key={field} className="production-field"><span>{emphasis(field)}</span>
            <textarea rows={field==='headline'?3:2} value={copy[field]} onChange={e=>{setCopy({...copy,[field]:e.target.value});setPreview(null);}}/></label>)}</details>
          <details><summary>Brand identity and creative preferences</summary>
            <label className="production-field"><span>Brand profile name</span><input value={brand.name} onChange={e=>setBrand({...brand,name:e.target.value})}/></label>
            <label className="production-field"><span>Provenance and usage terms</span><textarea value={brand.source_note} rows={2} onChange={e=>setBrand({...brand,source_note:e.target.value})}/></label>
            <div className="native-transform-fields">{(['background','surface','text','accent','muted_text','secondary'] as const).map(role=><label key={role} className="production-field"><span>{emphasis(role)}</span><input aria-label={role+' brand color'} type="color" value={brand.colors.find(c=>c.role===role)?.value ?? '#111922'} onChange={e=>updateBrandColor(role,e.target.value)}/></label>)}</div>
            <label className="production-field"><span>Typeface</span><select value={brand.font.kind} onChange={e=>setBrand({...brand,font:{kind:e.target.value as 'sans'|'mono'}})}><option value="sans">Instrument Sans</option><option value="mono">IBM Plex Mono</option></select></label>
            <div className="native-transform-fields"><label className="production-field"><span>Motion energy</span><input type="number" min={0} max={100} value={taste.motion_energy} onChange={e=>setTaste({...taste,motion_energy:Number(e.target.value)})}/></label>
              <label className="production-field"><span>Scene density</span><input type="number" min={0} max={100} value={taste.density} onChange={e=>setTaste({...taste,density:Number(e.target.value)})}/></label></div>
            <p className="production-help">Exact design choices become part of the committed native source. Changing this local profile alone does not mutate a saved component.</p></details>
          {(definition?.requires_media||sourceId||secondId)&&<details open><summary>Original media sources and usage authorization</summary>
            <p className="production-help">A source can only be an already imported asset with a SHA-256 digest. No stock photo or invented product capture is created.</p>
            <label className="production-field"><span>Primary source</span><select value={sourceId} onChange={e=>setSourceId(e.target.value)}><option value="">Select imported asset</option>{project.assets.filter(a=>a.content_sha256&&a.media_type.startsWith('image/')).map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
            {['flow-bridge','evidence-pair'].includes(recipeId)&&<label className="production-field"><span>Comparative source</span><select value={secondId} onChange={e=>setSecondId(e.target.value)}><option value="">Select second asset</option>{project.assets.filter(a=>a.content_sha256&&a.media_type.startsWith('image/')).map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>}
            <label className="production-field"><span>Source owner</span><input value={owner} onChange={e=>setOwner(e.target.value)} placeholder="Owner or licensor"/></label>
            <label className="production-field"><span>License / explicit usage terms</span><input value={license} onChange={e=>setLicense(e.target.value)} placeholder="Rights basis"/></label>
            <label className="production-check"><input type="checkbox" checked={authorized} onChange={e=>setAuthorized(e.target.checked)}/>I am authorized to use these assets for this project.</label></details>}
          {definition?.requires_data&&<details open><summary>Numeric source evidence</summary><p className="production-help">The default is synthetic and labeled. For production, enter an attributed source and observed values. The backend validates units, range and digest; this does not independently certify truth.</p>
            <textarea aria-label="Numeric data JSON" rows={9} spellCheck={false} className="native-source-code" value={dataJson} onChange={e=>setDataJson(e.target.value)}/></details>}
          <button className="primary-button" disabled={!canEdit||!scene||!displayProfile} onClick={generate}><Shapes size={15}/> Prepare verified editable proposal</button>
        </div>
      </section>
      <section className="native-render-view"><header><SlidersHorizontal size={15}/><strong>Source fidelity</strong></header>
        <div className="native-no-frame"><FilePlus2 size={30}/><p>Source plan, not a simulated render</p><span>Successful preparation proves typed editability and provenance, not pixel quality or creative superiority.</span></div>
        {preview&&<div className="native-difference"><Check size={15}/><div><strong>{emphasis(preview.recipe)} · v{preview.recipe_version}</strong><p>{emphasis(preview.source_classification)}</p>
          <p className="mono">Source sha256:{preview.source_sha256}</p>
          {preview.difference&&<p>Edited {preview.difference.changed_nodes.length} · added {preview.difference.added_nodes.length} · removed {preview.difference.removed_nodes.length}</p>}
          <p>Human creative review remains required.</p></div></div>}
        {preview?.normalized_edit?<button className="primary-button" disabled={!canEdit} onClick={save}><Check size={14}/> Commit native recipe as one revision</button>:
          preview&&<p className="production-help">{preview.renderer_plan.backend==='blender_stage'?'Blender stage plan is authored, but its Blender realization is not yet integrated with the existing Driver Host.':'Sound plan is authored, but its final mixing/export path is not yet validated.'}</p>}
        {preview?.renderer_plan.backend==='audio_score'&&<section className="native-job-controls" aria-label="Original synthetic sound audition">
          <strong>Original sound sketch · unmastered</strong>
          <button className="secondary-button" disabled={!available||busy||working||auditioning||!preview}
            onClick={audition}>Audition locally synthesized WAV</button>
          {auditionUrl&&<audio controls src={auditionUrl} preload="none" aria-label="Original sound audition"/>}
          <p className="production-help">48 kHz stereo PCM, deterministic and self-contained. No claim of voice sync, loudness mastering or licensed library music.</p>
        </section>}
        {preview&&<details className="native-runtime-panel">
          <summary>Original direction, craft and review advisors · 12</summary>
          <p className="production-help">Knowledge-only advisors cannot grant authority, install code, publish or independently approve the producer's output. Findings are scoped to the exact source revision and can be rejected by the owner.</p>
          {preview.skill_audit.skill_definitions.map(definition=><details key={definition.id}>
            <summary>{emphasis(definition.canonical_id)} · {definition.status.replaceAll('_',' ')}</summary>
            <p className="production-help">{definition.work_product}</p>
            <p className="production-help">Review condition: {definition.review_condition}</p>
            {preview.skill_audit.findings.filter(f=>f.skill===definition.id).map((item,index)=><div className="native-difference" key={index}>
              <div><strong>{emphasis(item.kind)}</strong><p>{item.observation}</p>
              <p>Risk: {item.risk}</p><p>Scoped next action: {item.scoped_next_action}</p>
              {item.object_id&&<p className="mono">Native object {item.object_id} · frames {item.source_frame_start}–{item.source_frame_end}</p>}</div>
            </div>)}
          </details>)}
        </details>}
        {preview&&<details className="native-runtime-panel">
          <summary>Renderer compatibility and explicit losses · 7 targets</summary>
          <p className="production-help">These are authoring-level assessments, not pixel test results. Runtime grants and creative approval are always separate.</p>
          <table className="native-fidelity-table"><thead><tr><th>Target</th><th>Editable</th><th>Converted</th><th>Unavailable</th></tr></thead>
          <tbody>{preview.fidelity_reports.map(report=><tr key={report.target}>
            <td>{emphasis(report.target)}</td>
            <td>{report.properties.filter(p=>p.state==='native'||p.state==='translated').length}</td>
            <td>{report.properties.filter(p=>p.state==='baked'||p.state==='approximated').length}</td>
            <td>{report.properties.filter(p=>p.state==='unavailable').length}</td>
          </tr>)}</tbody></table>
          {preview.fidelity_reports.map(report=><details key={report.target}><summary>{emphasis(report.target)} · {report.properties.length} authored feature groups</summary>
            <p className="production-help">{report.summary}</p>
            <ul>{report.properties.map(property=><li key={property.feature}><strong>{emphasis(property.feature)} — {property.state}.</strong> {property.loss_or_limitation}</li>)}</ul>
          </details>)}
        </details>}
        {preview&&draftBasis&&catalog&&<CreativeDistillationWorkbench
          project={project} blueprint={draftBasis.component} brand={draftBasis.brand}
          taste={draftBasis.taste} catalog={catalog} busy={!canEdit}/>}
        {preview&&<details><summary>Read the retained typed plan</summary><pre className="native-source-code" style={{overflow:'auto',maxHeight:340,fontSize:10}}>{JSON.stringify(preview.renderer_plan,null,2)}</pre></details>}
      </section>
    </div>}
  </div>;
}
function profileRateNum(profile:DeliverableProfile){return BigInt(profile.frame_rate.num)}
function profileRateDen(profile:DeliverableProfile){return BigInt(profile.frame_rate.den)}
