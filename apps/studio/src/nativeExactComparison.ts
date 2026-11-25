/** A local diagnostic of two already verified native PNGs. No renderer,
 * media substitution, regrant, resampling or creative verdict is involved. */
import type { NativeInspectionPlan, NativeSampleEvidence } from "./nativeInspection";

export const MAX_EXACT_COMPARE_PIXELS = 8_388_608;
const MAX_PNG_BYTES = 16 * 1024 * 1024;
const SHA = /^[a-f0-9]{64}$/;
const pngSignature = [137,80,78,71,13,10,26,10];
export interface PixelDelta {
  pixels: number;
  changedPixels: number;
  maxChannelDelta: number;
  totalAbsoluteChannelDelta: number;
  bounds: { x: number; y: number; right: number; bottom: number } | null;
}
export function exactComparisonSize(width: number,height: number): boolean {
  return Number.isSafeInteger(width) && Number.isSafeInteger(height) && width>0 && height>0 &&
    width<=8192 && height<=8192 && width*height<=MAX_EXACT_COMPARE_PIXELS;
}
export function comparisonPair(plan:NativeInspectionPlan, first:NativeSampleEvidence, second:NativeSampleEvidence) {
  if (!exactComparisonSize(plan.width, plan.height) || first.frameIndex===second.frameIndex)
    throw new Error("Select two distinct native frames within the full-resolution comparison budget.");
  const select=(sample:NativeSampleEvidence)=>{
    const point=plan.samples.find(s=>s.frameIndex===sample.frameIndex &&
      s.timelineTime.num===sample.timelineTime.num && s.timelineTime.den===sample.timelineTime.den);
    if (!point || sample.width!==plan.width || sample.height!==plan.height || !SHA.test(sample.pngSha256))
      throw new Error("Comparison frame is outside the inspected original source.");
    return point;
  };
  const a=select(first), b=select(second);
  if (a.token!==b.token || a.segmentId!==b.segmentId || a.frameCount!==b.frameCount)
    throw new Error("Comparison frames must belong to the same native source grant.");
  return [a,b] as const;
}
export async function verifyExactComparisonPng(bytes:Uint8Array, sample:NativeSampleEvidence):Promise<Uint8Array>{
  if (!exactComparisonSize(sample.width,sample.height) || bytes.length<33 || bytes.length>MAX_PNG_BYTES ||
    pngSignature.some((v,i)=>bytes[i]!==v))
    throw new Error("Original PNG is malformed or exceeds the full-resolution comparison budget.");
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  if(view.getUint32(8)!==13 || view.getUint32(12)!==0x49484452 ||
    view.getUint32(16)!==sample.width || view.getUint32(20)!==sample.height)
    throw new Error("Original PNG dimensions differ from the inspected source.");
  const owned=new Uint8Array(bytes);
  if(!SHA.test(sample.pngSha256)||!crypto.subtle)throw new Error("Exact PNG digest verification is unavailable.");
  const digest=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",owned.buffer)),
    byte=>byte.toString(16).padStart(2,"0")).join("");
  if(digest!==sample.pngSha256)throw new Error("Original PNG changed since inspection. Inspect the current source again.");
  return owned;
}
export const emptyPixelDelta=():PixelDelta=>({pixels:0,changedPixels:0,maxChannelDelta:0,totalAbsoluteChannelDelta:0,bounds:null});

/** Inputs are browser-decoded sRGB RGBA, not raw PNG channel samples. Alpha
 * participates in the absolute difference. Every output pixel is grayscale
 * max(|delta R|,|delta G|,|delta B|,|delta A|), opaque for legibility. */
export function compareRgbaBand(first:Uint8ClampedArray,second:Uint8ClampedArray,out:Uint8ClampedArray,
  width:number,yStart:number,stats:PixelDelta):void {
  if(!Number.isSafeInteger(width)||width<1||width>8192||!Number.isSafeInteger(yStart)||yStart<0||
    first.length!==second.length||first.length!==out.length||first.length===0||first.length%(width*4)!==0||
    stats.pixels!==yStart*width || stats.pixels+first.length/4>MAX_EXACT_COMPARE_PIXELS)
    throw new Error("Comparison band has an invalid bounded RGBA shape.");
  for(let i=0;i<first.length;i+=4) {
    const r=Math.abs(first[i]-second[i]),g=Math.abs(first[i+1]-second[i+1]),
      b=Math.abs(first[i+2]-second[i+2]),a=Math.abs(first[i+3]-second[i+3]);
    const peak=Math.max(r,g,b,a);
    out[i]=out[i+1]=out[i+2]=peak;out[i+3]=255;
    stats.totalAbsoluteChannelDelta+=r+g+b+a;
    if(peak>0){
      stats.changedPixels++;
      stats.maxChannelDelta=Math.max(stats.maxChannelDelta,peak);
      const x=(i/4)%width,y=yStart+Math.floor(i/(width*4));
      if(stats.bounds){
        stats.bounds.x=Math.min(stats.bounds.x,x);stats.bounds.y=Math.min(stats.bounds.y,y);
        stats.bounds.right=Math.max(stats.bounds.right,x+1);stats.bounds.bottom=Math.max(stats.bounds.bottom,y+1);
      }else stats.bounds={x,y,right:x+1,bottom:y+1};
    }
  }
  stats.pixels+=first.length/4;
}
