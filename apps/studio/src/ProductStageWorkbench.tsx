import { useEffect, useState } from "react";
import { ArrowUpRight, RefreshCw, Save } from "lucide-react";
import type { CanvasNode, Change, Project, Scene } from "./types";
import { seconds } from "./types";
import CompositionStudy from "./CompositionStudy";
import { inheritedTextInk } from "./canvasInheritedInk";
import { sameValue } from "./creativeProduction";
import {
  defaultProductStageConfig, realizeProductStage, validateProductStageConfig,
  PRODUCT_STAGE_DISCLOSURE
} from "./productStage";
import type { ProductStageConfig } from "./productStage";
const aspects=[
  {label:"16:9",width:1920,height:1080},
  {label:"9:16",width:1080,height:1920},
  {label:"1:1",width:1080,height:1080},
] as const;
const previewId="00000000-0000-4000-8000-000000000005";
type Props={
  project:Project;scene:Scene|null;commit:(change:Change)=>Promise<void>;
  busy:boolean;playhead:number;onSeek:(n:number)=>void;onOpenCanvas:()=>void;
};
export default function ProductStageWorkbench({project,scene,commit,busy,playhead,onSeek,onOpenCanvas}:Props){
  const stored=project.production_design?.stages?.find(stage=>stage.scene_id===scene?.id);
  const linked=project.production_design?.stage_capture_cards?.find(card=>card.stage_id===stored?.id);
  const sources=(project.production_design?.captures??[])
    .filter(c=>c.source_project_id===project.id && c.declared_scope==="first-party-synthetic-fixture-only" &&
      project.assets.some(a=>a.id===c.screenshot_asset_id &&
        a.media_type==="image/png" && a.content_sha256===c.screenshot_sha256));
  const [selectedCapture,setSelectedCapture]=useState(linked?.capture_id??"");
  const [draft,setDraft]=useState<ProductStageConfig>(()=>
    structuredClone(stored?.config??defaultProductStageConfig())
  );
  const [base,setBase]=useState(project.revision);
  const [aspect,setAspect]=useState<(typeof aspects)[number]>(aspects[0]);
  const [preview,setPreview]=useState<CanvasNode[]>([]);
  const [previewError,setPreviewError]=useState<string|null>(null);
  const [loading,setLoading]=useState(false);
  const [commitError,setCommitError]=useState<string|null>(null);
  const changed=base!==project.revision;
  const locked=project.locks.some(lock=>
    ["project:"+project.id,"scene:"+scene?.id].includes(lock.resource) &&
    ["content","position","style","timing","renderer"].includes(lock.kind));
  const valid=(()=>{
    try{validateProductStageConfig(draft);return true;}catch{return false;}
  })();
  const brandConflict=(()=>{
    const policy=project.production_design?.brand_profile;
    if(!policy || !scene)return null;
    const waived=(id:string)=>project.production_design?.brand_exceptions?.some(
      exception=>exception.scene_id===scene.id && exception.rule_id===id)??false;
    for(const rule of policy.rules){
      if(waived(rule.id))continue;
      if(rule.kind==="allowed_accents" &&
        !rule.colors.some(color=>color.toLowerCase()===draft.accent.toLowerCase()))
        return "This ProductStage accent is outside the mandatory BrandProfile palette.";
      if(rule.kind==="required_wordmark")
        return "This generic stage cannot satisfy the required wordmark; use an approved scene-scoped exception.";
      if(rule.kind==="forbidden_phrase" &&
        [draft.screen_heading,draft.screen_detail,draft.action_label].some(
          text=>text.toLowerCase().includes(rule.phrase.toLowerCase())))
        return "ProductStage copy contains a phrase forbidden by the active BrandProfile.";
    }
    return null;
  })();
  const canSave=!!scene && scene.renderer==="motion-canvas" && seconds(scene.duration)>=2 &&
    valid && !brandConflict && !!preview.length && !previewError && !loading && !changed && !locked && !busy;
  function reload(){
    setDraft(structuredClone(stored?.config??defaultProductStageConfig()));
    setSelectedCapture(linked?.capture_id??"");
    setBase(project.revision);setCommitError(null);
  }
  useEffect(()=>{
    setDraft(structuredClone(stored?.config??defaultProductStageConfig()));
    setSelectedCapture(linked?.capture_id??"");
    setBase(project.revision);setCommitError(null);
    // Deliberately not reset on same-scene remote revision: retain human draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[project.generation,scene?.id]);
  useEffect(()=>{
    if(stored && sameValue(stored.config,draft))setBase(project.revision);
  },[stored,project.revision,draft]);
  useEffect(()=>{
    let active=true;
    setLoading(true);setPreviewError(null);
    realizeProductStage(stored?.id??previewId,draft,aspect.width,aspect.height).then(
      nodes=>{if(active){setPreview(nodes);setLoading(false);}},
      error=>{if(active){setPreview([]);setPreviewError(String(error instanceof Error?error.message:error));setLoading(false);}}
    );
    return ()=>{active=false;};
  },[stored?.id,draft,aspect]);
  const localTime=scene?Math.max(0,Math.min(seconds(scene.duration),playhead-seconds(scene.start))):0;
  const background=project.visual_language.palette.find(token=>
    ["surface","background"].includes(token.name.toLowerCase()))?.value??"#0F1216";
  async function save(){
    if(!canSave || !scene)return;
    setCommitError(null);
    try{
      await commit({type:"upsert_product_stage",instance_id:stored?.id??crypto.randomUUID(),
        scene_id:scene.id,config:draft});
      setBase(project.revision+1);
    }catch(e){setCommitError(String(e instanceof Error?e.message:e));}
  }
  async function detach(){
    if(!stored||busy||changed||locked)return;
    setCommitError(null);
    try{
      await commit({type:"detach_product_stage",instance_id:stored.id});
      setBase(project.revision+1);
    }catch(e){setCommitError(String(e instanceof Error?e.message:e));}
  }
  async function bindSource(){
    if(!stored || !selectedCapture || busy || changed || locked ||
      !sources.some(source=>source.id===selectedCapture))return;
    setCommitError(null);
    try{
      await commit({type:"bind_stage_capture",stage_id:stored.id,capture_id:selectedCapture});
      setBase(project.revision+1);
    }catch(e){setCommitError(String(e instanceof Error?e.message:e));}
  }
  async function detachSource(){
    if(!stored || !linked || busy || changed || locked)return;
    setCommitError(null);
    try{
      await commit({type:"detach_stage_capture",stage_id:stored.id});
      setSelectedCapture("");
      setBase(project.revision+1);
    }catch(e){setCommitError(String(e instanceof Error?e.message:e));}
  }
  const patch=<K extends keyof ProductStageConfig>(key:K,value:ProductStageConfig[K])=>
    setDraft(old=>({...old,[key]:value}));
  return <div className="production-split">
    <section className="production-inspector" aria-label="ProductStage controls">
      <div className="production-section-title">
        <div><h2>ProductStage</h2><p>Generic original UI study · editable Canvas anchors</p></div>
      </div>
      <span className="production-status">ORIGINAL GRAPHIC · NOT A CAPTURE</span>
      <p className="production-help">Three unbranded silhouettes. No commercial model,
        third-party logo, actual browser session, opaque source execution or
        screenshot is inserted. This is a graphic study, not proof of real software behavior.</p>
      {changed && <div className="production-notice" role="status">
        <strong>Stage draft was based on revision {base}; current project revision is {project.revision}.</strong>
        <span>Unsaved human text is preserved until explicit reload.</span>
        <button className="secondary-button" onClick={reload}><RefreshCw size={14}/> Reload stage draft</button>
      </div>}
      <label className="production-field"><span>Neutral stage silhouette</span>
        <select aria-label="ProductStage frame" value={draft.frame} onChange={e=>
          patch("frame",e.target.value as ProductStageConfig["frame"])}>
          <option value="browser">Browser · anonymous chrome</option>
          <option value="flat_panel">Panel · no device manufacturer</option>
          <option value="generic_handset">Handset · generic outline</option>
        </select></label>
      <label className="production-field"><span>Original screen heading</span>
        <input aria-label="ProductStage heading" maxLength={40} value={draft.screen_heading}
          onChange={e=>patch("screen_heading",e.target.value)}/></label>
      <label className="production-field"><span>Original screen detail</span>
        <textarea aria-label="ProductStage detail" rows={2} maxLength={80} value={draft.screen_detail}
          onChange={e=>patch("screen_detail",e.target.value)}/></label>
      <label className="production-field"><span>Action label</span>
        <input aria-label="ProductStage action" maxLength={20} value={draft.action_label}
          onChange={e=>patch("action_label",e.target.value)}/></label>
      <div className="production-color-fields">
        {([["screen_surface","Screen surface"],["foreground","Foreground"],["accent","Accent"]] as const)
          .map(([key,label])=><label className="production-field" key={key}><span>{label}</span>
            <input type="color" aria-label={"ProductStage "+label.toLowerCase()}
              value={draft[key]} onChange={e=>patch(key,e.target.value)}/></label>)}
      </div>
      <div className="production-plan-grid">
        <label className="production-field"><span>Focus X / 1000</span>
          <input aria-label="ProductStage focus X" type="number" min={150} max={850} step={10}
            value={draft.focus_x_permille} onChange={e=>patch("focus_x_permille",Number(e.target.value))}/></label>
        <label className="production-field"><span>Focus Y / 1000</span>
          <input aria-label="ProductStage focus Y" type="number" min={200} max={800} step={10}
            value={draft.focus_y_permille} onChange={e=>patch("focus_y_permille",Number(e.target.value))}/></label>
      </div>
      <label className="production-check"><input aria-label="Animate ProductStage focus" type="checkbox"
        checked={draft.animate_focus} onChange={e=>patch("animate_focus",e.target.checked)}/>
        Animate original focus marker with native editable keyframes</label>
      <label className="production-field"><span>Author's declaration of original-content rights</span>
        <textarea rows={2} aria-label="ProductStage rights attestation" maxLength={160}
          placeholder="Explain why you are authorized to publish this original UI graphic"
          value={draft.author_attestation} onChange={e=>patch("author_attestation",e.target.value)}/>
      </label>
      {!draft.author_attestation.trim() && <p className="production-error" role="status">
        Original-content rights must be explicitly declared before publishing or previewing.
      </p>}
      <p className="production-help">{PRODUCT_STAGE_DISCLOSURE}. This disclosure is mandatory
        in native output while the stage is bound. Replacing content preserves stable screen
        and focus anchors, compatible human overrides and the existing scene camera.</p>
      {brandConflict && <p role="status" className="production-error">{brandConflict}</p>}
      {previewError && draft.author_attestation.trim() && <p role="alert" className="production-error">{previewError}</p>}
      {commitError && <p role="alert" className="production-error">{commitError}</p>}
      <button className="primary-button" disabled={!canSave} onClick={()=>void save()}>
        <Save size={14}/>{stored?"Update ProductStage":"Add ProductStage to scene"}
      </button>
      {stored && <div className="production-button-row">
        <button className="secondary-button" onClick={onOpenCanvas}><ArrowUpRight size={14}/> Edit stage objects</button>
        <button className="text-button" disabled={busy||changed||locked||!!linked} onClick={()=>void detach()}>
          Detach, retain objects</button>
      </div>}
      <div className="production-section-title"><h3>First-party source tile</h3></div>
      <p className="production-help">
        Optionally attach a previously registered, content-addressed first-party
        Studio capture to the original card panel. Native Film uses the original
        screenshot PNG as a contained source image, never a fabricated capture,
        external application or replacement for the other 14 editable nodes.
      </p>
      {!stored && <p role="status">Save the original ProductStage before attaching a source.</p>}
      {stored && <>
        <label className="production-field"><span>Previously registered synthetic first-party capture</span>
          <select aria-label="ProductStage first-party capture" value={selectedCapture}
            disabled={busy||changed||locked} onChange={e=>setSelectedCapture(e.target.value)}>
            <option value="">Choose an immutable capture-source receipt</option>
            {sources.map(c=><option key={c.id} value={c.id}>
              Studio v{c.app_version} ? {c.screenshot_sha256.slice(0,12)}? ? source revision {c.source_revision}
            </option>)}
          </select>
        </label>
        {!sources.length && <p role="status">No registered first-party source capture is available. Use Production ? Capture sources.</p>}
        <div className="production-button-row">
          <button className="secondary-button" onClick={()=>void bindSource()}
            disabled={busy||changed||locked||!selectedCapture||linked?.capture_id===selectedCapture}>
            Bind source tile
          </button>
          <button className="text-button" onClick={()=>void detachSource()}
            disabled={busy||changed||locked||!linked}>
            Detach source tile
          </button>
        </div>
        {linked && <div className="production-evidence-note" role="status">
          <strong>First-party source attached ? technical fixture, not customer evidence</strong>
          <p>PNG sha256: {sources.find(c=>c.id===linked.capture_id)?.screenshot_sha256??"missing"}
            . The editorial vector preview does not display this binary.
            Verify the actual Semwright native image renderer and source registry before publishing.
            Detach this tile before detaching ProductStage itself.</p>
        </div>}
      </>}
    </section>
    <section className="production-viewer" aria-label="ProductStage editable study viewer">
      <div className="production-viewer-toolbar">
        <div><strong>Original stage preview</strong><span>
          {sameValue(draft,stored?.config)?"Stored parameters":"Uncommitted parameters"} · editorial only
        </span></div>
        <div className="production-segmented" aria-label="ProductStage preview aspect">
          {aspects.map(item=><button key={item.label} aria-pressed={item===aspect} onClick={()=>setAspect(item)}>
            {item.label}</button>)}
        </div>
      </div>
      <div className={"production-preview-mat "+(aspect.height>aspect.width?"portrait":"")}>
        {loading?<p role="status">Preparing stage preview…</p>:
          preview.length?<CompositionStudy nodes={preview} width={aspect.width} height={aspect.height}
            background={background} time={localTime} ink={inheritedTextInk(project.visual_language.palette)}
            label={"ProductStage "+draft.frame+" "+aspect.label+", original graphic, editorial preview"}/>:
          <p role="status">Declare original-content rights to preview this stage.</p>}
      </div>
      <div className="production-transport">
        <span className="mono">{localTime.toFixed(3)} s</span>
        <input aria-label="ProductStage playhead" type="range" min={0}
          max={scene?seconds(scene.duration):6} step={1/30} value={localTime} disabled={!scene}
          onChange={e=>onSeek(seconds(scene!.start)+Number(e.target.value))}/>
      </div>
      <div className="production-evidence-note">
        <strong>{aspect.width} × {aspect.height} · original semantic composition</strong>
        <p>Editorial preview is not native renderer evidence. Imported app captures,
          3D camera rigs, media rights verification and human creative approval
          require separate admission and testing; no fake screenshot is substituted.</p>
      </div>
    </section>
  </div>;
}
