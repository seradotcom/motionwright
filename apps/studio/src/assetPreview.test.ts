import {describe,expect,it} from "vitest";
import {assetPreviewSupported,parseAssetPreview} from "./assetPreview";
import {fixtureProject} from "./fixture";
import type {Asset} from "./types";
const asset:Asset={id:"00000000-0000-4000-8000-000000000051",name:"Original.mp4",media_type:"video/mp4",
  content_sha256:"ab".repeat(32),source_revision:"imported"};
const project={...fixtureProject,assets:[asset]};
const token="00000000-0000-4000-8000-000000000052";
const source=()=>({schema:"motionwright.original-asset-preview/1",project_id:project.id,generation:project.generation,
  revision:project.revision,asset_id:asset.id,stream_token:token,url:"http://motionwright-asset.localhost/"+token,
  sha256:asset.content_sha256,size_bytes:32*1024*1024,max_range_bytes:4*1024*1024,media_type:"video/mp4"});
describe("imported original media descriptor",()=>{
  it("binds source identity and both native local protocol forms",()=>{
    expect(parseAssetPreview(source(),project,asset).asset_id).toBe(asset.id);
    expect(parseAssetPreview({...source(),url:"motionwright-asset://localhost/"+token},project,asset).stream_token).toBe(token);
    const wav={...asset,media_type:"audio/x-wav"};
    expect(parseAssetPreview({...source(),media_type:"audio/wav"},{...project,assets:[wav]},wav).media_type).toBe("audio/wav");
  });
  it("refuses another project, generation, revision, asset, digest or declared media",()=>{
    for(const patch of [{project_id:"foreign"},{generation:"foreign"},{revision:project.revision+1},
      {asset_id:"foreign"},{sha256:"cd".repeat(32)},{media_type:"text/html"}])
      expect(()=>parseAssetPreview({...source(),...patch},project,asset)).toThrow();
    expect(()=>parseAssetPreview(source(),{...project,assets:[]},asset)).toThrow();
  });
  it("refuses paths, remote hosts and Native master/export authority",()=>{
    for(const url of ["file:///private.mp4","https://motionwright-asset.localhost/"+token,
      "http://motionwright-asset.localhost.evil/"+token,source().url+"?path=private",
      source().url+"#media","http://motionwright-asset.localhost:80/"+token,
      "motionwright-media://localhost/"+token])
      expect(()=>parseAssetPreview({...source(),url},project,asset)).toThrow();
    for(const field of ["path","export_token","effect_grant","receipt","verified_native"])
      expect(()=>parseAssetPreview({...source(),[field]:"caller"},project,asset)).toThrow();
  });
  it("preserves byte, range and supported-type bounds",()=>{
    for(const patch of [{size_bytes:0},{size_bytes:2**30+1},{size_bytes:12.5},
      {max_range_bytes:2**30},{stream_token:"../private"},{stream_token:"00000000-0000-0000-0000-000000000000"}])
      expect(()=>parseAssetPreview({...source(),...patch},project,asset)).toThrow();
    expect(assetPreviewSupported({...asset,media_type:"image/svg+xml"})).toBe(false);
    expect(assetPreviewSupported({...asset,content_sha256:null})).toBe(false);
  });
});
