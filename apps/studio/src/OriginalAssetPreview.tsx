import {useCallback,useEffect,useRef,useState} from "react";
import {Image as ImageIcon,Play,X} from "lucide-react";
import type {Asset,Project} from "./types";
import {assetPreviewSupported,closeAssetPreview,openAssetPreview,type AssetPreviewDescriptor} from "./assetPreview";
import "./original-asset-preview.css";

export default function OriginalAssetPreviewButton({project,asset,disabled}:{project:Project;asset:Asset;disabled:boolean}){
  const [open,setOpen]=useState(false);
  if(!assetPreviewSupported(asset))return null;
  return <>
    <button type="button" className="plain-icon" aria-label={"Preview original "+asset.name}
      title="Preview the imported original" disabled={disabled} onClick={()=>setOpen(true)}>{asset.media_type.startsWith("image/")?<ImageIcon size={13}/>:<Play size={13}/>}</button>
    {open&&<OriginalAssetPreview project={project} asset={asset} onClose={()=>setOpen(false)}/>}
  </>;
}
function OriginalAssetPreview({project,asset,onClose}:{project:Project;asset:Asset;onClose:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null),heading=useRef<HTMLHeadingElement>(null);
  const media=useRef<HTMLMediaElement|null>(null);
  const attachMedia=useCallback((node:HTMLMediaElement|null)=>{media.current=node;},[]);
  const [attempt,setAttempt]=useState(0),[descriptor,setDescriptor]=useState<AssetPreviewDescriptor|null>(null);
  const [error,setError]=useState<string|null>(null),[ready,setReady]=useState(false),[duration,setDuration]=useState<number|null>(null);
  const [dimensions,setDimensions]=useState<{width:number;height:number}|null>(null);
  useEffect(()=>{
    const previous=document.activeElement;
    const element=dialog.current;
    if(element&&!element.open)element.showModal();
    heading.current?.focus();
    return()=>{element?.close();if(previous instanceof HTMLElement&&previous.isConnected)previous.focus();};
  },[]);
  useEffect(()=>{
    let current=true,owned:AssetPreviewDescriptor|null=null;
    setDescriptor(null);setError(null);setReady(false);setDuration(null);setDimensions(null);
    void openAssetPreview(project,asset).then(value=>{
      if(!current){void closeAssetPreview(value.stream_token).catch(()=>undefined);return;}
      owned=value;setDescriptor(value);
    },reason=>{if(current)setError(reason instanceof Error?reason.message:"Original media is unavailable.");});
    return()=>{
      current=false;
      if(owned)void closeAssetPreview(owned.stream_token).catch(()=>undefined);
    };
  },[project.id,project.generation,project.revision,asset.id,asset.content_sha256,attempt]);
  useEffect(()=>{
    const element=media.current,url=descriptor?.url;
    if(!element||!url)return;
    // Own setup and teardown together. React can replay ref attachment without
    // rewriting an unchanged JSX src attribute; a ref cleanup must not erase it.
    element.setAttribute("src",url);element.load();
    return()=>{element.pause();element.removeAttribute("src");element.load();};
  },[descriptor?.url]);
  const loaded=()=>{
    const value=media.current?.duration;
    if(value!==undefined&&Number.isFinite(value)&&value>0)setDuration(value);
  };
  const failed=()=>{setReady(false);setError("This original could not be decoded or its saved source changed. Reopen it after checking the file and available media codecs.");};
  return <dialog className="original-asset-preview" ref={dialog} aria-labelledby="original-asset-preview-title"
    onCancel={event=>{event.preventDefault();onClose();}}>
    <header><div><h2 id="original-asset-preview-title" ref={heading} tabIndex={-1}>Original imported media</h2>
      <p>{asset.name}</p></div>
      <button type="button" className="plain-icon" aria-label="Close original preview" onClick={onClose}><X size={18}/></button></header>
    <p>Review the original file saved with this project. This preview does not change the edit or create a rendered deliverable.</p>
    {asset.media_type.startsWith("image/")&&<p>Static PNG and JPEG previews support files up to 16 MiB, 8,192 pixels per side and 16,777,216 pixels in total.</p>}
    {descriptor&&(descriptor.media_type==="image/png"||descriptor.media_type==="image/jpeg"?
      <img key={descriptor.stream_token} src={descriptor.url} alt={"Original image "+asset.name}
        onLoad={event=>{const image=event.currentTarget;setDimensions({width:image.naturalWidth,height:image.naturalHeight});setReady(true);}}
        onError={failed}/>:
      descriptor.media_type==="video/mp4"?
      <video ref={attachMedia} aria-label={"Original video "+asset.name} controls playsInline preload="metadata"
        onLoadedMetadata={loaded} onCanPlay={()=>setReady(true)} onError={failed}/>:
      <audio ref={attachMedia} aria-label={"Original audio "+asset.name} controls preload="metadata"
        onLoadedMetadata={loaded} onCanPlay={()=>setReady(true)} onError={failed}/>)}
    {!descriptor&&!error&&<p role="status">Checking the saved original...</p>}
    {ready&&!error&&<p role="status">Original media ready{duration!==null?" · "+duration.toFixed(2)+" seconds":""}</p>}
    {ready&&!error&&dimensions&&<p>{dimensions.width} × {dimensions.height} pixels</p>}
    {error&&<div><p role="alert">{error}</p><button type="button" className="button" onClick={()=>setAttempt(value=>value+1)}>Reopen original media</button></div>}
    {descriptor&&<dl><dt>Saved source</dt><dd>Revision {descriptor.revision} · {(descriptor.size_bytes/1024/1024).toFixed(2)} MiB</dd>
      <dt>SHA-256</dt><dd className="original-asset-digest">{descriptor.sha256}</dd></dl>}
    <footer><button type="button" className="button button-primary" onClick={onClose}>Close preview</button></footer>
  </dialog>;
}
