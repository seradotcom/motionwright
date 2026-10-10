import {useEffect,useMemo,useRef,useState} from 'react';
import {Eye,Layers,RefreshCw} from 'lucide-react';
import type {Project} from '../types';
import {readNativePreviewFrame} from '../api';
import type {HyperframesRenderEvidence} from './nativeTypes';
import './native-editor.css';

type NativeSample={index:number;url:string;sha:string};
type Mode='onion'|'difference';
export const samplesFor=(frames:number)=>[0,Math.floor((frames-1)/4),Math.floor((frames-1)/2),Math.floor((frames-1)*3/4),frames-1]
  .filter((value,index,values)=>values.indexOf(value)===index);
export function exactGrant(evidence:HyperframesRenderEvidence):string|null {
  const eligible=evidence.preview.filter(grant=>grant.segment_id===evidence.document_id &&
    grant.scene_ids.length===1&&grant.scene_ids[0]===evidence.scene_id &&
    grant.frame_count===evidence.frame_count);
  return eligible.length===1?eligible[0].token:null;
}
export default function NativeHtmlContactReview({project,evidence,onSeek}:{project:Project;evidence:HyperframesRenderEvidence;onSeek:(seconds:number)=>void}){
  const token=exactGrant(evidence);
  const current=project.id===evidence.project_id && project.generation===evidence.generation && project.revision===evidence.revision;
  const indices=useMemo(()=>samplesFor(evidence.frame_count),[evidence.frame_count]);
  const [content,setContent]=useState<NativeSample[]>([]),[status,setStatus]=useState<'idle'|'loading'|'ready'|'error'>('idle'),[failure,setFailure]=useState<string|null>(null);
  const [left,setLeft]=useState(0),[right,setRight]=useState(1),[mode,setMode]=useState<Mode>('onion');
  const canvas=useRef<HTMLCanvasElement|null>(null);
  useEffect(()=>{
    let active=true;const urls:string[]=[];setContent([]);setFailure(null);
    if(!token||!current){setStatus('idle');return;}
    setStatus('loading');
    (async()=>{
      const next:NativeSample[]=[];
      for(const index of indices){
        const png=await readNativePreviewFrame(project,token,index);
        if(!active)return;
        if(png.byteLength>8*1024*1024)throw new Error('Native review thumbnail exceeds its 8 MiB preview budget. Inspect the full artifact separately.');
        const url=URL.createObjectURL(new Blob([Uint8Array.from(png).buffer],{type:'image/png'}));
        urls.push(url);next.push({index,url,sha:evidence.source_sha256});
      }
      if(active){setContent(next);setLeft(0);setRight(Math.min(1,next.length-1));setStatus('ready');}
    })().catch(error=>{if(active){setStatus('error');setFailure(error instanceof Error?error.message:'Native sample readback unavailable');}});
    return()=>{active=false;urls.forEach(url=>URL.revokeObjectURL(url));};
  },[project.id,project.generation,project.revision,token,current,evidence.source_sha256,indices]);
  useEffect(()=>{
    const target=canvas.current,a=content[left],b=content[right];
    if(!target||!a||!b)return;
    let active=true;
    (async()=>{
      const [imageA,imageB]=await Promise.all([createImageBitmap(await (await fetch(a.url)).blob()),createImageBitmap(await (await fetch(b.url)).blob())]);
      try{
        if(!active)return;
        const ratio=Math.min(1,960/Math.max(imageA.width,imageA.height));
        target.width=Math.max(1,Math.round(imageA.width*ratio));target.height=Math.max(1,Math.round(imageA.height*ratio));
        const context=target.getContext('2d',{willReadFrequently:true});
        if(!context)throw new Error('Native comparison canvas unavailable');
        context.clearRect(0,0,target.width,target.height);
        context.drawImage(imageA,0,0,target.width,target.height);
        if(mode==='onion'){
          context.globalAlpha=.5;context.drawImage(imageB,0,0,target.width,target.height);context.globalAlpha=1;
        } else {
          const before=context.getImageData(0,0,target.width,target.height);
          context.clearRect(0,0,target.width,target.height);
          context.drawImage(imageB,0,0,target.width,target.height);
          const after=context.getImageData(0,0,target.width,target.height);
          for(let i=0;i<before.data.length;i+=4){
            before.data[i]=Math.abs(before.data[i]-after.data[i]);
            before.data[i+1]=Math.abs(before.data[i+1]-after.data[i+1]);
            before.data[i+2]=Math.abs(before.data[i+2]-after.data[i+2]);
            before.data[i+3]=255;
          }
          context.putImageData(before,0,0);
        }
      } finally {imageA.close();imageB.close();}
    })().catch(error=>{if(active)setFailure(error instanceof Error?error.message:'Native comparison failed');});
    return()=>{active=false;};
  },[content,left,right,mode]);
  const seconds=(index:number)=>index*evidence.rate.den/evidence.rate.num;
  return <section className="native-contact-review" aria-label="HyperFrames native contact and comparison">
    <header className="native-render-view-heading"><Eye size={15}/><strong>Native contact review</strong></header>
    <p className="production-help">Five source-verified frames from the current renderer revision. This is a sampled inspection, not all-frame approval or evidence of creative quality.</p>
    {(!current||!token)&&<p role="alert" className="production-error">The native review grant does not belong to the selected source revision.</p>}
    {status==='loading'&&<p className="production-help"><RefreshCw size={12}/> Reading bounded native PNG samples…</p>}
    {failure&&<p role="alert" className="production-error">{failure}</p>}
    {content.length>0&&<><div className="native-contact-strip">
      {content.map((sample,i)=><button key={sample.index} type="button" aria-label={'Seek native sample frame '+sample.index}
        onClick={()=>onSeek(seconds(sample.index))}>
        <img src={sample.url} alt={'Native frame '+sample.index+' for sampled visual inspection'} loading="lazy"/>
        <span>F{sample.index} · {seconds(sample.index).toFixed(3)} s</span></button>)}
    </div>
    <div className="native-transform-fields">
      <label className="production-field"><span>Compare A</span><select value={left} onChange={e=>setLeft(Number(e.target.value))}>
        {content.map((sample,index)=><option value={index} key={sample.index}>Frame {sample.index}</option>)}</select></label>
      <label className="production-field"><span>Compare B</span><select value={right} onChange={e=>setRight(Number(e.target.value))}>
        {content.map((sample,index)=><option value={index} key={sample.index}>Frame {sample.index}</option>)}</select></label>
      <label className="production-field"><span>Comparison mode</span><select value={mode} onChange={e=>setMode(e.target.value as Mode)}>
        <option value="onion">50% onion</option><option value="difference">Absolute RGB difference</option></select></label>
    </div>
    <canvas ref={canvas} className="native-review-comparison" aria-label={mode==='onion'?'Two native frames overlay':'Thumbnail RGB channel difference'} />
    <p className="production-help"><Layers size={12}/> These differences are computed on capped browser thumbnails. Pixel comparison is not evidence of source feature equivalence, exact color-management matching or independent artistic acceptance.</p>
    </>}
    <p className="mono">Source {evidence.source_sha256.slice(0,12)}… · revision {evidence.revision}</p>
  </section>;
}
