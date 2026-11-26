import {describe,expect,it} from "vitest";
import {parseNativeAvPlayback,verifiedNativeReviewBytes} from "./nativeAvMedia";

const scope={project:{id:"source-project",generation:"source-generation",revision:7},exportToken:"original-session",
  deliverableId:"profile",masterSha256:"a".repeat(64)};
const token="00000000-0000-4000-8000-000000000001";
const descriptor=()=>({schema:"motionwright.native-av-review/1",project_id:scope.project.id,generation:scope.project.generation,
  revision:7,deliverable_id:"profile",export_token:scope.exportToken,stream_token:token,
  url:"http://motionwright-media.localhost/"+token,sha256:"a".repeat(64),size_bytes:32*1024*1024,
  max_range_bytes:4*1024*1024,media_type:"video/mp4"});

describe("original Native media playback",()=>{
  it("admits bounded local range playback above the old IPC cap",()=>{
    expect(parseNativeAvPlayback(descriptor(),scope)).toEqual({kind:"stream",url:descriptor().url,
      sizeBytes:32*1024*1024,sha256:scope.masterSha256});
    expect(parseNativeAvPlayback({...descriptor(),url:"motionwright-media://localhost/"+token},scope).kind).toBe("stream");
  });
  it("rejects foreign project, revision, delivery and master without a source fallback",()=>{
    for(const patch of [{project_id:"other"},{generation:"other"},{revision:8},{export_token:"other"},
      {deliverable_id:"other"},{sha256:"b".repeat(64)}]){
      expect(()=>parseNativeAvPlayback({...descriptor(),...patch},scope)).toThrow();
    }
  });
  it("never accepts file paths, remote URLs, query parameters or expanded source capabilities",()=>{
    for(const url of ["file:///private/master.mp4","https://motionwright-media.localhost/"+token,
      "http://motionwright-media.localhost.evil/"+token,descriptor().url+"?path=/private",
      descriptor().url+"#fragment","http://motionwright-media.localhost:99/"+token,
      "motionwright-media://foreign/"+token,"http://motionwright-media.localhost/%2e%2e/"+token]){
      expect(()=>parseNativeAvPlayback({...descriptor(),url},scope)).toThrow();
    }
    expect(()=>parseNativeAvPlayback({...descriptor(),path:"private"},scope)).toThrow();
    expect(()=>parseNativeAvPlayback({...descriptor(),export_allowed:true},scope)).toThrow();
  });
  it("enforces media, source and response resource bounds",()=>{
    for(const patch of [{size_bytes:0},{size_bytes:2**30+1},{size_bytes:12.5},{max_range_bytes:2**30},
      {stream_token:"../private"},{media_type:"text/html"},{sha256:"A".repeat(64)},{schema:"other"}]){
      expect(()=>parseNativeAvPlayback({...descriptor(),...patch},scope)).toThrow();
    }
  });
  it("retains validated small binary replies without accepting arbitrary arrays or containers",()=>{
    const bytes=Uint8Array.from([0,0,0,24,102,116,121,112,105,115,111,109]);
    expect(parseNativeAvPlayback(bytes,scope)).toEqual({kind:"bytes",bytes});
    expect(verifiedNativeReviewBytes([...bytes])).toEqual(bytes);
    expect(verifiedNativeReviewBytes(bytes.buffer)).toEqual(bytes);
    for(const value of [[...bytes,NaN],[...bytes,256],[...bytes,-1],[...bytes,"1"],new Uint8Array(16*1024*1024+1),{},null])
      expect(()=>verifiedNativeReviewBytes(value)).toThrow();
  });
});
