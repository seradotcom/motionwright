import {invoke} from "@tauri-apps/api/core";
import type {Asset,Project} from "./types";

export interface AssetPreviewDescriptor {
  schema:"motionwright.original-asset-preview/1";
  project_id:string;generation:string;revision:number;asset_id:string;stream_token:string;
  url:string;sha256:string;size_bytes:number;max_range_bytes:number;media_type:"video/mp4"|"audio/wav"|"image/png"|"image/jpeg";
}
const FIELDS=["schema","project_id","generation","revision","asset_id","stream_token","url","sha256","size_bytes","max_range_bytes","media_type"];
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function assetPreviewSupported(asset:Asset):boolean {
  return ["video/mp4","audio/wav","audio/x-wav","audio/wave","image/png","image/jpeg"].includes(asset.media_type)&&/^[0-9a-f]{64}$/.test(asset.content_sha256??"");
}
export function parseAssetPreview(raw:unknown,project:Project,asset:Asset):AssetPreviewDescriptor {
  if(!raw||typeof raw!=="object"||Array.isArray(raw))throw new Error("Original media descriptor is unavailable.");
  const value=raw as AssetPreviewDescriptor;
  const mime=["audio/wav","audio/x-wav","audio/wave"].includes(asset.media_type)?"audio/wav":asset.media_type;
  const image=mime==="image/png"||mime==="image/jpeg";
  if(Object.keys(value).sort().join("|")!==[...FIELDS].sort().join("|")||
    !assetPreviewSupported(asset)||!project.assets.some(item=>item.id===asset.id&&
      item.content_sha256===asset.content_sha256&&item.media_type===asset.media_type)||
    value.schema!=="motionwright.original-asset-preview/1"||
    value.project_id!==project.id||value.generation!==project.generation||value.revision!==project.revision||
    value.asset_id!==asset.id||value.sha256!==asset.content_sha256||value.media_type!==mime||
    typeof value.stream_token!=="string"||!UUID.test(value.stream_token)||value.stream_token==="00000000-0000-0000-0000-000000000000"||
    !Number.isSafeInteger(value.size_bytes)||value.size_bytes<12||value.size_bytes>(image?16*1024*1024:1024**3)||
    value.max_range_bytes!==4*1024*1024||
    !["http://motionwright-asset.localhost/"+value.stream_token,"motionwright-asset://localhost/"+value.stream_token].includes(value.url))
    throw new Error("Original media does not match the selected saved asset.");
  return value;
}
export async function openAssetPreview(project:Project,asset:Asset):Promise<AssetPreviewDescriptor> {
  if(typeof window==="undefined"||!("__TAURI_INTERNALS__" in window))
    throw new Error("Original media preview requires the connected Desktop service.");
  if(!assetPreviewSupported(asset))throw new Error("Choose an imported MP4, WAV, static PNG or JPEG source.");
  const request={project_id:project.id,generation:project.generation,revision:project.revision,
    asset_id:asset.id,sha256:asset.content_sha256};
  return parseAssetPreview(await invoke<unknown>("asset_preview_open",{request}),project,asset);
}
export async function closeAssetPreview(streamToken:string):Promise<void> {
  if(!UUID.test(streamToken))return;
  await invoke("asset_preview_close",{request:{stream_token:streamToken}});
}
