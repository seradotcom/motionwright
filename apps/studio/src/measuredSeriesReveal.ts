import type {CanvasKeyframe,DeliverableProfile,Project,Scene} from "./types";
import {sameValue,type ScopedCanvasEdit} from "./creativeProduction";
import {exactCanvasOpacitySpans} from "./canvasOpacity";
import {recipeFrameTime} from "./focusTransfer";
import {measuredComparisonRows,measuredRowLayout,type MeasuredLayoutInput,type MeasuredLayoutResult} from "./measuredComparison";

export const MEASURED_SERIES_REVEAL_ID="motionwright.data.series-reveal";
export interface MeasuredSeriesRevealInput extends MeasuredLayoutInput{
  tableId:string;startFrame:number;stepFrames:number;fadeFrames:number;curve:"linear"|"ease_out_cubic";
}
type SeriesEdit=Extract<ScopedCanvasEdit,{kind:"transform"|"motion"}>;
export interface MeasuredSeriesRevealResult extends Omit<MeasuredLayoutResult,"edits">{
  edits:SeriesEdit[];dates:string[];intervals:{date:string;startFrame:number;endFrame:number}[];
}
export function isMeasurementDate(label:string):boolean{
  if(!/^\d{4}-\d{2}-\d{2}$/.test(label))return false;
  const [year,month,day]=label.split("-").map(Number);
  if(year<1||month<1||month>12||day<1)return false;
  const leap=year%4===0&&(year%100!==0||year%400===0);
  return day<=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31][month-1];
}

/** Every source row is shown, with its literal ISO calendar date and unit.
 * Sequence timing is an editorial attention schedule, never elapsed time in
 * the dataset. Only opacity changes during reveal; numeric geometry is static. */
export function measuredSeriesRevealEdits(project:Project,scene:Scene,profile:DeliverableProfile,
  input:MeasuredSeriesRevealInput):MeasuredSeriesRevealResult{
  if(!project.deliverables.some(item=>sameValue(item,profile))||
    (profile.included_scene_ids.length&&!profile.included_scene_ids.includes(scene.id)))
    throw new Error("Choose a current saved output profile containing the measured scene.");
  const table=project.production_design?.measured_tables?.find(item=>item.id===input.tableId);
  if(!table||table.rows.length<2||table.rows.length>4)
    throw new Error("Series reveal requires the entire saved table, with two to four dated measurements.");
  if(table.rows.some(row=>!isMeasurementDate(row.label))||new Set(table.rows.map(row=>row.label)).size!==table.rows.length)
    throw new Error("Every source row needs a distinct valid ISO calendar date (YYYY-MM-DD); dates are never inferred.");
  const ordered=[...table.rows].sort((a,b)=>a.label<b.label?-1:a.label>b.label?1:0);
  const available=measuredComparisonRows(project,scene);
  const labelIds=ordered.map(datum=>available.find(row=>row.table.id===table.id&&row.datum.key===datum.key)?.label.id);
  if(labelIds.some(id=>!id))throw new Error("Bind exactly one original label and bar for every dated source row.");
  const layout=measuredRowLayout(project,scene,labelIds as string[],input);
  if(![input.startFrame,input.stepFrames,input.fadeFrames].every(Number.isSafeInteger)||input.startFrame<0||
    input.fadeFrames<1||input.stepFrames<input.fadeFrames||
    !["linear","ease_out_cubic"].includes(input.curve)||
    input.startFrame+(ordered.length-1)*input.stepFrames+input.fadeFrames>36000)
    throw new Error("Choose non-overlapping, positive, bounded reveal intervals on the saved frame clock.");
  const affected=project.deliverables.filter(item=>!item.included_scene_ids.length||item.included_scene_ids.includes(scene.id));
  const intervals=ordered.map((row,i)=>({date:row.label,startFrame:input.startFrame+i*input.stepFrames,
    endFrame:input.startFrame+i*input.stepFrames+input.fadeFrames}));
  const motions:SeriesEdit[]=[];
  for(const [i,row] of layout.rows.entries()){
    const interval=intervals[i],start=recipeFrameTime(interval.startFrame,profile.frame_rate),end=recipeFrameTime(interval.endFrame,profile.frame_rate);
    for(const binding of [row.label,row.bar]){
      const node=scene.nodes.find(item=>item.id===binding.node_id)!;
      if(node.property_locks.includes("opacity"))throw new Error("A measured series object's opacity is locked.");
      const keyframes:CanvasKeyframe[]=[{at:{num:"0",den:"1"},property:"opacity",value:0,interpolation:"linear"}];
      if(interval.startFrame>0)keyframes.push({at:start,property:"opacity",value:0,interpolation:"hold"});
      keyframes.push({at:end,property:"opacity",value:1,interpolation:input.curve});
      for(const output of affected){
        try{
          const spans=exactCanvasOpacitySpans({...node,keyframes},scene,output.frame_rate);
          if(spans?.length!==1||!sameValue(spans[0].start,start)||!sameValue(spans[0].end,end))throw new Error("Unexpected span");
        }catch{throw new Error("Series reveal is not frame-aligned strictly inside output "+output.name+". Choose a shared interval.");}
      }
      motions.push({kind:"motion",node_id:node.id,keyframes});
    }
  }
  return {...layout,edits:[...layout.edits,...motions],dates:ordered.map(row=>row.label),intervals,
    rationale:MEASURED_SERIES_REVEAL_ID+"/v1: "+layout.rationale+" Chronological observations "+ordered.map(row=>row.label).join(", ")+
      ". Editorial visibility (not a continuous time axis) "+intervals.map(i=>i.startFrame+"-"+i.endFrame).join(", ")+" frames at "+
      profile.frame_rate.num+"/"+profile.frame_rate.den+", "+input.curve+"."};
}
