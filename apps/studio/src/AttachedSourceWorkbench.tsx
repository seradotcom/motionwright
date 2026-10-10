import {useEffect,useState} from 'react';
import {Eye,ShieldCheck} from 'lucide-react';
import {nativeAttachedSourceInspection} from './api';
import type {Project} from './types';
import type {NativeCapsule} from './creativeProduction';
import type {AttachedSourceInspection,AttachedInspectionResponse} from './attachedSourceTypes';

const describe=(value:unknown)=>value instanceof Error?value.message:String(value);
export default function AttachedSourceWorkbench({
  project,capsule,busy
}:{project:Project;capsule:NativeCapsule;busy:boolean}){
  const [inspection,setInspection]=useState<AttachedSourceInspection|null>(null);
  const [error,setError]=useState<string|null>(null),[reading,setReading]=useState(false);
  useEffect(()=>{setInspection(null);setError(null);},
    [project.id,project.generation,project.revision,capsule.source_sha256,capsule.id]);
  const inspect=async()=>{
    setReading(true);setError(null);setInspection(null);
    try{
      const reply:AttachedInspectionResponse=await nativeAttachedSourceInspection(project,capsule.id);
      const report=reply.observation;
      if(reply.project_revision!==project.revision||
        report.source_sha256!==capsule.source_sha256||
        report.capsule_id!==capsule.id||
        !report.original_binary_preserved||
        report.semantically_editable_by_motionwright||
        reply.project_changed||reply.owner_runtime_granted||
        reply.source_semantically_imported){
        throw new Error('Current-source inspection attempted to claim a different asset or unearned native authority.');
      }
      setInspection(report);
    }catch(err){setError(describe(err));}finally{setReading(false);}
  };
  return <details className="attached-source-review">
    <summary><Eye size={13}/> Inspect original source without importing its effects</summary>
    <p className="production-help">Read-only, SHA-256-bound inspection of the exact stored project asset. The browser demo cannot access imported source files. Blender files remain opaque; glTF GLB structure can be observed, not edited or executed.</p>
    <button type="button" className="secondary-button" disabled={busy||reading}
      onClick={()=>void inspect()}>{reading?'Reading bounded source…':'Inspect persisted source'}</button>
    {error&&<p className="production-error" role="alert">{error} The attached asset remains unchanged.</p>}
    {inspection&&<div className="attached-source-results">
      <div className="attached-source-heading"><ShieldCheck size={15}/><strong>{inspection.source_format.replaceAll('_',' ')}</strong>
        <small>{inspection.source_bytes.toLocaleString()} original bytes</small></div>
      <p className="mono">{inspection.source_sha256}</p>
      <p className="production-help">Original bytes preserved. Current Motionwright semantic editability: unavailable. Runtime execution, renderer readback, rights grants and artistic approval were not performed.</p>
      <table><thead><tr><th>Original source part</th><th>What is verifiable</th><th>Count</th></tr></thead>
      <tbody>{inspection.properties.map((property,index)=><tr key={property.path+'-'+index}>
        <td>{property.path}</td><td><strong>{property.disposition.replaceAll('_',' ')}</strong><p>{property.note}</p></td>
        <td>{property.count??'—'}</td>
      </tr>)}</tbody></table>
      {inspection.glb_chunks.length>0&&<details>
        <summary>Original GLB chunks · {inspection.glb_chunks.length}</summary>
        <ul>{inspection.glb_chunks.map((part,index)=><li key={index}>
          {part.chunk_type} · {part.bytes.toLocaleString()} bytes · SHA {part.sha256.slice(0,16)}…
          <span> — opaque preserved bytes, not imported editable geometry</span>
        </li>)}</ul>
      </details>}
      {(inspection.used_extensions.length>0||inspection.unknown_root_fields.length>0)&&<details>
        <summary>Unknown or external source semantics remain opaque</summary>
        {!!inspection.used_extensions.length&&<p>Original declared extensions: {inspection.used_extensions.join(', ')}</p>}
        {!!inspection.required_extensions.length&&<p>Required original extensions: {inspection.required_extensions.join(', ')}</p>}
        {!!inspection.unknown_root_fields.length&&<p>Unknown metadata fields retained: {inspection.unknown_root_fields.join(', ')}</p>}
        <p>None of these extensions were installed, executed, translated or stripped. The original GLB must be opened by a separately authorized application for semantic editing.</p>
      </details>}
      <p className="production-help">Use portable project export to preserve the original blob and source identity. This inspector does not generate a native Motionwright composition or assert visual fidelity.</p>
    </div>}
  </details>;
}
