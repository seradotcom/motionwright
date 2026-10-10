import {useEffect,useState} from 'react';
import {BookOpen,FlaskConical} from 'lucide-react';
import {creativeDistillationExperiment} from '../api';
import type {Project} from '../types';
import type {
 BrandProfile,ComponentRequest,CreativeCatalog,DistillationReference,
 Locale,SourceDistillationResponse,TasteProfile
} from './recipeTypes';

const initialSources=():DistillationReference[]=>
 Array.from({length:4},(_,index)=>({
  original_source_sha256:'',polarity:index<2?'positive':'negative',
  observed_strength_or_failure:'',source_rights_note:'',
  source_owner_attested_rights:false
 }));
const errorText=(e:unknown)=>e instanceof Error?e.message:String(e);
export default function CreativeDistillationWorkbench({
 project,blueprint,brand,taste,catalog,busy
}:{project:Project;blueprint:ComponentRequest;brand:BrandProfile;taste:TasteProfile;catalog:CreativeCatalog;busy:boolean}){
 const [sources,setSources]=useState<DistillationReference[]>(initialSources);
 const [intent,setIntent]=useState('Identify a reusable, source-preserving component from contrasted positive and negative original design studies.');
 const [ids,setIds]=useState<string[]>(()=>Array.from({length:9},()=>crypto.randomUUID()));
 const [result,setResult]=useState<SourceDistillationResponse|null>(null);
 const [working,setWorking]=useState(false),[error,setError]=useState<string|null>(null);
 useEffect(()=>{
  setResult(null);setError(null);
  setIds(Array.from({length:9},()=>crypto.randomUUID()));
 },[project.id,project.generation,project.revision,blueprint.instance_id,blueprint.recipe]);
 const edit=(index:number,change:Partial<DistillationReference>)=>{
  setSources(current=>current.map((item,i)=>i===index?{...item,...change}:item));setResult(null);
 };
 const buildVariations=():ComponentRequest[]=>{
  const outputRatios=[{width:640,height:360,aspect:'16:9'},{width:360,height:640,aspect:'9:16'},
   {width:640,height:640,aspect:'1:1'}];
  const locales:Locale[]=['en','es','de'];
  return outputRatios.flatMap((ratio,ratioIndex)=>locales.map((locale,localeIndex)=>({
   ...blueprint,
   instance_id:ids[ratioIndex*3+localeIndex],
   output:{...blueprint.output,width:ratio.width,height:ratio.height},
   locale,copy:{...catalog.default_copy[locale]},
  })));
 };
 const inspect=async()=>{
  setWorking(true);setError(null);setResult(null);
  try{
   if(sources.some(source=>!/^[a-f0-9]{64}$/.test(source.original_source_sha256)))
    throw new Error('Provide four distinct, lowercase SHA-256 source references from your own reviewed examples.');
   if(new Set(sources.map(source=>source.original_source_sha256)).size!==4)
    throw new Error('Positive and negative source examples must be independent.');
   if(sources.some(source=>!source.source_owner_attested_rights||!source.source_rights_note.trim()||
     !source.observed_strength_or_failure.trim()))
    throw new Error('Every positive and negative study requires an explicit observation and rights declaration.');
   const trial=await creativeDistillationExperiment(project,intent,blueprint,brand,taste,sources,buildVariations());
   if(trial.trial.variations.length!==9||trial.trial.rendered||trial.trial.executable_code_admitted)
    throw new Error('The native source experiment returned unexpected execution or admission authority.');
   setResult(trial);
  }catch(reason){setError(errorText(reason));}finally{setWorking(false);}
 };
 return <details className="native-contact-review" aria-label="Candidate recipe distillation experiments">
  <summary><BookOpen size={13}/> Source distillation · compare four design studies</summary>
  <p className="production-help">Draft from two positive and two negative original examples. This is a source-only experiment across nine locale/format variants, not a render, code installation, creative approval or permission request.</p>
  <label className="production-field"><span>Original design intent</span>
   <textarea value={intent} maxLength={2000} rows={3} disabled={busy||working}
    onChange={e=>{setIntent(e.target.value);setResult(null);}}/>
  </label>
  <div className="native-transform-fields">
   {sources.map((source,index)=><div key={index} className="native-difference">
    <strong>{source.polarity==='positive'?'Positive source':'Negative source'} {index%2+1}</strong>
    <label className="production-field"><span>Source SHA-256</span>
     <input value={source.original_source_sha256} maxLength={64} spellCheck={false}
      placeholder="64 lowercase hex digits" disabled={busy||working}
      onChange={e=>edit(index,{original_source_sha256:e.target.value.trim().toLowerCase()})}/>
    </label>
    <label className="production-field"><span>Observed design strength or failure</span>
     <textarea value={source.observed_strength_or_failure} maxLength={1200} rows={2}
      disabled={busy||working}
      onChange={e=>edit(index,{observed_strength_or_failure:e.target.value})}/>
    </label>
    <label className="production-field"><span>Original source owner / rights</span>
     <textarea value={source.source_rights_note} maxLength={900} rows={2}
      disabled={busy||working} onChange={e=>edit(index,{source_rights_note:e.target.value})}/>
    </label>
    <label className="production-check">
     <input type="checkbox" checked={source.source_owner_attested_rights}
      disabled={busy||working} onChange={e=>edit(index,{source_owner_attested_rights:e.target.checked})}/>
     I attest I can use this exact original source in a design experiment.
    </label>
   </div>)}
  </div>
  {error&&<p className="production-error" role="alert">{error}</p>}
  <button className="secondary-button" disabled={busy||working} onClick={inspect}>
   <FlaskConical size={14}/> Run nine-source variant trial
  </button>
  {result&&<div className="native-job-controls">
   <strong>Source validation only · nine design variants</strong>
   <p className="production-help">Pixel evidence: {result.trial.native_pixel_validation}. Human review: {result.trial.human_design_status}. Installation: {result.trial.owner_install_approval}. This analysis does not create or approve a library recipe.</p>
   <table className="native-fidelity-table"><thead>
    <tr><th>Ratio</th><th>Locale</th><th>Native source</th></tr>
   </thead><tbody>{result.trial.variations.map(variation=><tr key={variation.native_output_sha256}>
    <td>{variation.aspect}</td><td>{variation.locale}</td>
    <td className="mono">{variation.native_output_sha256.slice(0,12)}…</td>
   </tr>)}</tbody></table>
   <p className="production-help">Source set {result.trial.examples_sha256.slice(0,14)}… · original draft {result.trial.draft_sha256.slice(0,14)}… · all approvals remain with the owner.</p>
  </div>}
 </details>;
}
