import {describe,expect,it} from "vitest";
import {compareRgbaBand,comparisonPair,emptyPixelDelta,exactComparisonSize,verifyExactComparisonPng} from "./nativeExactComparison";
import type {NativeInspectionPlan,NativeSampleEvidence} from "./nativeInspection";
const point=(frameIndex:number)=>({frameIndex,frameCount:60,localFrame:frameIndex,token:"source-token",segmentId:"native-segment",timelineTime:{num:String(frameIndex),den:"30"}});
const plan:NativeInspectionPlan={projectId:"p",generation:"g",revision:2,sceneId:"scene",profileId:"profile",width:2,height:2,frameRate:{num:30,den:1},samples:[point(0),point(1)]};
const sample=(index:number):NativeSampleEvidence=>({frameIndex:index,timelineTime:point(index).timelineTime,width:2,height:2,pngSha256:"a".repeat(64)});
describe("full-resolution original comparison",()=>{
  it("includes RGB and alpha changes and accumulates exclusive bounds across strips",()=>{
    const stats=emptyPixelDelta(),out=new Uint8ClampedArray(8);
    compareRgbaBand(new Uint8ClampedArray([0,0,0,0,5,7,9,255]),new Uint8ClampedArray([0,0,0,9,5,7,9,255]),out,2,0,stats);
    expect([...out]).toEqual([9,9,9,255,0,0,0,255]);
    compareRgbaBand(new Uint8ClampedArray([1,2,3,255,7,8,9,255]),new Uint8ClampedArray([1,2,3,255,9,4,9,255]),out,2,1,stats);
    expect([...out]).toEqual([0,0,0,255,4,4,4,255]);
    expect(stats).toEqual({pixels:4,changedPixels:2,maxChannelDelta:9,totalAbsoluteChannelDelta:15,bounds:{x:0,y:0,right:2,bottom:2}});
  });
  it("never reports a changed pixel for equal decoded inputs",()=>{
    const stats=emptyPixelDelta(),a=new Uint8ClampedArray([8,5,3,255]);
    compareRgbaBand(a,a,new Uint8ClampedArray(4),1,0,stats);
    expect(stats).toEqual({pixels:1,changedPixels:0,maxChannelDelta:0,totalAbsoluteChannelDelta:0,bounds:null});
    expect(()=>compareRgbaBand(a,a,new Uint8ClampedArray(4),1,0,stats)).toThrow();
    expect(()=>compareRgbaBand(a,a,new Uint8ClampedArray(3),1,1,stats)).toThrow();
  });
  it("refuses foreign source, duplicate selection, drifted time, and oversized images",()=>{
    expect(comparisonPair(plan,sample(0),sample(1))).toEqual(plan.samples);
    expect(()=>comparisonPair(plan,sample(0),sample(0))).toThrow();
    expect(()=>comparisonPair(plan,sample(0),sample(3))).toThrow();
    expect(()=>comparisonPair(plan,sample(0),{...sample(1),timelineTime:{num:"4",den:"1"}})).toThrow();
    expect(()=>comparisonPair({...plan,samples:[point(0),{...point(1),token:"other"}]},sample(0),sample(1))).toThrow();
    expect(exactComparisonSize(3840,2160)).toBe(true);
    expect(exactComparisonSize(8192,8192)).toBe(false);
  });
  it("requires exact original PNG dimensions and digest before the decoder",async()=>{
    const bytes=new Uint8Array(33);bytes.set([137,80,78,71,13,10,26,10]);
    const view=new DataView(bytes.buffer);view.setUint32(8,13);view.setUint32(12,0x49484452);view.setUint32(16,2);view.setUint32(20,2);
    const sha=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes)),b=>b.toString(16).padStart(2,"0")).join("");
    expect(await verifyExactComparisonPng(bytes,{...sample(0),pngSha256:sha})).toEqual(bytes);
    await expect(verifyExactComparisonPng(bytes,sample(0))).rejects.toThrow(/changed/);
    await expect(verifyExactComparisonPng(bytes,{...sample(0),width:3,pngSha256:sha})).rejects.toThrow(/dimensions/);
    bytes[0]=0;await expect(verifyExactComparisonPng(bytes,{...sample(0),pngSha256:sha})).rejects.toThrow(/malformed/);
  });
});
