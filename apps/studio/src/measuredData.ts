/** Typed, source-bound measurement projections. Mirror of Rust measured_data.rs.
 * No invented data, implicit axes, live spreadsheet connections or verification
 * of imported source bytes is implied by this editorial model.
 */
import type { CanvasNode, Project, Scene } from "./types";
import { sameValue } from "./creativeProduction";
export type MeasuredUnit="count"|"percent"|"seconds"|"milliseconds"|"bytes";
export interface MeasuredDatum {key:string;label:string;milli:number;unit:MeasuredUnit;}
export interface MeasuredTable {
  schema:string;id:string;label:string;version:number;
  source_asset_id:string;source_sha256:string;rows:MeasuredDatum[];
}
export type MeasuredView=
  |{kind:"counter";decimals:number}
  |{kind:"label";decimals:number}
  |{kind:"bar";axis_min_milli:number;axis_max_milli:number;max_width_px:number};
export type MeasuredOutput={kind:"text";value:string}|{kind:"bar";width_px:number};
export interface MeasuredBinding {
  id:string;scene_id:string;node_id:string;table_id:string;
  datum_key:string;view:MeasuredView;baseline:MeasuredOutput;
}
export interface MeasuredDataImpact {
  table_id:string;version:number;changed_keys:string[];
  scene_ids:string[];deliverable_ids:string[];
}
const schema="motionwright.measured-table/1";
/** Exactly the narrow grammar admitted by Storage, before dataset metadata is persisted. */
export function parseMeasuredSource(text:string):MeasuredDatum[] {
  if(new TextEncoder().encode(text).length>64*1024)
    invalid("Measured source exceeds the 64 KiB verified-ingest budget.");
  const trimmed=text.trimStart();
  if(trimmed.startsWith("[")){
    let rows:unknown;
    try{rows=JSON.parse(text);}catch{invalid("Imported source is not strict JSON rows.");}
    if(!Array.isArray(rows))invalid("Measured JSON requires an array of rows.");
    return rows as MeasuredDatum[];
  }
  const lines=text.trimEnd().split(/\r?\n/u);
  if(lines.shift()!=="key,label,milli,unit")
    invalid("Measured CSV requires key,label,milli,unit header.");
  return lines.map(line=>{
    const cells=line.split(",");
    if(cells.length!==4 || !/^-?[0-9]+$/u.test(cells[2]))
      invalid("Measured CSV row is outside its strict four-column subset.");
    return {key:cells[0],label:cells[1],milli:Number(cells[2]),unit:cells[3] as MeasuredUnit};
  });
}
const units=["count","percent","seconds","milliseconds","bytes"] as const;
const bounded=1_000_000_000;
const keyPattern=/^[a-z][a-z0-9_]{0,47}$/;
const sha=/^[0-9a-f]{64}$/;
function invalid(message:string):never{throw new Error(message);}
export function validateMeasuredTable(table:MeasuredTable,project:Project):void{
  if(table.schema!==schema || !Number.isInteger(table.version) || table.version<1 ||
    !table.label.trim() || table.label.length>160 || /[\x00-\x1f\x7f]/.test(table.label) ||
    table.rows.length<1 || table.rows.length>64 || !sha.test(table.source_sha256))
    invalid("Measured source/schema/budget invalid.");
  const asset=project.assets.find(a=>a.id===table.source_asset_id);
  if(!asset || !["application/json","text/csv"].includes(asset.media_type) ||
    asset.content_sha256!==table.source_sha256)
    invalid("Measured table SHA-256 must match an imported JSON/CSV asset.");
  const keys=new Set<string>();
  for(const row of table.rows){
    if(!keyPattern.test(row.key) || keys.has(row.key) || !row.label.trim() ||
      row.label.length>120 || /[\x00-\x1f\x7f]/.test(row.label) ||
      !Number.isInteger(row.milli) || Math.abs(row.milli)>bounded ||
      !units.includes(row.unit))
      invalid("Invalid or duplicate measured datum.");
    keys.add(row.key);
  }
}
export function validateMeasuredView(view:MeasuredView):void{
  if(view.kind==="counter" || view.kind==="label"){
    if(!Number.isInteger(view.decimals) || view.decimals<0 || view.decimals>3)
      invalid("Invalid measured precision.");
  }else if(view.kind==="bar"){
    if(!Number.isInteger(view.axis_min_milli)||!Number.isInteger(view.axis_max_milli)||
       !Number.isInteger(view.max_width_px)||view.axis_min_milli < -bounded ||
       view.axis_max_milli > bounded || view.axis_min_milli>=view.axis_max_milli ||
       view.max_width_px<16 || view.max_width_px>1920)
      invalid("Invalid explicitly authored metric bar axis.");
  }else invalid("Unknown measured presentation.");
}
function formatMilli(value:number,decimals:number):string {
  const scale=10**(3-decimals);
  const rounded=Math.floor((Math.abs(value)+scale/2)/scale);
  const base=10**decimals;
  const sign=value<0 && rounded!==0?"-":"";
  if(decimals===0)return sign+String(rounded);
  return sign+Math.floor(rounded/base)+"."+String(rounded%base).padStart(decimals,"0");
}
export function measuredOutput(datum:MeasuredDatum,view:MeasuredView):MeasuredOutput{
  validateMeasuredView(view);
  if(!Number.isInteger(datum.milli)||Math.abs(datum.milli)>bounded ||
     !units.includes(datum.unit))invalid("Invalid measured value or unit.");
  if(view.kind==="counter" || view.kind==="label"){
    const suffix={count:"",percent:"%",seconds:" s",milliseconds:" ms",bytes:" B"}[datum.unit];
    const raw=formatMilli(datum.milli,view.decimals)+suffix;
    return {kind:"text",value:view.kind==="label"?datum.label+" · "+raw:raw};
  }
  if(datum.milli<=view.axis_min_milli || datum.milli>view.axis_max_milli)
    invalid("Measured bar data outside explicitly declared axis; cannot invent/clamp it.");
  const numerator=view.max_width_px*(datum.milli-view.axis_min_milli);
  const denominator=view.axis_max_milli-view.axis_min_milli;
  const width=Math.floor((numerator+Math.floor(denominator/2))/denominator);
  return {kind:"bar",width_px:Math.max(1,width)};
}
function lookup(project:Project,tableId:string,key:string){
  const table=project.production_design?.measured_tables?.find(x=>x.id===tableId);
  const datum=table?.rows.find(x=>x.key===key);
  if(!table || !datum)invalid("Unbound measured datum key or dataset.");
  return datum;
}
function nodeFor(project:Project,binding:MeasuredBinding){
  const scene=project.scenes.find(s=>s.id===binding.scene_id && s.renderer==="motion-canvas");
  const node=scene?.nodes.find(n=>n.id===binding.node_id);
  if(!scene || !node)invalid("Measured binding requires an existing Motion Canvas node.");
  return {scene,node};
}
function verifyBaseline(node:CanvasNode,output:MeasuredOutput):void{
  if(output.kind==="text" && (node.kind!=="text" || node.text!==output.value))
    invalid("Bound Canvas metric was edited; detach/rebind before replacing its data.");
  if(output.kind==="bar" && (node.kind!=="rectangle" || node.width!==output.width_px))
    invalid("Bound Canvas metric was edited; detach/rebind before replacing its data.");
}
function checkWritable(project:Project,binding:MeasuredBinding):void{
  const kind=binding.view.kind==="bar"?"position":"content";
  const property=binding.view.kind==="bar"?"size":"text";
  if(project.locks.some(lock=>lock.resource==="scene:"+binding.scene_id && lock.kind===kind))
    invalid("Measured scene is locked.");
  const {node}=nodeFor(project,binding);
  if(node.property_locks.includes(property))invalid("Measured Canvas node property is locked.");
}
function projectUnlocked(project:Project):void{
  if(project.locks.some(l=>l.resource==="project:"+project.id && l.kind==="content"))
    invalid("Measured project content is locked.");
}
function writeOutput(project:Project,binding:MeasuredBinding,output:MeasuredOutput):void{
  const {scene,node}=nodeFor(project,binding);
  if(output.kind==="text"){
    if(node.kind!=="text")invalid("Metric counter/label requires text node.");
    node.text=output.value;
  }else{
    if(node.kind!=="rectangle")invalid("Metric bar requires rectangle node.");
    node.width=output.width_px;
  }
  scene.status="draft";
}
export function validateMeasuredData(project:Project):void{
  const design=project.production_design;
  const tables=design?.measured_tables??[],bindings=design?.measured_bindings??[];
  if(tables.length>16 || bindings.length>64)invalid("Measured project collection exceeds budget.");
  const tableIds=new Set<string>();
  for(const table of tables){
    validateMeasuredTable(table,project);
    if(tableIds.has(table.id))invalid("Duplicate measured table identity.");
    tableIds.add(table.id);
  }
  const ids=new Set<string>(),targets=new Set<string>();
  for(const binding of bindings){
    const target=binding.scene_id+":"+binding.node_id;
    if(ids.has(binding.id)||targets.has(target))invalid("Duplicate measured target or binding.");
    ids.add(binding.id);targets.add(target);
    const value=lookup(project,binding.table_id,binding.datum_key);
    const computed=measuredOutput(value,binding.view);
    if(!sameValue(binding.baseline,computed))invalid("Stale measured source/presentation baseline.");
    verifyBaseline(nodeFor(project,binding).node,binding.baseline);
  }
  const impact=design?.last_measured_impact;
  if(impact && (!tableIds.has(impact.table_id)||impact.version<=0||
    impact.scene_ids.some(id=>!project.scenes.some(scene=>scene.id===id))||
    impact.changed_keys.length>64 || impact.scene_ids.length>64 || impact.deliverable_ids.length>64))
    invalid("Invalid measured dependency impact.");
}
export function applyMeasuredTable(project:Project,table:MeasuredTable):void{
  projectUnlocked(project);validateMeasuredTable(table,project);
  const design=project.production_design!;
  design.measured_tables??=[];design.measured_bindings??=[];
  const old=design.measured_tables.find(d=>d.id===table.id);
  if(old){
    if(table.version!==old.version+1 || table.source_sha256===old.source_sha256)
      invalid("Measured update requires new source hash and next version.");
  }else if(table.version!==1 || design.measured_tables.length>=16)
    invalid("New measured table requires version 1 and available capacity.");
  const affected=new Set<string>(),keys=new Set<string>();
  const updates:{binding:MeasuredBinding;output:MeasuredOutput}[]=[];
  for(const binding of design.measured_bindings.filter(b=>b.table_id===table.id)){
    checkWritable(project,binding);
    verifyBaseline(nodeFor(project,binding).node,binding.baseline);
    const datum=table.rows.find(row=>row.key===binding.datum_key);
    if(!datum)invalid("Bound datum cannot disappear on measured update.");
    const output=measuredOutput(datum,binding.view);
    if(!sameValue(output,binding.baseline)){
      keys.add(binding.datum_key);affected.add(binding.scene_id);
    }
    updates.push({binding,output});
  }
  for(const {binding,output} of updates){
    if(!sameValue(output,binding.baseline))writeOutput(project,binding,output);
    binding.baseline=output;
  }
  if(old)design.measured_tables[design.measured_tables.findIndex(d=>d.id===table.id)]=structuredClone(table);
  else design.measured_tables.push(structuredClone(table));
  design.last_measured_impact={
    table_id:table.id,version:table.version,
    changed_keys:[...keys].sort(),scene_ids:[...affected].sort(),
    deliverable_ids:project.deliverables
      .filter(profile=>affected.size>0 && (profile.included_scene_ids.length===0 ||
        profile.included_scene_ids.some(id=>affected.has(id))))
      .map(profile=>profile.id),
  };
}
export type BindMeasuredInput={
  binding_id:string;scene_id:string;node_id:string;table_id:string;
  datum_key:string;view:MeasuredView;
};
export function applyMeasuredBinding(project:Project,input:BindMeasuredInput):void{
  projectUnlocked(project);validateMeasuredView(input.view);
  const design=project.production_design!;
  design.measured_bindings??=[];
  const pos=design.measured_bindings.findIndex(b=>b.id===input.binding_id);
  if(pos<0 && (design.measured_bindings.length>=64 ||
    design.measured_bindings.some(b=>b.scene_id===input.scene_id && b.node_id===input.node_id)))
    invalid("Measured binding limit or duplicate target.");
  if(pos>=0){
    const prev=design.measured_bindings[pos];
    if(prev.scene_id!==input.scene_id||prev.node_id!==input.node_id||prev.table_id!==input.table_id)
      invalid("Bound metric identity cannot move node/source.");
    checkWritable(project,prev);
    verifyBaseline(nodeFor(project,prev).node,prev.baseline);
  }
  const value=lookup(project,input.table_id,input.datum_key);
  const computed=measuredOutput(value,input.view);
  const binding:MeasuredBinding={
    id:input.binding_id,scene_id:input.scene_id,node_id:input.node_id,
    table_id:input.table_id,datum_key:input.datum_key,view:structuredClone(input.view),
    baseline:computed,
  };
  checkWritable(project,binding);
  writeOutput(project,binding,computed);
  if(pos>=0)design.measured_bindings[pos]=binding;
  else design.measured_bindings.push(binding);
}
export function detachMeasuredBinding(project:Project,id:string):void{
  projectUnlocked(project);
  const design=project.production_design!;
  const binding=design.measured_bindings?.find(b=>b.id===id);
  if(!binding)invalid("Measured binding does not exist.");
  if(project.locks.some(l=>l.resource==="scene:"+binding.scene_id && l.kind==="content"))
    invalid("Measured scene content is locked.");
  design.measured_bindings=design.measured_bindings!.filter(b=>b.id!==id);
}
