import {describe,expect,it} from "vitest";
import {
  MAX_NATIVE_PNG_BYTES,
  MAX_PREVIEW_CACHE_BYTES,
  MAX_PREVIEW_CACHE_FRAMES,
  NativePreviewByteCache,
  planNativePreviewRead,
  previewTier,
  requireBoundedNativePng,
} from "./nativeProgressivePreview";

const baseline={
  desiredFrame:8,showingFrame:4,playing:true,
  inFlight:false,failedFrame:null,cacheHit:false,
  nowMs:900,targetChangedAtMs:895,lastRequestStartedAtMs:500,
} as const;
const buffer=(size:number,value=0)=>new Uint8Array(size).fill(value);

describe("v0.5 progressive source-verified native preview request policy",()=>{
  it("distinguishes sparse scrub frames from exact settled source frames",()=>{
    expect(previewTier(true)).toBe("sampled_scrub");
    expect(previewTier(false)).toBe("settled_exact");
    expect(planNativePreviewRead(baseline)).toBe("fetch");
  });
  it("limits active scrub to one read start per quarter second",()=>{
    expect(planNativePreviewRead({...baseline,nowMs:749,targetChangedAtMs:600})).toBe("hold");
    expect(planNativePreviewRead({...baseline,nowMs:750,targetChangedAtMs:600})).toBe("fetch");
    expect(planNativePreviewRead({...baseline,lastRequestStartedAtMs:800,nowMs:1049})).toBe("hold");
    expect(planNativePreviewRead({...baseline,lastRequestStartedAtMs:800,nowMs:1050})).toBe("fetch");
  });
  it("waits 120ms after the exact paused playhead target settles",()=>{
    expect(planNativePreviewRead({...baseline,playing:false,nowMs:1000,targetChangedAtMs:900})).toBe("hold");
    expect(planNativePreviewRead({...baseline,playing:false,nowMs:1019,targetChangedAtMs:900})).toBe("hold");
    expect(planNativePreviewRead({...baseline,playing:false,nowMs:1020,targetChangedAtMs:900})).toBe("fetch");
  });
  it("does not request a frame already presented as current",()=>{
    expect(planNativePreviewRead({...baseline,showingFrame:8})).toBe("hold");
  });
  it("does not initiate a second native read while another is outstanding",()=>{
    expect(planNativePreviewRead({...baseline,inFlight:true})).toBe("hold");
  });
  it("never automatically retries an unsuccessful source frame",()=>{
    expect(planNativePreviewRead({...baseline,failedFrame:8})).toBe("hold");
    expect(planNativePreviewRead({...baseline,failedFrame:7})).toBe("fetch");
  });
  it("uses a source-scoped cached frame instead of issuing another grant readback",()=>{
    expect(planNativePreviewRead({...baseline,cacheHit:true})).toBe("cache");
    expect(planNativePreviewRead({...baseline,cacheHit:true,inFlight:true})).toBe("cache");
  });
  it("never uses cache as proof if the precise frame is already visible",()=>{
    expect(planNativePreviewRead({...baseline,showingFrame:8,cacheHit:true})).toBe("hold");
  });
  it("rejects an invalid frame index or reversed clock",()=>{
    for(const desiredFrame of [-1,NaN,Infinity,0.3,Number.MAX_SAFE_INTEGER+1]){
      expect(planNativePreviewRead({...baseline,desiredFrame})).toBe("hold");
    }
    expect(planNativePreviewRead({...baseline,targetChangedAtMs:901})).toBe("hold");
    expect(planNativePreviewRead({...baseline,nowMs:NaN})).toBe("hold");
  });
});

describe("source-lifetime bounded native PNG byte cache",()=>{
  it("has explicit small frame and byte budgets",()=>{
    expect(MAX_PREVIEW_CACHE_FRAMES).toBe(3);
    expect(MAX_PREVIEW_CACHE_BYTES).toBe(32*1024*1024);
  });
  it("accepts real-sized frames only within a bounded lifetime",()=>{
    const c=new NativePreviewByteCache();
    expect(c.put(1,buffer(40,1))).toBe(true);
    expect(c.put(2,buffer(48,2))).toBe(true);
    expect(c.size).toBe(2);
    expect(c.bytes).toBe(88);
    expect(c.get(1)).toHaveLength(40);
    c.clear();
    expect(c.get(1)).toBeNull();
    expect(c.bytes).toBe(0);
  });
  it("evicts the oldest frame when a fourth verified image arrives",()=>{
    const c=new NativePreviewByteCache();
    for(let i=0;i<4;i++)expect(c.put(i,buffer(40,i))).toBe(true);
    expect(c.has(0)).toBe(false);
    expect(c.has(3)).toBe(true);
    expect(c.size).toBe(3);
  });
  it("reads update LRU precedence before eviction",()=>{
    const c=new NativePreviewByteCache();
    for(let i=0;i<3;i++)c.put(i,buffer(32,i));
    c.get(0);
    c.put(3,buffer(32,3));
    expect(c.has(0)).toBe(true);
    expect(c.has(1)).toBe(false);
  });
  it("evicts by total byte budget without unbounded renderer media accumulation",()=>{
    const c=new NativePreviewByteCache();
    const bytes=12*1024*1024;
    c.put(0,buffer(bytes));
    c.put(1,buffer(bytes));
    c.put(2,buffer(bytes));
    expect(c.bytes).toBeLessThanOrEqual(MAX_PREVIEW_CACHE_BYTES);
    expect(c.has(0)).toBe(false);
    expect(c.has(1)).toBe(true);
    expect(c.has(2)).toBe(true);
  });
  it("rejects empty, invalid indexes and oversized media",()=>{
    const c=new NativePreviewByteCache();
    expect(c.put(-1,buffer(8))).toBe(false);
    expect(c.put(1,buffer(0))).toBe(false);
    expect(c.put(1,buffer(MAX_PREVIEW_CACHE_BYTES+1))).toBe(false);
    expect(c.size).toBe(0);
  });
  it("owns copies of source bytes so callers cannot mutate an admitted cache entry",()=>{
    const c=new NativePreviewByteCache();
    const original=buffer(20,5);
    c.put(0,original);
    original[0]=1;
    expect(c.get(0)?.[0]).toBe(5);
  });
  it("supports replacement and scoped removal with exact byte accounting",()=>{
    const c=new NativePreviewByteCache();
    c.put(0,buffer(28));
    c.put(0,buffer(44));
    expect(c.size).toBe(1);
    expect(c.bytes).toBe(44);
    c.delete(0);
    c.delete(0);
    expect(c.size).toBe(0);
    expect(c.bytes).toBe(0);
  });
});

describe("native pixels stay from the actual checked PNG data",()=>{
  const png=()=>{
    const bytes=buffer(36);
    bytes.set([137,80,78,71,13,10,26,10],0);
    return bytes;
  };
  it("requires the exact PNG signature without synthesizing a frame",()=>{
    expect(()=>requireBoundedNativePng(png())).not.toThrow();
    const bad=png();bad[3]=72;
    expect(()=>requireBoundedNativePng(bad)).toThrow(/invalid or unbounded/);
  });
  it("rejects too short or unbounded PNG containers",()=>{
    expect(()=>requireBoundedNativePng(buffer(8))).toThrow();
    expect(()=>requireBoundedNativePng(buffer(MAX_NATIVE_PNG_BYTES+1))).toThrow();
  });
});
