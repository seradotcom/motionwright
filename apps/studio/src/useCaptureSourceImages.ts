import {useEffect,useRef,useState} from "react";
import type {Project} from "./types";
import {registeredCapturePng} from "./api";
import {captureBoardNodeId} from "./captureBoard";
import {capturePngRequest,type CapturePngRequest} from "./captureSourcePreview";

export type CaptureSourceImages=Readonly<Record<string,{url:string;sha256:string}>>;
type PreviewState={key:string;images:CaptureSourceImages;status:"loading"|"ready"|"unavailable";error:string|null};
const EMPTY:CaptureSourceImages=Object.freeze({});

function decodeOriginal(url:string,signal:AbortSignal):Promise<void>{
  return new Promise((resolve,reject)=>{
    const image=new Image();
    let settled=false;
    const finish=(error?:Error)=>{
      if(settled)return;
      settled=true;clearTimeout(timer);signal.removeEventListener("abort",cancel);
      image.onload=null;image.onerror=null;image.removeAttribute("src");
      if(error)reject(error);else resolve();
    };
    const cancel=()=>finish(new Error("Original capture read was superseded."));
    const timer=setTimeout(()=>finish(new Error("Original capture image decoding timed out.")),10_000);
    image.onload=()=>finish(image.naturalWidth===1440&&image.naturalHeight===940?undefined:
      new Error("Original capture decoded to an unexpected viewport."));
    image.onerror=()=>finish(new Error("The original PNG could not be decoded."));
    signal.addEventListener("abort",cancel,{once:true});
    if(signal.aborted)cancel();else image.src=url;
  });
}

/** Two bounded owner reads per changed source/stamp. Label, timing and aspect edits
 * reuse the same originals. A changed scope hides old images before effects run. */
export function useCaptureSourceImages(project:Project,boardId:string,captureIds:readonly string[]){
  const [retryVersion,setRetryVersion]=useState(0),[state,setState]=useState<PreviewState|null>(null);
  let binding:string|null=null,issue:string|null=null;
  if(captureIds.length===2&&captureIds.every(Boolean)&&captureIds[0]!==captureIds[1]){
    try{
      binding=JSON.stringify({boardId,requests:captureIds.map(id=>capturePngRequest(project,id))});
    }catch(error){issue=error instanceof Error?error.message:"Registered source selection is unavailable.";}
  }
  const key=(binding??"unselected")+":"+retryVersion;
  const currentKey=useRef(key);currentKey.current=key;
  useEffect(()=>{
    if(!binding)return;
    const {boardId:selectedBoard,requests}=JSON.parse(binding) as {boardId:string;requests:CapturePngRequest[]};
    const controller=new AbortController(),urls=new Set<string>();
    const current=()=>!controller.signal.aborted&&currentKey.current===key;
    const release=()=>{for(const url of urls)URL.revokeObjectURL(url);urls.clear();};
    setState({key,images:EMPTY,status:"loading",error:null});
    void (async()=>{
      try{
        const originals=await Promise.all(requests.map(request=>registeredCapturePng(request)));
        if(!current())return;
        const images:Record<string,{url:string;sha256:string}>={};
        await Promise.all(originals.map(async(bytes,index)=>{
          const id=await captureBoardNodeId(selectedBoard,"image-"+(index+1));
          if(!current())return;
          const url=URL.createObjectURL(new Blob([bytes],{type:"image/png"}));urls.add(url);
          await decodeOriginal(url,controller.signal);
          if(current())images[id]={url,sha256:requests[index].screenshot_sha256};
        }));
        if(current())setState({key,images,status:"ready",error:null});else release();
      }catch(error){
        // Abort every sibling decoder before releasing this attempt's URLs.
        const stillCurrent=current();controller.abort();release();
        if(stillCurrent)setState({key,images:EMPTY,status:"unavailable",
          error:error instanceof Error?error.message:"Original capture preview is unavailable."});
      }
    })();
    return()=>{controller.abort();release();};
  },[binding,key]);
  const visible=binding&&state?.key===key?state:null;
  return {images:visible?.images??EMPTY,status:issue?"unavailable":binding?(visible?.status??"loading"):"unselected",
    error:issue??visible?.error??null,retry:()=>setRetryVersion(value=>value+1)};
}
