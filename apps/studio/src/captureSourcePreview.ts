import type { Project } from "./types";
import { validateCaptureRecord } from "./captureSource";

export interface CapturePngRequest {
  project_id:string; generation:string; revision:number; capture_id:string; screenshot_sha256:string;
}
export const MAX_CAPTURE_PNG_BYTES=12*1024*1024;

/** Selection only. Native independently resolves the registered source and all three CAS originals. */
export function capturePngRequest(project:Project,captureId:string):CapturePngRequest {
  const capture=project.production_design?.captures?.find(item=>item.id===captureId);
  if(!capture)throw new Error("Choose an existing registered capture.");
  validateCaptureRecord(project,capture);
  return {project_id:project.id,generation:project.generation,revision:project.revision,
    capture_id:capture.id,screenshot_sha256:capture.screenshot_sha256};
}

/** Tauri binary IPC and the real-Service JSONL fixture carry the same original bytes.
 * Never create a URL from a response until its exact length, format and SHA match. */
export async function verifiedCapturePngBytes(raw:unknown,expectedSha:string):Promise<Uint8Array<ArrayBuffer>> {
  if(!/^[0-9a-f]{64}$/.test(expectedSha))throw new Error("Original capture digest is invalid.");
  const length=raw instanceof ArrayBuffer?raw.byteLength:raw instanceof Uint8Array?raw.byteLength:
    Array.isArray(raw)?raw.length:0;
  if(length<57||length>MAX_CAPTURE_PNG_BYTES)throw new Error("Original capture exceeds the PNG byte bound.");
  let bytes:Uint8Array<ArrayBuffer>;
  if(raw instanceof ArrayBuffer)bytes=new Uint8Array(raw.slice(0));
  else if(raw instanceof Uint8Array)bytes=new Uint8Array(raw);
  else if(Array.isArray(raw)){
    bytes=new Uint8Array(length);
    for(let i=0;i<length;i++){
      if(!Number.isInteger(raw[i])||raw[i]<0||raw[i]>255)throw new Error("Original capture contains invalid bytes.");
      bytes[i]=raw[i];
    }
  }else throw new Error("Original capture binary response is unavailable.");
  const view=new DataView(bytes.buffer),signature=[137,80,78,71,13,10,26,10];
  if(signature.some((value,index)=>bytes[index]!==value)||view.getUint32(8)!==13||
    String.fromCharCode(...bytes.subarray(12,16))!=="IHDR"||view.getUint32(16)!==1440||
    view.getUint32(20)!==940||bytes[24]!==8||![2,6].includes(bytes[25])||
    bytes[26]!==0||bytes[27]!==0||bytes[28]!==0||
    view.getUint32(length-12)!==0||String.fromCharCode(...bytes.subarray(length-8,length-4))!=="IEND")
    throw new Error("Original capture is not the admitted PNG viewport.");
  const digest=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes)),
    byte=>byte.toString(16).padStart(2,"0")).join("");
  if(digest!==expectedSha)throw new Error("Original capture bytes do not match the saved digest.");
  return bytes;
}
