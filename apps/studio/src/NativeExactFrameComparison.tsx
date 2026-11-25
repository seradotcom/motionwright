import {useEffect,useRef,useState} from "react";
import {readNativePreviewFrame} from "./api";
import {comparisonPair,compareRgbaBand,emptyPixelDelta,exactComparisonSize,verifyExactComparisonPng,type PixelDelta} from "./nativeExactComparison";
import type {NativeInspectionPlan,NativeSampleEvidence} from "./nativeInspection";
import type {Project} from "./types";

type Result={firstUrl:string;secondUrl:string;diffUrl:string;stats:PixelDelta};
function imageBlob(bytes:Uint8Array):Blob {
  return new Blob([new Uint8Array(bytes).buffer],{type:"image/png"});
}
function pngBlob(canvas:HTMLCanvasElement):Promise<Blob>{
  return new Promise((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error("Cannot encode comparison map.")),"image/png"));
}
function canvas(width:number,height:number){
  const element=document.createElement("canvas");element.width=width;element.height=height;
  const context=element.getContext("2d",{willReadFrequently:true,colorSpace:"srgb"});
  if(!context)throw new Error("Full-resolution sRGB inspection is unavailable in this WebView.");
  return {element,context};
}

/** Key this view by the selected current source pair. Results and Blob URLs
 * are discarded on source/pair changes. Only owner-initiated reads are used. */
