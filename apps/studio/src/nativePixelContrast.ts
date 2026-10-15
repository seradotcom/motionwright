/** Bounded measurement of two manually selected, source-verified native
 * PNG regions. This is pixel contrast, never text/accessibility/aesthetic approval.
 * Both RGB patches are browser-decoded from the same original PNG digest.
 */
export interface ContrastPoint { xPct:number; yPct:number }
export interface PixelContrastMeasurement {
  foreground: [number,number,number];
  background: [number,number,number];
  ratio:number;
  reference45:boolean;
  reference30:boolean;
  interpretation:"manual_sampled_pixel_contrast_only";
}
const fail=(message:string):never=>{throw new Error(message);};
const luminance=(rgb:readonly number[])=>{
  const linear=rgb.map(channel=>{
    if(!Number.isInteger(channel)||channel<0||channel>255)fail("Pixel colors are outside sRGB byte bounds.");
    const c=channel/255;
    return c<=0.04045?c/12.92:((c+0.055)/1.055)**2.4;
  });
  return 0.2126*linear[0]+0.7152*linear[1]+0.0722*linear[2];
};
function medianChannel(patch:Uint8ClampedArray,channel:number):number{
  const values:Array<number>=[];
  for(let i=0;i<9;i++)values.push(patch[i*4+channel]);
  values.sort((a,b)=>a-b);
  if(values[8]-values[0]>32)
    fail("Selected region crosses an edge, gradient or antialiased pixels. Choose stable foreground/background patches.");
  return values[4];
}
function inspectPatch(patch:Uint8ClampedArray,label:string):[number,number,number] {
  if(!(patch instanceof Uint8ClampedArray)||patch.length!==36)
    fail("Contrast requires exactly nine browser-decoded RGBA pixels per patch.");
  for(let i=0;i<9;i++)if(patch[i*4+3]!==255)
    fail(label+" sample is transparent; composite background is not verified.");
  return [medianChannel(patch,0),medianChannel(patch,1),medianChannel(patch,2)];
}
export function measureNativePixelContrast(foreground:Uint8ClampedArray,background:Uint8ClampedArray):PixelContrastMeasurement{
  const fg=inspectPatch(foreground,"Foreground");
  const bg=inspectPatch(background,"Background");
  const a=luminance(fg),b=luminance(bg);
  const ratio=(Math.max(a,b)+0.05)/(Math.min(a,b)+0.05);
  if(!Number.isFinite(ratio)||ratio<1||ratio>21.000001)
    fail("Decoded pixel contrast could not be computed.");
  return {
    foreground:fg,background:bg,ratio,
    reference45:ratio>=4.5,reference30:ratio>=3,
    interpretation:"manual_sampled_pixel_contrast_only"
  };
}
export function exactContrastPoint(xPct:number,yPct:number):ContrastPoint {
  if(!Number.isFinite(xPct)||!Number.isFinite(yPct)||
      xPct<0||xPct>100||yPct<0||yPct>100)
    return fail("Sample coordinates must be between zero and one hundred percent.");
  return {xPct,yPct};
}
export function pointPixel(point:ContrastPoint,width:number,height:number):{x:number;y:number}{
  exactContrastPoint(point.xPct,point.yPct);
  if(!Number.isSafeInteger(width)||!Number.isSafeInteger(height)||
      width<3||height<3||width*height>16_777_216)
    return fail("Native decode dimensions exceed the bounded pixel sample budget.");
  return {
    x:Math.max(1,Math.min(width-2,Math.round(point.xPct*(width-1)/100))),
    y:Math.max(1,Math.min(height-2,Math.round(point.yPct*(height-1)/100)))
  };
}
export function measureNativeSourceContext(context:CanvasRenderingContext2D,
    width:number,height:number,foreground:ContrastPoint,background:ContrastPoint):PixelContrastMeasurement{
  const fg=pointPixel(foreground,width,height),bg=pointPixel(background,width,height);
  if(fg.x===bg.x&&fg.y===bg.y)
    return fail("Foreground and background points must identify distinct source pixels.");
  const first=context.getImageData(fg.x-1,fg.y-1,3,3).data;
  const second=context.getImageData(bg.x-1,bg.y-1,3,3).data;
  return measureNativePixelContrast(first,second);
}
const PNG=Uint8Array.from([137,80,78,71,13,10,26,10]);
export function checkedNativePng(bytes:Uint8Array,width:number,height:number):void{
  if(!(bytes instanceof Uint8Array)||bytes.length<33||bytes.length>16*1024*1024)
    return fail("Native source PNG is truncated or exceeds the 16 MiB limit.");
  if(!PNG.every((v,i)=>bytes[i]===v) ||
      bytes[12]!==73||bytes[13]!==72||bytes[14]!==68||bytes[15]!==82)
    return fail("Native frame response is not a valid PNG IHDR source.");
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  if(!Number.isSafeInteger(width)||!Number.isSafeInteger(height)||width<3||height<3||
      width*height>16_777_216||view.getUint32(16,false)!==width||view.getUint32(20,false)!==height)
    return fail("Native PNG dimensions do not match the current source-bound output profile.");
}
export async function sourcePngSha256(bytes:Uint8Array):Promise<string>{
  const owned=new Uint8Array(bytes.length);owned.set(bytes);
  const digest=await crypto.subtle.digest("SHA-256",owned.buffer);
  return Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,"0")).join("");
}
