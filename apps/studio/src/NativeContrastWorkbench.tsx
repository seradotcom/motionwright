import { useEffect,useMemo,useRef,useState,type MouseEvent } from "react";
import { ExternalLink, ScanEye, RefreshCw } from "lucide-react";
import { readNativePreviewFrame } from "./api";
import { planNativeInspection } from "./nativeInspection";
import {
  checkedNativePng,exactContrastPoint,measureNativeSourceContext,
  sourcePngSha256,type ContrastPoint,type PixelContrastMeasurement,
} from "./nativePixelContrast";
import type { DeliverableProfile,MotionCanvasRenderEvidence,Project,Scene } from "./types";
import { seconds } from "./types";
import "./native-contrast.css";

interface NativeOriginal {
  url:string;frameIndex:number;pngSha256:string;width:number;height:number;scope:string;
}
const valuePoint=(x:string,y:string):ContrastPoint|null=>{
  if(!x.trim()||!y.trim())return null;
  const first=Number(x),second=Number(y);
  if(!Number.isFinite(first)||!Number.isFinite(second)||
      first<0||first>100||second<0||second>100)return null;
  return {xPct:first,yPct:second};
};
const textError=(error:unknown)=>error instanceof Error?error.message:String(error);

export default function NativeContrastWorkbench({
  project,scene,displayProfile,evidence,busy,onOpenRender,
}:{
  project:Project;scene:Scene|null;displayProfile:DeliverableProfile|null;
  evidence:MotionCanvasRenderEvidence|null;busy:boolean;onOpenRender:()=>void;
}){
  const plan=useMemo(()=>planNativeInspection(project,scene,displayProfile,evidence),
    [project,scene,displayProfile,evidence]);
  const signature=plan?
    [plan.projectId,plan.generation,plan.revision,plan.sceneId,plan.profileId,plan.samples[0]?.token].join(":"):
    "not-authorized";
  const latest=useRef(signature);latest.current=signature;
  const epoch=useRef(0);
  const uri=useRef<string|null>(null);
  const bytes=useRef<{scope:string;frameIndex:number;sha:string;source:Uint8Array}|null>(null);
  const [selected,setSelected]=useState(0);
  const [original,setOriginal]=useState<NativeOriginal|null>(null);
  const [loading,setLoading]=useState(false);
  const [measuring,setMeasuring]=useState(false);
  const [target,setTarget]=useState<"foreground"|"background">("foreground");
  const [fgX,setFgX]=useState("");const [fgY,setFgY]=useState("");
  const [bgX,setBgX]=useState("");const [bgY,setBgY]=useState("");
  const [metric,setMetric]=useState<PixelContrastMeasurement|null>(null);
  const [error,setError]=useState<string|null>(null);
  const [status,setStatus]=useState<string|null>(null);
  const clearSource=()=>{
    if(uri.current){URL.revokeObjectURL(uri.current);uri.current=null;}
    bytes.current=null;setOriginal(null);setMetric(null);
    setFgX("");setFgY("");setBgX("");setBgY("");
  };
  useEffect(()=>{
    epoch.current++;
    if(uri.current)URL.revokeObjectURL(uri.current);
    uri.current=null;bytes.current=null;setOriginal(null);setMetric(null);
    setFgX("");setFgY("");setBgX("");setBgY("");
    setSelected(0);setTarget("foreground");setLoading(false);setMeasuring(false);
    setError(null);setStatus(null);
    return()=>{
      epoch.current++;
      if(uri.current)URL.revokeObjectURL(uri.current);
      uri.current=null;bytes.current=null;
    };
  },[signature]);
  const selectFrame=(next:number)=>{
    epoch.current++;clearSource();setError(null);setStatus(null);
    setSelected(next);setLoading(false);setMeasuring(false);
  };
  const read=async()=>{
    if(!plan || !plan.samples[selected] || busy || loading || measuring)return;
    const sample=plan.samples[selected];
    const sourceScope=signature;
    const token=++epoch.current;
    clearSource();setError(null);setStatus(null);setLoading(true);
    try{
      const returned=await readNativePreviewFrame(project,sample.token,sample.frameIndex);
      checkedNativePng(returned,plan.width,plan.height);
      const copy=new Uint8Array(returned.length);copy.set(returned);
      const sha=await sourcePngSha256(copy);
      if(!/^[a-f0-9]{64}$/.test(sha))throw new Error("Original source digest failed validation.");
      if(typeof createImageBitmap!=="function")throw new Error("Exact native PNG decode unavailable in this WebView.");
      const targetWidth=Math.min(plan.width,768);
      const targetHeight=Math.max(1,Math.round(plan.height*targetWidth/plan.width));
      const bitmap=await createImageBitmap(new Blob([copy.buffer],{type:"image/png"}),{
        resizeWidth:targetWidth,resizeHeight:targetHeight,resizeQuality:"high",
      });
      let thumbnail:Blob;
      try{
        const canvas=document.createElement("canvas");
        canvas.width=targetWidth;canvas.height=targetHeight;
        const context=canvas.getContext("2d");
        if(!context)throw new Error("Browser cannot draw the bounded native frame preview.");
        context.drawImage(bitmap,0,0,targetWidth,targetHeight);
        thumbnail=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(blob=>
          blob?resolve(blob):reject(new Error("Native preview thumbnail encoding failed.")),"image/png"));
      }finally{bitmap.close();}
      if(token!==epoch.current || latest.current!==sourceScope)return;
      const url=URL.createObjectURL(thumbnail);
      uri.current=url;
      bytes.current={scope:sourceScope,frameIndex:sample.frameIndex,sha,source:copy};
      setOriginal({url,frameIndex:sample.frameIndex,pngSha256:sha,
        width:plan.width,height:plan.height,scope:sourceScope});
      setStatus("Exact native source loaded. Choose one solid foreground color and one solid background color.");
    }catch(reason){
      if(token===epoch.current && latest.current===sourceScope)setError(textError(reason));
    }finally{
      if(token===epoch.current && latest.current===sourceScope)setLoading(false);
    }
  };
  const pointChange=(which:"foreground"|"background",coordinate:"x"|"y",value:string)=>{
    epoch.current++;setMetric(null);setError(null);
    if(which==="foreground"){if(coordinate==="x")setFgX(value);else setFgY(value);}
    else{if(coordinate==="x")setBgX(value);else setBgY(value);}
  };
  const pick=(event:MouseEvent<HTMLButtonElement>)=>{
    if(event.detail===0)return; // Keyboard users can enter exact percentages below.
    if(!original || original.scope!==signature || busy || measuring)return;
    const rectangle=event.currentTarget.getBoundingClientRect();
    if(rectangle.width<=0||rectangle.height<=0)return;
    const x=Math.max(0,Math.min(100,100*(event.clientX-rectangle.left)/rectangle.width));
    const y=Math.max(0,Math.min(100,100*(event.clientY-rectangle.top)/rectangle.height));
    pointChange(target,"x",x.toFixed(2));
    pointChange(target,"y",y.toFixed(2));
    if(target==="foreground")setTarget("background");
  };
  const first=valuePoint(fgX,fgY),second=valuePoint(bgX,bgY);
  const measure=async()=>{
    if(!plan || !first || !second || !original || !bytes.current ||
        original.scope!==signature || bytes.current.scope!==signature ||
        bytes.current.frameIndex!==original.frameIndex || measuring || busy)return;
    const active=++epoch.current;setMetric(null);setError(null);setMeasuring(true);setStatus(null);
    let bitmap:ImageBitmap|null=null;
    try{
      const source=bytes.current.source;
      checkedNativePng(source,plan.width,plan.height);
      const sha=await sourcePngSha256(source);
      if(sha!==bytes.current.sha||sha!==original.pngSha256)
        throw new Error("Native source changed after the preview was observed.");
      // A fresh sized copy owns an ArrayBuffer, never a SharedArrayBufferLike.
      const decodeBytes=new Uint8Array(source.length);decodeBytes.set(source);
      bitmap=await createImageBitmap(new Blob([decodeBytes.buffer],{type:"image/png"}));
      if(bitmap.width!==plan.width||bitmap.height!==plan.height)
        throw new Error("Decoded frame dimensions differ from the verified native output profile.");
      const canvas=document.createElement("canvas");
      canvas.width=bitmap.width;canvas.height=bitmap.height;
      const context=canvas.getContext("2d",{willReadFrequently:true});
      if(!context)throw new Error("Browser cannot inspect exact original-resolution frame colors.");
      context.drawImage(bitmap,0,0);
      const observed=measureNativeSourceContext(context,plan.width,plan.height,
        exactContrastPoint(first.xPct,first.yPct),exactContrastPoint(second.xPct,second.yPct));
      canvas.width=0;canvas.height=0;
      if(active!==epoch.current || latest.current!==signature)return;
      setMetric(observed);
      setStatus("Two opaque source-pixel neighborhoods measured. This is not a typography or creative-quality approval.");
    }catch(reason){
      if(active===epoch.current && latest.current===signature)setError(textError(reason));
    }finally{
      bitmap?.close();
      if(latest.current===signature)setMeasuring(false);
    }
  };
  const previewIsCurrent=!!(original&&original.scope===signature);
  const swatch=(rgb:[number,number,number])=>`rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
  return <section className="native-contrast" aria-label="Native source pixel contrast">
    <header className="production-section-title">
      <ScanEye size={19} aria-hidden="true"/>
      <div><h2>Native pixel contrast</h2><p>Two deliberate source-pixel samples · sRGB luminance, not an AI aesthetic verdict.</p></div>
    </header>
    <div className="native-contrast-notice">
      <strong>Original-resolution evidence only</strong>
      <span>Read a frame through the existing Semwright native grant. No screenshot,
        CSS composition, thumbnail pixel or external image substitutes for the source PNG.</span>
    </div>
    {!plan?<section className="production-empty">
      <h3>No current native frame grant</h3>
      <p>Choose a scene and deliverable, then render a native Motion Canvas preview in Deliver.
        Changed revisions or profiles require a new frame source.</p>
      <button className="secondary-button" type="button" onClick={onOpenRender}>
        <ExternalLink size={14}/> Open native Deliver
      </button>
    </section>:<>
      <div className="native-contrast-controls">
        <label className="production-field"><span>Exact source frame</span>
          <select aria-label="Pixel probe source frame" value={selected} disabled={busy||loading||measuring}
            onChange={event=>selectFrame(Number(event.target.value))}>
            {plan.samples.map((sample,index)=><option key={sample.frameIndex} value={index}>
              Frame {sample.frameIndex} · {seconds(sample.timelineTime).toFixed(3)} s
            </option>)}
          </select>
        </label>
        <button type="button" className="primary-button" onClick={()=>void read()}
          disabled={busy||loading||measuring}>
          <RefreshCw size={14}/>{loading?"Reading original pixels…":"Read exact native source frame"}
        </button>
        <span className="production-help">{plan.width} × {plan.height} · r{plan.revision} · source-bound to {plan.profileId.slice(0,8)}…</span>
      </div>
      {previewIsCurrent&&original&&<div className="native-contrast-workspace">
        <section className="native-contrast-preview" aria-label="Pixel sample viewer">
          <div className="native-contrast-source"><span>f{original.frameIndex} · original {original.width} × {original.height}</span>
            <span className="mono">PNG SHA-256 {original.pngSha256.slice(0,16)}…</span></div>
          <div className="native-contrast-picture">
            <button type="button" className="native-contrast-picker" onClick={pick}
              aria-label={`Choose ${target} pixel point on native source frame`} disabled={busy||measuring}>
              <img src={original.url} draggable={false} alt="Browser thumbnail of the hash-bound native source frame; manual sample markers appear above it."/>
              {first&&<span className="native-contrast-pin" style={{left:first.xPct+"%",top:first.yPct+"%"}}>F</span>}
              {second&&<span className="native-contrast-pin background" style={{left:second.xPct+"%",top:second.yPct+"%"}}>B</span>}
            </button>
          </div>
          <p className="production-help">The preview is resized for interaction. The measurement decodes the same SHA-bound PNG at its original resolution.</p>
        </section>
        <section className="native-contrast-settings" aria-label="Contrast probe coordinates">
          <h3>Choose two flat-color regions</h3>
          <p className="production-help">Select a foreground and background point on the image or enter percentages with the keyboard. Avoid borders, gradients and antialiased edges.</p>
          <fieldset><legend>Point to place</legend>
            <label><input type="radio" name="pixel-probe-target" checked={target==="foreground"}
              disabled={measuring||busy} onChange={()=>setTarget("foreground")}/> Foreground</label>
            <label><input type="radio" name="pixel-probe-target" checked={target==="background"}
              disabled={measuring||busy} onChange={()=>setTarget("background")}/> Background</label>
          </fieldset>
          <div className="native-contrast-coordinate-grid">
            {([["foreground","Foreground",fgX,fgY],["background","Background",bgX,bgY]] as const).map(([which,label,x,y])=>
              <div className="native-contrast-coordinate" key={which}>
                <strong>{label}</strong><div>
                  <label><span>X %</span><input aria-label={label+" X percent"} type="number" min={0} max={100} step="0.01"
                    value={x} disabled={measuring||busy} onChange={event=>pointChange(which,"x",event.target.value)}/></label>
                  <label><span>Y %</span><input aria-label={label+" Y percent"} type="number" min={0} max={100} step="0.01"
                    value={y} disabled={measuring||busy} onChange={event=>pointChange(which,"y",event.target.value)}/></label>
                </div>
              </div>)}
          </div>
          <button type="button" className="primary-button" disabled={!first||!second||busy||measuring}
            onClick={()=>void measure()}>{measuring?"Sampling original pixels…":"Measure source-pixel contrast"}</button>
          {metric&&<section className="native-contrast-measurement" aria-label="Measured source-pixel contrast">
            <span className="production-help">Measured sRGB luminance contrast</span>
            <strong className="native-contrast-score">{metric.ratio.toFixed(2)}<span>:1</span></strong>
            <div className="native-contrast-swatches">
              <div><i style={{background:swatch(metric.foreground)}}/><span>Foreground {swatch(metric.foreground)}</span></div>
              <div><i style={{background:swatch(metric.background)}}/><span>Background {swatch(metric.background)}</span></div>
            </div>
            <dl><div><dt>4.5:1 reference</dt><dd>{metric.reference45?"At or above":"Below"}</dd></div>
              <div><dt>3:1 reference</dt><dd>{metric.reference30?"At or above":"Below"}</dd></div></dl>
            <p className="production-help">Reference thresholds are not WCAG compliance or verified text readability. This probe does not know font size, glyph boundaries, motion, visual perception or whether the sampled colors correspond to actual text.</p>
          </section>}
        </section>
      </div>}
    </>}
    {status&&<p role="status" className="production-help">{status}</p>}
    {error&&<p role="alert" className="production-error">{error}</p>}
  </section>;
}
