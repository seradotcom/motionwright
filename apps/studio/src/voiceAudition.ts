import type {Project} from "./types";

export const MAX_VOICE_AUDITION_BYTES=16*1024*1024;
const wavTypes=["audio/wav","audio/x-wav","audio/wave"];
const sha=/^[0-9a-f]{64}$/;

export function voiceAuditionSource(project:Project,trackId:string){
  const track=project.audio.voice_tracks.find(track=>track.id===trackId);
  const asset=track&&project.assets.find(asset=>asset.id===track.asset_id);
  if(!track||!asset||!sha.test(track.source_sha256)||asset.content_sha256!==track.source_sha256
    ||!wavTypes.includes(asset.media_type))
    throw new Error("Select an exact imported measured WAV take.");
  const rate=track.sample_rate_hz,channels=track.channels;
  if(!Number.isInteger(rate)||rate<8000||rate>96000||![1,2].includes(channels))
    throw new Error("Original take listening supports mono/stereo PCM16 WAV at 8–96 kHz.");
  const {num,den}=track.measured_duration;
  if(!/^\d{1,19}$/.test(num)||!/^\d{1,19}$/.test(den))
    throw new Error("Measured take duration is invalid.");
  const n=BigInt(num),d=BigInt(den);
  if(d<=0n||n<=0n||n>d*180n||(n*BigInt(rate))%d!==0n)
    throw new Error("The measured take must have an exact sample clock and last no more than 180 seconds.");
  const frames=n*BigInt(rate)/d;
  if(44n+frames*BigInt(channels)*2n>BigInt(MAX_VOICE_AUDITION_BYTES))
    throw new Error("Original take exceeds the 16 MiB local listening limit.");
  return {track,asset};
}

export function originalVoiceAuditionRequest(project:Project,trackId:string,confirmed:boolean){
  if(!confirmed)throw new Error("Confirm permission to listen to this exact original take locally.");
  const {track}=voiceAuditionSource(project,trackId);
  return {project_id:project.id,generation:project.generation,revision:project.revision,
    track_id:track.id,source_sha256:track.source_sha256,owner_attests_preview_rights:true as const};
}

/** The owner command independently validates the full RIFF index and clock.
 * The WebView accepts only an exact bounded original-container digest. */
export async function verifyOriginalVoiceAuditionBytes(value:unknown,expectedSha:string):Promise<Uint8Array>{
  let bytes:Uint8Array;
  if(value instanceof Uint8Array)bytes=value;
  else if(value instanceof ArrayBuffer)bytes=new Uint8Array(value);
  else if(Array.isArray(value)&&value.length<=MAX_VOICE_AUDITION_BYTES
    &&value.every(v=>typeof v==="number"&&Number.isInteger(v)&&v>=0&&v<=255))bytes=Uint8Array.from(value);
  else throw new Error("Original take read did not return bounded binary WAV bytes.");
  if(bytes.length<44||bytes.length>MAX_VOICE_AUDITION_BYTES||!sha.test(expectedSha)
    ||String.fromCharCode(...bytes.subarray(0,4))!=="RIFF"
    ||String.fromCharCode(...bytes.subarray(8,12))!=="WAVE")
    throw new Error("Original take read did not return a valid bounded RIFF/WAVE.");
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  if(view.getUint32(4,true)+8!==bytes.length)
    throw new Error("Original take WAV size differs from its container header.");
  const owned=new Uint8Array(bytes.length);owned.set(bytes);
  if(!crypto.subtle)throw new Error("Local source SHA-256 verification is unavailable; no playback was admitted.");
  const hash=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",owned.buffer)))
    .map(byte=>byte.toString(16).padStart(2,"0")).join("");
  if(hash!==expectedSha)throw new Error("Original take WAV digest differs from the measured source.");
  return owned;
}

/** A derivative has a different digest than the original. The owner command
 * verifies full original CAS bytes and the saved binding; WebView verifies the
 * closed PCM16 response shape and exact output sample count before playback. */
export function verifyVoiceCutAuditionBytes(value: unknown, frames: number): Uint8Array {
  if (!Number.isSafeInteger(frames) || frames < 48000 || frames > 720000)
    throw new Error("Voice-only audition has an invalid reviewed sample count.");
  const size = 44 + frames * 4;
  let bytes: Uint8Array;
  if (value instanceof Uint8Array) bytes = value;
  else if (value instanceof ArrayBuffer) bytes = new Uint8Array(value);
  else if (Array.isArray(value) && value.length === size &&
    value.every(v=>typeof v==="number"&&Number.isInteger(v)&&v>=0&&v<=255)) bytes=Uint8Array.from(value);
  else throw new Error("Voice-only audition did not return exact bounded PCM bytes.");
  if (bytes.length !== size) throw new Error("Voice-only audition differs from the reviewed output duration.");
  const view = new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  const marker=(at:number,n:number)=>String.fromCharCode(...bytes.subarray(at,at+n));
  if (marker(0,4)!=="RIFF" || marker(8,8)!=="WAVEfmt " || marker(36,4)!=="data"
    || view.getUint32(4,true)!==size-8 || view.getUint32(16,true)!==16
    || view.getUint16(20,true)!==1 || view.getUint16(22,true)!==2
    || view.getUint32(24,true)!==48000 || view.getUint32(28,true)!==192000
    || view.getUint16(32,true)!==4 || view.getUint16(34,true)!==16
    || view.getUint32(40,true)!==frames*4)
    throw new Error("Voice-only audition is not the exact canonical stereo PCM16 output.");
  return new Uint8Array(bytes);
}