export default function NativeExactFrameComparison({project,plan,first,second,busy}:{
  project:Project;plan:NativeInspectionPlan;first:NativeSampleEvidence;second:NativeSampleEvidence;busy:boolean;
}){
  const [result,setResult]=useState<Result|null>(null);
  const [mode,setMode]=useState<"difference"|"first"|"second"|"onion">("difference");
  const [loading,setLoading]=useState(false),[error,setError]=useState<string|null>(null);
  const live=useRef(false),reading=useRef(false),urls=useRef<string[]>([]);
  useEffect(()=>{
    live.current=true;
    return()=>{live.current=false;urls.current.forEach(URL.revokeObjectURL);urls.current=[];};
  },[]);
  const available=first.frameIndex!==second.frameIndex&&exactComparisonSize(plan.width,plan.height);
  const run=async()=>{
    if(!available||busy||reading.current||!live.current)return;
    reading.current=true;setLoading(true);setError(null);setResult(null);
    urls.current.forEach(URL.revokeObjectURL);urls.current=[];
    let a:ImageBitmap|null=null,b:ImageBitmap|null=null;
    const owned:HTMLCanvasElement[]=[];
    try{
      if(project.id!==plan.projectId||project.generation!==plan.generation||project.revision!==plan.revision)
        throw new Error("The inspected native revision is no longer current.");
      const [left,right]=comparisonPair(plan,first,second);
      if(typeof createImageBitmap!=="function")throw new Error("This WebView cannot decode original comparison frames.");
      const firstBytes=await verifyExactComparisonPng(await readNativePreviewFrame(project,left.token,left.frameIndex),first);
      if(!live.current)return;
      const firstBlob=imageBlob(firstBytes);
      a=await createImageBitmap(firstBlob);
      if(!live.current)return;
      const secondBytes=await verifyExactComparisonPng(await readNativePreviewFrame(project,right.token,right.frameIndex),second);
      if(!live.current)return;
      const secondBlob=imageBlob(secondBytes);
      b=await createImageBitmap(secondBlob);
      if(!live.current)return;
      const {width,height}=plan;
      if(a.width!==width||a.height!==height||b.width!==width||b.height!==height)
        throw new Error("Decoded native frames do not have the exact inspected dimensions.");
      const rows=Math.min(128,height);
      const leftBand=canvas(width,rows),rightBand=canvas(width,rows),difference=canvas(width,height);
      owned.push(leftBand.element,rightBand.element,difference.element);
      const stats=emptyPixelDelta();
      for(let y=0;y<height;y+=rows){
        if(!live.current)return;
        const count=Math.min(rows,height-y);
        leftBand.context.clearRect(0,0,width,rows);rightBand.context.clearRect(0,0,width,rows);
        leftBand.context.drawImage(a,0,y,width,count,0,0,width,count);
        rightBand.context.drawImage(b,0,y,width,count,0,0,width,count);
        const ap=leftBand.context.getImageData(0,0,width,count).data;
        const bp=rightBand.context.getImageData(0,0,width,count).data;
        const out=difference.context.createImageData(width,count);
        compareRgbaBand(ap,bp,out.data,width,y,stats);
        difference.context.putImageData(out,0,y);
        // Yield between bounded pixel bands so source changes can cancel work.
        if(y+rows<height)await new Promise<void>(resolve=>setTimeout(resolve,0));
      }
      const diff=await pngBlob(difference.element);
      if(!live.current)return;
      const admit=(blob:Blob)=>{const url=URL.createObjectURL(blob);urls.current.push(url);return url;};
      setResult({firstUrl:admit(firstBlob),secondUrl:admit(secondBlob),diffUrl:admit(diff),stats});
    }catch(reason){if(live.current)setError(reason instanceof Error?reason.message:String(reason));}
    finally{a?.close();b?.close();for(const element of owned){element.width=0;element.height=0;}
      reading.current=false;if(live.current)setLoading(false);}
  };
  return <section className="native-exact-comparison" aria-label="Full-resolution native frame comparison">
    <h3>Full-resolution comparison</h3>
    <p className="production-help">Read the two selected original PNGs again and match their inspected SHA-256 hashes. Compare every browser-decoded sRGB RGBA pixel without resizing. Transparent RGB and color conversion follow the WebView; this is not raw PNG equivalence or a fidelity verdict.</p>
    <button type="button" className="secondary-button" disabled={!available||loading||busy} onClick={()=>void run()}>
      {loading?"Reading and comparing original pixels...":"Compare original frame pixels"}
    </button>
    {!available&&<p role="status">Select distinct frames at a resolution of at most 8,388,608 pixels (8192 per side).</p>}
    {error&&<p role="alert">{error}</p>}
    {result&&<>
      <div className="production-button-row">
        <label className="production-field"><span>Full-resolution view</span><select value={mode} onChange={event=>setMode(event.target.value as typeof mode)}>
          <option value="difference">Absolute RGBA difference</option>
          <option value="first">First original PNG</option>
          <option value="second">Second original PNG</option>
          <option value="onion">50% original onion overlay</option>
        </select></label>
      </div>
      <div className="native-exact-image" style={{aspectRatio:plan.width+"/"+plan.height}}>
        <img src={mode==="difference"?result.diffUrl:mode==="second"?result.secondUrl:result.firstUrl}
          alt={mode==="difference"?"Full-resolution RGBA delta map":mode==="second"?"Second original native PNG":"First original native PNG"}/>
        {mode==="onion"&&<img className="native-exact-overlay" src={result.secondUrl} alt="Second original native PNG at 50% opacity"/>}
      </div>
      <dl className="native-exact-metrics" aria-label="Decoded pixel comparison measurements">
        <dt>Resolution</dt><dd>{plan.width} x {plan.height}</dd>
        <dt>Changed decoded pixels</dt><dd>{result.stats.changedPixels} / {result.stats.pixels}</dd>
        <dt>Maximum channel delta</dt><dd>{result.stats.maxChannelDelta} / 255</dd>
        <dt>Mean absolute channel delta</dt><dd>{(result.stats.totalAbsoluteChannelDelta/(result.stats.pixels*4)).toFixed(4)} / 255</dd>
        <dt>Changed bounds (exclusive end)</dt><dd>{result.stats.bounds?JSON.stringify(result.stats.bounds):"None in decoded pixels"}</dd>
      </dl>
      <p className="production-help">Source r{plan.revision}, frames {first.frameIndex} and {second.frameIndex}. Black is zero decoded channel difference; white is 255. Neither image nor this diagnostic authorizes a new render or a creative approval.</p>
      <details><summary>Exact source PNG identities</summary><p className="mono">Frame {first.frameIndex}: {first.pngSha256}</p><p className="mono">Frame {second.frameIndex}: {second.pngSha256}</p></details>
    </>}
  </section>;
}
