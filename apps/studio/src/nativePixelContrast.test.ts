import {describe,expect,it} from "vitest";
import {
  checkedNativePng,exactContrastPoint,measureNativePixelContrast,
  measureNativeSourceContext,pointPixel,
} from "./nativePixelContrast";

const patch=(rgba:readonly [number,number,number,number]):Uint8ClampedArray=>
  Uint8ClampedArray.from(Array.from({length:9},()=>rgba).flat());

describe("bounded original-frame contrast QC: measured pixels, never approval",()=>{
  it("reports exact WCAG sRGB luminance ratio on manually picked opaque points",()=>{
    const observed=measureNativePixelContrast(patch([0,0,0,255]),patch([255,255,255,255]));
    expect(observed.ratio).toBeCloseTo(21,9);
    expect(observed.reference45).toBe(true);
    expect(observed.reference30).toBe(true);
    expect(observed.interpretation).toBe("manual_sampled_pixel_contrast_only");
    expect(observed.foreground).toEqual([0,0,0]);
    expect(observed.background).toEqual([255,255,255]);
    expect(measureNativePixelContrast(patch([255,255,255,255]),patch([0,0,0,255])).ratio).toBeCloseTo(21);
  });
  it("does not misclassify very low contrast as readable text",()=>{
    const p=measureNativePixelContrast(patch([120,120,120,255]),patch([128,128,128,255]));
    expect(p.ratio).toBeLessThan(1.2);
    expect(p.reference30).toBe(false);
    expect(p.reference45).toBe(false);
  });
  it("refuses gradients, antialias boundaries, transparent pixels and truncated patches",()=>{
    const unstable=patch([250,250,250,255]);unstable[0]=0;
    expect(()=>measureNativePixelContrast(unstable,patch([0,0,0,255]))).toThrow(/edge, gradient/i);
    expect(()=>measureNativePixelContrast(patch([0,0,0,0]),patch([255,255,255,255]))).toThrow(/transparent/i);
    expect(()=>measureNativePixelContrast(new Uint8ClampedArray(32),patch([0,0,0,255]))).toThrow(/nine/i);
  });
  it("maps manually authored percent coordinates to original source pixels",()=>{
    expect(pointPixel(exactContrastPoint(0,100),1920,1080)).toEqual({x:1,y:1078});
    expect(pointPixel(exactContrastPoint(100,0),1920,1080)).toEqual({x:1918,y:1});
    for(const bad of [-1,101,NaN,Infinity]){
      expect(()=>exactContrastPoint(bad,50)).toThrow();
      expect(()=>pointPixel({xPct:0,yPct:bad},1920,1080)).toThrow();
    }
    expect(()=>pointPixel({xPct:10,yPct:20},5000,5000)).toThrow();
  });
  it("returns the measured contrast from exact 3x3 RGBA neighborhoods only",()=>{
    const fg=patch([10,10,10,255]);
    const bg=patch([255,255,255,255]);
    const calls:Array<[number,number,number,number]>=[];
    const context={
      getImageData:(x:number,y:number,w:number,h:number)=>{
        calls.push([x,y,w,h]);
        return {data:calls.length===1?fg:bg};
      },
    } as unknown as CanvasRenderingContext2D;
    const result=measureNativeSourceContext(context,200,100,
      {xPct:20,yPct:30},{xPct:80,yPct:30});
    expect(result.ratio).toBeGreaterThan(18);
    expect(calls).toHaveLength(2);
    expect(calls.every(call=>call[2]===3&&call[3]===3)).toBe(true);
    expect(()=>measureNativeSourceContext(context,200,100,
      {xPct:50,yPct:50},{xPct:50,yPct:50})).toThrow(/distinct/);
  });
  it("rejects tampered native PNG header/dimensions before decoding any image",()=>{
    const header=new Uint8Array(33);
    header.set([137,80,78,71,13,10,26,10],0);
    header.set([73,72,68,82],12);
    const view=new DataView(header.buffer);
    view.setUint32(16,400,false);view.setUint32(20,250,false);
    expect(()=>checkedNativePng(header,400,250)).not.toThrow();
    expect(()=>checkedNativePng(header,401,250)).toThrow(/dimensions/);
    header[0]=0;
    expect(()=>checkedNativePng(header,400,250)).toThrow(/IHDR/);
  });
});
