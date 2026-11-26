import { Download } from "lucide-react";
import { useId, useState } from "react";
import type { Project, Scene } from "./types";
import type { NativeSourceFields } from "./nativeSourceDraft";
import { BLENDER_PRODUCT_RECIPE_MIME, PRODUCT_COATINGS, PRODUCT_CAMERA_CURVES,
  blenderProductMetadata, blenderProductSceneIssues, blenderRecipeFile, newBlenderProductInput,
  parseBlenderProductInput,withProductCameraPreset,withoutProductCameraMotion,type ProductCameraPreset,type BlenderProductRecipeInput } from "./blenderProductRecipe";

interface Props {
  project: Project; scene: Scene; capsuleId: string; fields: NativeSourceFields;
  blocked: boolean; change: (fields: Partial<NativeSourceFields>) => boolean;
  preferredCameraMotion?:ProductCameraPreset;
}
const msg = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
export default function BlenderProductRecipeEditor({project,scene,capsuleId,fields,blocked,change,preferredCameraMotion}: Props) {
  const heading = useId();
  const [notice,setNotice] = useState<string|null>(null);
  const [error,setError] = useState<string|null>(null);
  const issues = blenderProductSceneIssues(project,scene,capsuleId);
  let input: BlenderProductRecipeInput|null = null, raw = fields.blenderRecipe || "", problem: string|null = null;
  let file: ReturnType<typeof blenderRecipeFile>|null = null;
  try {
    input = raw ? parseBlenderProductInput(raw) : newBlenderProductInput(scene.id);
    raw = raw || JSON.stringify(input);
    file = blenderRecipeFile(raw,capsuleId);
  } catch (reason) { problem = msg(reason); }
  const patch = (field: keyof BlenderProductRecipeInput, value: string) => {
    if (!input) return;
    setNotice(null);setError(null);
    change({blenderRecipe: JSON.stringify({...input,[field]:value})});
  };
  const changeCameraMotion=(preset:ProductCameraPreset|"legacy")=>{
    if(!input||blocked||issues.length)return;
    setNotice(null);setError(null);
    const next=preset==="legacy"?withoutProductCameraMotion(input):withProductCameraPreset(input,preset);
    change({blenderRecipe:JSON.stringify(next)});
  };
  const motionEnabled=input?.arc_degrees!==undefined||input?.dolly_percent!==undefined;
  const exportFile = () => {
    setNotice(null);setError(null);
    try {
      if (blocked || issues.length || !file) return;
      // One explicit action prepares the existing source saver, including the
      // exact file digest. Do not dispatch any project or native mutation.
      if (!change(blenderProductMetadata(raw,capsuleId))) throw new Error("Verify the retained source input before preparing a recipe.");
      const url = URL.createObjectURL(new Blob([file.text], {type:BLENDER_PRODUCT_RECIPE_MIME}));
      try {
        const link = document.createElement("a");link.href=url;link.download=file.name;link.click();
      } finally { window.setTimeout(()=>URL.revokeObjectURL(url),1000); }
      setNotice("Recipe download requested. Choose its actual saved absolute path in Source file path, then Save source and capsule. The prepared import fields and expected digest are retained.");
    } catch(reason){setError(msg(reason));}
  };
  const capsule = project.production_design?.capsules.find(c=>c.id===capsuleId && c.scene_id===scene.id);
  const asset = project.assets.find(a=>a.id===capsule?.source_asset_id);
  const recipeBound = !!fields.blenderRecipeExport;
  const exactSaved = recipeBound && fields.mediaType===BLENDER_PRODUCT_RECIPE_MIME && asset?.media_type===BLENDER_PRODUCT_RECIPE_MIME
    && capsule?.source_sha256===fields.blenderRecipeExport && asset.content_sha256===fields.blenderRecipeExport;
  return <section className="production-notice" aria-labelledby={heading}>
    <div className="production-section-title"><div>
      <h3 id={heading}>Author Blender product recipe</h3>
      <p>Fixed vessel source: editable surface, annotation, two native camera shots. 40 seconds at 30 fps.</p>
    </div></div>
    <p className="production-help">Author a closed ProductScene v1 document, save its exact file through the existing source importer, then inspect the retained recipe in the Blender product workflow. This does not run Blender, render pixels, or edit an existing .blend.</p>
    {issues.length>0 && <ul aria-label="Product scene requirements">{issues.map(issue=><li key={issue}>{issue}</li>)}</ul>}
    {input ? <fieldset style={{border:0,padding:0,margin:0,minWidth:0}} disabled={blocked || issues.length>0}>
      <div className="production-plan-grid">
        <label className="production-field"><span>Product namespace</span><input value={input.namespace} maxLength={32} spellCheck={false} onChange={e=>patch("namespace",e.target.value)}/></label>
        <label className="production-field"><span>Product coating</span><select aria-label="Product coating" value={input.coating} onChange={e=>patch("coating",e.target.value)}>
          {PRODUCT_COATINGS.map(value=><option key={value} value={value}>{value[0].toUpperCase()+value.slice(1)}</option>)}
        </select></label>
        <label className="production-field"><span>Surface roughness</span><input type="text" inputMode="decimal" value={input.roughness} maxLength={64} onChange={e=>patch("roughness",e.target.value)}/></label>
        <label className="production-field"><span>Surface metallic</span><input type="text" inputMode="decimal" value={input.metallic} maxLength={64} onChange={e=>patch("metallic",e.target.value)}/></label>
        <label className="production-field"><span>Render samples</span><input type="text" inputMode="numeric" value={input.samples} maxLength={8} onChange={e=>patch("samples",e.target.value)}/></label>
        <label className="production-field"><span>Native camera curve</span><select aria-label="Native camera curve" value={input.camera_curve} disabled={motionEnabled} onChange={e=>patch("camera_curve",e.target.value)}>
          {PRODUCT_CAMERA_CURVES.map(value=><option key={value} value={value}>{value==="linear"?"Linear":"Bézier auto-clamped"}</option>)}
        </select></label>
      </div>
      <section aria-label="Original spatial camera motion">
        <h4>Spatial camera motion</h4>
        <p className="production-help">Optional native 3D camera path for the same two-shot vessel and exact 40-second clock.
          Arc uses three equal-radius waypoints joined by linear chords; Dolly moves along the ray toward the original surface focus without changing lens.
          These are authored source parameters, not a verified render.</p>
        {preferredCameraMotion&&<p role="status">Library choice: {preferredCameraMotion==="arc_reveal"?"Arc reveal":"Dolly focus"}. Choose its preset explicitly to change the retained draft.</p>}
        <div className="production-segmented">
          <button type="button" className="secondary-button" onClick={()=>changeCameraMotion("arc_reveal")}>Use Arc reveal</button>
          <button type="button" className="secondary-button" onClick={()=>changeCameraMotion("dolly_focus")}>Use Dolly focus</button>
          <button type="button" className="secondary-button" disabled={!motionEnabled} onClick={()=>changeCameraMotion("legacy")}>Restore legacy camera motion</button>
        </div>
        {motionEnabled&&<div className="production-plan-grid">
          <label className="production-field"><span>Arc degrees (0 or 10?35)</span>
            <input aria-label="Product arc degrees" inputMode="numeric" value={input.arc_degrees??""} maxLength={8} onChange={e=>patch("arc_degrees",e.target.value)}/>
          </label>
          <label className="production-field"><span>Dolly approach percent (0 or 5?18)</span>
            <input aria-label="Product dolly percent" inputMode="numeric" value={input.dolly_percent??""} maxLength={8} onChange={e=>patch("dolly_percent",e.target.value)}/>
          </label>
          <p className="production-help">Both nonzero values combine the two movements. A zero value retains that shot's original key positions. Both shots use explicit linear interpolation.</p>
        </div>}
      </section>
      <label className="production-field"><span>Product annotation</span><textarea aria-label="Product annotation" rows={3} maxLength={128} value={input.annotation} onChange={e=>patch("annotation",e.target.value)}/></label>
      {file && <div className="production-help">
        <strong>{file.name}</strong><p>Exact source: {file.byteLength} bytes. SHA-256: <span className="mono" style={{overflowWrap:"anywhere"}}>{file.sha256}</span></p>
        <details><summary>Review recipe JSON</summary><pre style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{file.text}</pre></details>
      </div>}
      <button type="button" className="secondary-button" disabled={!file || blocked || issues.length>0} onClick={exportFile}><Download size={14} aria-hidden="true"/>Download recipe and prepare source input</button>
    </fieldset> : null}
    {problem && <p role="status" className="production-help">{problem} Your exact input remains retained.</p>}
    {recipeBound && <div aria-label="Authored recipe file binding">
      <p>Prepared file SHA-256: <span className="mono" style={{overflowWrap:"anywhere"}}>{fields.blenderRecipeExport}</span></p>
      {file && fields.blenderRecipeExport!==file.sha256 && <p role="status">Recipe input changed. Download the new recipe before saving its file.</p>}
      {exactSaved ? <p role="status">Saved source metadata matches the prepared recipe digest. Native inspection of the CAS bytes is still required.</p>
        : capsule ? <p role="status">The current saved capsule does not match this prepared recipe. Review the actual retained source before running Blender.</p>
        : <p>Choose the downloaded file using its absolute path below. The importer reads that path at Save; a different file will not be acknowledged as this recipe.</p>}
      <button type="button" className="secondary-button" disabled={blocked} onClick={()=>{
        if(change({blenderRecipeExport:"",mediaType:"",path:""}))setNotice("The prepared file binding was cleared explicitly. The recipe draft remains available; enter a separate general source path and media type.");
      }}>Clear prepared recipe file binding</button>
    </div>}
    {error && <p role="alert" className="production-error">{error}</p>}
    {notice && <p role="status" className="production-help">{notice}</p>}
  </section>;
}
