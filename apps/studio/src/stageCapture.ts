/** MW05-E07: a bounded native first-party capture tile within an original,
 * still-editable ProductStage. Never a screenshot of customer software.
 * The screenshot lives in the existing immutable CAS, not HTML/UI code.
 */
import type {Change, Project} from "./types";
import {emptyProductionDesign} from "./creativeProduction";
import {validateCaptureRecord} from "./captureSource";
import {stageNodeId} from "./productStage";

export interface StageCaptureCard {
  stage_id:string;
  capture_id:string;
  version:number;
}

async function check(project:Project,card:StageCaptureCard):Promise<void>{
  if(!card || typeof card!=="object" || Array.isArray(card) ||
    Object.keys(card).sort().join("|")!==["capture_id","stage_id","version"].join("|") ||
    card.version!==1)
    throw new Error("Unsupported or extended ProductStage first-party source card.");
  const stage=project.production_design?.stages?.find(s=>s.id===card.stage_id);
  const source=project.production_design?.captures?.find(c=>c.id===card.capture_id);
  if(!stage || !source)
    throw new Error("Stage source tile requires an owned ProductStage and registered first-party capture.");
  validateCaptureRecord(project,source);
  if(source.source_project_id!==project.id ||
    source.declared_scope!=="first-party-synthetic-fixture-only" ||
    source.origin!=="http://127.0.0.1:1420" ||
    source.viewport_width!==1440 || source.viewport_height!==940 ||
    source.screenshot_sha256!==source.screenshot_repeat_sha256 ||
    !project.assets.some(a=>a.id===source.screenshot_asset_id &&
      a.media_type==="image/png" && a.content_sha256===source.screenshot_sha256))
    throw new Error("Stage source tile cannot attach external or unverified PNG material.");
  const scene=project.scenes.find(s=>s.id===stage.scene_id);
  const cardNodeId=await stageNodeId(stage.id,"card");
  if(!scene || scene.renderer!=="motion-canvas" ||
    !scene.nodes.some(node=>node.id===cardNodeId && node.kind==="rectangle" &&
      node.width>0 && node.height>0))
    throw new Error("Stage source tile needs an editable original Canvas card rectangle.");
}
export async function validateStageCaptureCards(project:Project):Promise<void>{
  const cards=project.production_design?.stage_capture_cards??[];
  if(cards.length>16)throw new Error("Stage source tile budget exceeded.");
  const stages=new Set<string>();
  for(const card of cards){
    if(stages.has(card.stage_id))throw new Error("Only one bound source tile per ProductStage.");
    stages.add(card.stage_id);
    await check(project,card);
  }
}
export type StageCaptureChange=Extract<Change,{type:"bind_stage_capture"|"detach_stage_capture"}>;
export async function applyStageCaptureChange(project:Project,change:StageCaptureChange):Promise<void>{
  const stages=project.production_design?.stages??[];
  const stage=stages.find(item=>item.id===change.stage_id);
  if(!stage)throw new Error("Original ProductStage must exist before a capture can be bound.");
  const locked=project.locks.some(lock=>
    (lock.resource==="project:"+project.id || lock.resource==="scene:"+stage.scene_id) &&
    (lock.kind==="content" || (change.type==="bind_stage_capture" &&
       ["position","style","renderer"].includes(lock.kind))));
  if(locked)throw new Error("Source tile change is locked by the owner.");
  project.production_design??=emptyProductionDesign();
  const cards=project.production_design.stage_capture_cards??[];
  if(change.type==="bind_stage_capture"){
    const proposed:StageCaptureCard={stage_id:change.stage_id,capture_id:change.capture_id,version:1};
    await check(project,proposed);
    const index=cards.findIndex(item=>item.stage_id===change.stage_id);
    if(index<0){
      if(cards.length>=16)throw new Error("Stage source tile capacity exceeded.");
      cards.push(proposed);
    }else{
      cards[index]=proposed;
    }
    project.production_design.stage_capture_cards=cards;
  }else{
    if(!cards.some(card=>card.stage_id===change.stage_id))
      throw new Error("ProductStage first-party source tile does not exist.");
    project.production_design.stage_capture_cards=cards.filter(card=>card.stage_id!==change.stage_id);
  }
  const scene=project.scenes.find(s=>s.id===stage.scene_id);
  if(scene)scene.status="draft";
}
