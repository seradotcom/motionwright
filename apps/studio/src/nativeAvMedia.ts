import type {DeliverableProfile, Project} from "./types";
import {parseHybridMasterStreamClock,type HybridMasterStreamClock} from "./hybridNative";

/** Expected output for the supported Native MLT mux. Actual media
 * measurements remain attached to the original rendered artifact. */
export function nativeAvMasterContract(profile: DeliverableProfile | null | undefined) {
  if (!profile || profile.video_codec !== "h264" || profile.audio_codec !== "aac"
    || profile.audio_sample_rate_hz !== 48_000 || profile.color_space !== "rec709" || profile.container !== "mp4")
    return null;
  return {
    video: { codec: "H.264", frameRate: profile.frame_rate.num + "/" + profile.frame_rate.den,
      pixelFormat: "yuv420p", primaries: "BT.709", transfer: "BT.709", matrix: "BT.709", range: "limited", alpha: "opaque" },
    audio: { codec: "AAC", sampleRateHz: 48_000, channels: 2 },
    container: "MP4",
    captions: !profile.captions ? "None" : profile.burn_in_captions ? "Burn-in on supported routes"
      : profile.caption_format === "web_vtt" ? "Separate WebVTT sidecar" : "Separate SRT sidecar",
  } as const;
}

export type SavedHybridTiming =
  | {status:"observed";clock:HybridMasterStreamClock}
  | {status:"unavailable"};

export type NativeAvPlayback =
  | {kind:"bytes";bytes:Uint8Array}
  | {kind:"stream";url:string;sizeBytes:number;sha256:string;savedHybridTiming?:SavedHybridTiming};

export interface NativeAvPlaybackScope {
  project:Pick<Project,"id"|"generation"|"revision">;
  exportToken:string|null;
  deliverableId?:string|null;
  masterSha256?:string|null;
  savedHybridFrameCount?:number;
}

export function verifiedNativeReviewBytes(data:unknown):Uint8Array {
  const maximum=16*1024*1024;
  let bytes:Uint8Array;
  if(data instanceof Uint8Array&&data.byteLength<=maximum)bytes=data;
  else if(data instanceof ArrayBuffer&&data.byteLength<=maximum)bytes=new Uint8Array(data);
  else if(Array.isArray(data)&&data.length<=maximum&&data.every(value=>Number.isInteger(value)&&value>=0&&value<=255))
    bytes=Uint8Array.from(data);
  else throw new Error("Native media returned an invalid or unbounded binary response.");
  if(bytes.length<12||bytes[4]!==102||bytes[5]!==116||bytes[6]!==121||bytes[7]!==112)
    throw new Error("Native media returned an invalid MP4 signature.");
  return bytes;
}

function parseSavedHybridTiming(value:unknown,frames:number):SavedHybridTiming {
  if(value&&typeof value==="object"&&"status" in value){
    if(value.status==="unavailable"&&Object.keys(value).length===1) return {status:"unavailable"};
    if(value.status==="observed"&&Object.keys(value).length===2&&Object.hasOwn(value,"clock")&&"clock" in value)
      return {status:"observed",clock:parseHybridMasterStreamClock(value.clock,frames)};
  }
  throw new Error("The saved hybrid stream measurement is invalid.");
}

/** This parses only the admitted backend response. A URL, digest or token
 * supplied by a project, receipt hint or imported asset cannot create a reader.
 * A legacy small-file binary reply retains its existing bounded IPC contract. */
export function parseNativeAvPlayback(data:unknown,scope:NativeAvPlaybackScope):NativeAvPlayback {
  if(data instanceof Uint8Array||data instanceof ArrayBuffer||Array.isArray(data)){
    if(scope.exportToken===null)throw new Error("Saved master recovery requires an exact original stream descriptor.");
    return {kind:"bytes",bytes:verifiedNativeReviewBytes(data)};
  }
  if(!data||typeof data!=="object")throw new Error("Native media playback is unavailable.");
  const value=data as Record<string,unknown>;
  const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  const version2=value.schema==="motionwright.native-av-review/2";
  const frameCount=scope.savedHybridFrameCount;
  const allowed=new Set(["schema","project_id","generation","revision","deliverable_id","export_token",
    "stream_token","url","sha256","size_bytes","max_range_bytes","media_type"]);
  if(version2) allowed.add("saved_hybrid_timing");
  if(Object.keys(value).some(key=>!allowed.has(key))||Object.keys(value).length!==allowed.size||
    (!version2&&value.schema!=="motionwright.native-av-review/1")||
    (version2&&(scope.exportToken!==null||frameCount===undefined||!Number.isSafeInteger(frameCount)||frameCount<30||frameCount>180))||
    value.project_id!==scope.project.id||
    value.generation!==scope.project.generation||value.revision!==scope.project.revision||
    (scope.exportToken!==null&&value.export_token!==scope.exportToken)||
    (scope.exportToken===null&&(typeof value.export_token!=="string"||!uuid.test(value.export_token)))||
    typeof value.deliverable_id!=="string"||
    (scope.deliverableId!=null&&value.deliverable_id!==scope.deliverableId)||
    typeof value.stream_token!=="string"||!uuid.test(value.stream_token)||
    typeof value.sha256!=="string"||!/^[0-9a-f]{64}$/.test(value.sha256)||
    (scope.masterSha256!=null&&value.sha256!==scope.masterSha256)||
    !Number.isSafeInteger(value.size_bytes)||(value.size_bytes as number)<12||
    (value.size_bytes as number)>1024*1024*1024||value.max_range_bytes!==4*1024*1024||
    value.media_type!=="video/mp4"||
    (value.url!=="motionwright-media://localhost/"+value.stream_token&&
      value.url!=="http://motionwright-media.localhost/"+value.stream_token))
    throw new Error("Native media playback does not match the selected original source.");
  return {kind:"stream",url:value.url as string,sizeBytes:value.size_bytes as number,sha256:value.sha256,
    ...(version2&&frameCount!==undefined
      ? {savedHybridTiming:parseSavedHybridTiming(value.saved_hybrid_timing,frameCount)} : {})};
}
