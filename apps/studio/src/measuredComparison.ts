import type {CanvasNode,CanvasTransform,Project,Scene} from "./types";
import {sameValue,type ScopedCanvasEdit} from "./creativeProduction";
import {validateMeasuredData,type MeasuredBinding,type MeasuredDatum,type MeasuredTable} from "./measuredData";
import {deliveryTextSourceConflict} from "./deliveryTextSources";

export const MEASURED_COMPARISON_ID="motionwright.data.state-comparison";
export interface MeasuredLayoutInput {left:number;top:number;rowSpacing:number;labelGap:number;barHeight:number}
export interface MeasuredComparisonInput extends MeasuredLayoutInput {beforeId:string;afterId:string}
type TransformEdit=Extract<ScopedCanvasEdit,{kind:"transform"}>;
export interface ComparisonRow {label:MeasuredBinding;bar:MeasuredBinding;datum:MeasuredDatum;table:MeasuredTable}
export interface MeasuredLayoutResult {edits:TransformEdit[];rationale:string;unit:MeasuredDatum["unit"];axisMaxMilli:number;sourceSha:string;tableVersion:number;rows:ComparisonRow[]}
export interface MeasuredComparisonResult extends MeasuredLayoutResult {rows:[ComparisonRow,ComparisonRow]}

/** Discover only unambiguous saved label/bar pairs, not unrelated shapes. */
export function measuredComparisonRows(project:Project,scene:Scene):ComparisonRow[]{
  const bindings=(project.production_design?.measured_bindings??[]).filter(item=>item.scene_id===scene.id);
  return bindings.filter(item=>item.view.kind==="label").flatMap(label=>{
    const matches=bindings.filter(item=>item.table_id===label.table_id&&item.datum_key===label.datum_key);
    const bars=matches.filter(item=>item.view.kind==="bar");
    const table=project.production_design?.measured_tables?.find(item=>item.id===label.table_id);
    const datum=table?.rows.find(item=>item.key===label.datum_key);
    if(bars.length!==1||matches.filter(item=>item.view.kind==="label").length!==1||!table||!datum)return [];
    return [{label,bar:bars[0],datum,table}];
  });
}
const transform=(node:CanvasNode,x=node.x,y=node.y,width=node.width,height=node.height):CanvasTransform=>({
  x,y,width,height,rotation_deg:node.rotation_deg,opacity:node.opacity
});
/** Two already bound measurements. Saved geometry is an ordinary reversible
 * patch; data bindings continue to own numeric source changes. */
