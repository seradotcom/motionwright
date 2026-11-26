import {describe,expect,it} from "vitest";
import sourcePng from "../../../tooling/testdata/synthetic-first-party-viewport.png?inline";
import {MAX_CAPTURE_PNG_BYTES,verifiedCapturePngBytes,capturePngRequest} from "./captureSourcePreview";
import {fixtureProject} from "./fixture";

if(!sourcePng.startsWith("data:image/png;base64,"))throw new Error("Original PNG test asset was not inlined.");
const original=Uint8Array.from(atob(sourcePng.slice(sourcePng.indexOf(",")+1)),char=>char.charCodeAt(0));
const sha=async(bytes:Uint8Array<ArrayBuffer>)=>Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes)),
  byte=>byte.toString(16).padStart(2,"0")).join("");
describe("registered original capture bytes",()=>{
  it("preserves exact PNG bytes across native binary and real-Service JSON transport",async()=>{
    for(const response of [original,original.slice().buffer,[...original]]){
      const accepted=await verifiedCapturePngBytes(response,await sha(original));
      expect(accepted).toEqual(original);
      expect(accepted.buffer).not.toBe(original.buffer);
    }
  });
  it("rejects a different original even when its header still matches",async()=>{
    const altered=original.slice();altered[40]^=1;
    await expect(verifiedCapturePngBytes(altered,await sha(original))).rejects.toThrow("saved digest");
  });
  it("requires the admitted PNG viewport before decoding",async()=>{
    for(const [index,value] of [[0,0],[16,1],[20,1],[24,16],[25,3],[28,1],[original.length-8,0]]){
      const wrong=original.slice();wrong[index]=value;
      await expect(verifiedCapturePngBytes(wrong,await sha(wrong))).rejects.toThrow("PNG viewport");
    }
  });
  it("bounds every transport before allocating an image",async()=>{
    for(const raw of [null,{},[],new Uint8Array(56),new Uint8Array(MAX_CAPTURE_PNG_BYTES+1),
      new Array(MAX_CAPTURE_PNG_BYTES+1)]){
      await expect(verifiedCapturePngBytes(raw,await sha(original))).rejects.toThrow("byte bound");
    }
  });
  it("refuses sparse or coerced JSON bytes and noncanonical digests",async()=>{
    for(const invalid of [undefined,NaN,-1,256,1.5,"137"]){
      const bytes:unknown[]=[...original];bytes[0]=invalid;
      await expect(verifiedCapturePngBytes(bytes,await sha(original))).rejects.toThrow("invalid bytes");
    }
    const sparse:number[]=[...original];delete sparse[5];
    await expect(verifiedCapturePngBytes(sparse,await sha(original))).rejects.toThrow("invalid bytes");
    await expect(verifiedCapturePngBytes(original,(await sha(original)).toUpperCase())).rejects.toThrow("digest is invalid");
  });
  it("cannot request an unregistered capture from an otherwise opened project",()=>{
    expect(()=>capturePngRequest(fixtureProject,"00000000-0000-4000-8000-000000000005")).toThrow("registered capture");
  });
});