export function measuredRowLayout(project:Project,scene:Scene,labelIds:readonly string[],input:MeasuredLayoutInput):MeasuredLayoutResult{
  if(!project.scenes.some(item=>sameValue(item,scene))||scene.renderer!=="motion-canvas")
    throw new Error("Choose a current Motion Canvas scene.");
  validateMeasuredData(project);
  if(labelIds.length<2||labelIds.length>4||new Set(labelIds).size!==labelIds.length)
    throw new Error("Choose two to four distinct measured states.");
  for(const [value,min,max] of [[input.left,0,1920],[input.top,0,1080],[input.rowSpacing,64,1080],[input.labelGap,0,160],[input.barHeight,8,160]])
    if(!Number.isSafeInteger(value)||value<min||value>max)throw new Error("Comparison layout requires bounded whole source pixels.");
  const available=measuredComparisonRows(project,scene);
  const selected=labelIds.map(id=>available.find(item=>item.label.id===id));
  if(selected.some(row=>!row))throw new Error("Each state requires one saved label and one saved bar for the same datum.");
  const rows=selected as ComparisonRow[],before=rows[0];
  if(rows.some(row=>row.table.id!==before.table.id||row.datum.unit!==before.datum.unit)||
    new Set(rows.map(row=>row.datum.key)).size!==rows.length||new Set(rows.map(row=>row.datum.label)).size!==rows.length)
    throw new Error("Choose distinct named rows from the same saved table with identical units.");
  const a=before.bar.view,l=before.label.view;
  if(a.kind!=="bar"||l.kind!=="label"||a.axis_min_milli!==0||rows.some(row=>
    row.bar.view.kind!=="bar"||!sameValue(a,row.bar.view)||row.label.view.kind!=="label"||l.decimals!==row.label.view.decimals))
    throw new Error("All bars must share the same explicit zero-based axis and maximum width; labels must share precision.");
  for(const row of rows){
    if(row.datum.milli<=0||row.datum.milli>a.axis_max_milli||
      row.datum.milli % 10**(3-l.decimals)!==0 ||
      row.datum.milli*a.max_width_px*2<a.axis_max_milli)
      throw new Error("Comparison needs positive values exactly represented by label precision and at least one rounded pixel. No inferred scale or clamped zero bar.");
  }
  const targets=rows.flatMap(row=>[row.label.node_id,row.bar.node_id]);
  const ids=new Set(targets);
  if(ids.size!==targets.length)throw new Error("Comparison requires distinct original objects.");
  const managed=new Set([
    ...(project.production_design?.heroes??[]).flatMap(item=>item.baseline.map(node=>node.id)),
    ...(project.production_design?.stages??[]).flatMap(item=>item.baseline.map(node=>node.id)),
    ...(project.production_design?.procedural_fields??[]).flatMap(item=>item.baseline.map(node=>node.id))
  ]);
  if(project.locks.some(lock=>["project:"+project.id,"scene:"+scene.id].includes(lock.resource)&&["content","style","position","timing","renderer"].includes(lock.kind)))
    throw new Error("The original scene is locked.");
  const nodes=targets.map(id=>scene.nodes.find(node=>node.id===id));
  if(nodes.some(node=>!node||managed.has(node.id)||node.parent_id!==null||node.coordinate_space!=="project_pixels"||
    node.rotation_deg!==0||node.opacity!==1||node.style.blend_mode!=="normal"||node.keyframes.length||node.relations.length||
    node.property_locks.some(lock=>lock==="position"||lock==="size")||node.width<=0||node.height<=0)||
    scene.nodes.some(node=>(node.parent_id!==null&&ids.has(node.parent_id))||node.relations.some(rel=>ids.has(rel.target_id))))
    throw new Error("Use independent, static, unlocked measured objects, without managed components, parent transforms or relation writers.");
  const admitted=nodes as CanvasNode[],labels=admitted.filter((_,i)=>i%2===0),bars=admitted.filter((_,i)=>i%2===1);
  const first=labels[0];
  if(bars.some(node=>node.kind!=="rectangle"||node.style.stroke_width!==0||!node.style.fill)||
    labels.some(node=>node.kind!=="text"||node.text===null||!node.style.font_size||node.text.includes("\n")))
    throw new Error("Comparison needs filled, unstroked bars and explicit single-line source labels.");
  if(labels.some(node=>node.style.font_size!==first.style.font_size||node.style.font_family!==first.style.font_family||
    node.style.font_weight!==first.style.font_weight))
    throw new Error("Use the same type scale and weight for all measured states.");
  const labelWidth=Math.max(a.max_width_px,...labels.map(node=>node.width)),labelHeight=Math.max(...labels.map(node=>node.height));
  for(const label of labels){
    const size=label.style.font_size!;
    if(Array.from(label.text!).length*size*.56>labelWidth*.96||size*1.4>labelHeight)
      throw new Error("A source label exceeds its nominal text box. Adjust the authored typography before comparison.");
  }
  if(input.rowSpacing<labelHeight+input.labelGap+input.barHeight+16)
    throw new Error("The states overlap. Increase row spacing to leave a distinct gap.");
  if(scene.camera.center_x!==960||scene.camera.center_y!==540||scene.camera.zoom!==1||scene.camera.rotation_deg!==0||
    project.production_design?.camera_reframes?.some(item=>item.scene_id===scene.id))
    throw new Error("Comparison requires the neutral source camera; review other framing separately.");
  const profiles=project.deliverables.filter(profile=>!profile.included_scene_ids.length||profile.included_scene_ids.includes(scene.id));
  if(!profiles.length)throw new Error("The measured scene must belong to a saved delivery profile.");
  const poses=labels.flatMap((label,i)=>[
    transform(label,input.left,input.top+i*input.rowSpacing,labelWidth,labelHeight),
    transform(bars[i],input.left,input.top+i*input.rowSpacing+labelHeight+input.labelGap,bars[i].width,input.barHeight),
  ]);
  // Reserve full axis, not today's bar widths. The standard three aspect
  // families retain a common horizontal origin when x scale = object scale.
  const envelopes=poses.map((pose,i)=>({...pose,width:i%2===1?a.max_width_px:pose.width}));
  const margin=scene.camera.safe_margin;
  if(!Number.isFinite(margin)||margin<0||margin>=.5)throw new Error("Invalid source safe area.");
  function fits(width:number,height:number){
    const sx=width/1920,sy=height/1080,scale=Math.min(sx,sy);
    return width>=320&&height>=320&&width<=7680&&height<=7680&&sx===scale&&envelopes.every(pose=>{
      const x=(pose.x+pose.width/2)*sx-pose.width*scale/2,y=(pose.y+pose.height/2)*sy-pose.height*scale/2;
      return x>=width*margin&&y>=height*margin&&x+pose.width*scale<=width*(1-margin)&&y+pose.height*scale<=height*(1-margin);
    });
  }
  if(!fits(1920,1080))throw new Error("Comparison exceeds the authored safe area at its full axis width.");
  for(const profile of profiles){
    const conflict=deliveryTextSourceConflict(project,profile,scene);
    if(conflict)throw new Error(conflict);
    if(profile.framing_strategy!=="replan"||!fits(profile.width,profile.height))
      throw new Error("Output "+profile.name+" cannot retain this common zero origin and full axis inside its safe area.");
  }
  const edits=targets.map((node_id,i):TransformEdit=>({kind:"transform",node_id,transform:poses[i]}));
  return {edits,rows,unit:before.datum.unit,axisMaxMilli:a.axis_max_milli,sourceSha:before.table.source_sha256,tableVersion:before.table.version,
    rationale:before.table.label+" v"+before.table.version+" ("+before.table.source_sha256+
      "), "+rows.map(row=>row.datum.key).join(" to ")+", common zero-based "+before.datum.unit+" axis 0.."+a.axis_max_milli+" milli; retain measured bindings."};
}
export function measuredComparisonEdits(project:Project,scene:Scene,input:MeasuredComparisonInput):MeasuredComparisonResult{
  const result=measuredRowLayout(project,scene,[input.beforeId,input.afterId],input);
  if(result.edits.every(edit=>sameValue(edit.transform,transform(scene.nodes.find(node=>node.id===edit.node_id)!))))
    throw new Error("The original states already use this comparison layout.");
  return {...result,rows:result.rows as [ComparisonRow,ComparisonRow],rationale:MEASURED_COMPARISON_ID+"/v1: "+result.rationale};
}
