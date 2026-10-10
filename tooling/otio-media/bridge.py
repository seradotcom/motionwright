#!/usr/bin/env python3
"""Owner-scoped OTIO ExternalReference packaging and bounded cut import preview.

This extends Motionwright's existing conservative OTIO Timeline. It never
guesses scene media, invents a missing clip, launches an NLE, grants source
rights, executes project code, or directly commits editorial changes.
"""
from __future__ import annotations
import argparse
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

SCHEMA="motionwright.otio-portable-media-bridge/1"
HEX=re.compile(r"^[a-f0-9]{64}$")
UUID=re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")
ALLOWED_EXT={".mp4", ".mov", ".mkv", ".webm"}
MAX_MEDIA=256*1024*1024
MAX_TOTAL=512*1024*1024
MAX_SCENES=32
MAX_JSON=2*1024*1024
RATES={24,25,30,60}

class BridgeRefused(ValueError):pass
def insist(ok:bool,message:str)->None:
    if not ok:raise BridgeRefused(message)
def exact(value:object,fields:set[str],label:str)->dict:
    insist(isinstance(value,dict) and set(value)==fields,
           f"{label} fields are missing or unsupported")
    return value
def sha_bytes(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def fingerprint(path:Path,budget:int)->tuple[str,int]:
    insist(path.is_file() and not path.is_symlink(),f"Missing or unsafe file: {path.name}")
    size=path.stat().st_size
    insist(0<size<=budget,f"File exceeds byte budget: {path.name}")
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        while block:=stream.read(1024*1024):digest.update(block)
    return digest.hexdigest(),size
def owner_file(root:Path,name:str,budget:int,extension:set[str]|None=None)->Path:
    insist(isinstance(name,str) and 1<=len(name)<=250,
           "Owner file requires a bounded relative path")
    rel=Path(name)
    insist(not rel.is_absolute() and all(part not in ("..",".")for part in rel.parts),
           "Owner file path may not escape source root")
    if extension:insist(rel.suffix.lower() in extension,"Owner file type is unsupported")
    path=root
    for component in rel.parts:
        path=path/component
        insist(not path.is_symlink(),"Symlinked source paths are never admitted")
    fingerprint(path,budget)
    return path
def read_json(root:Path,name:str)->dict:
    data=owner_file(root,name,MAX_JSON).read_bytes()
    result=json.loads(data)
    insist(isinstance(result,dict),"Expected a bounded JSON object")
    return result
def rational(source:object,label:str)->Fraction:
    value=exact(source,{"num","den"},label)
    def integer(v:object)->int:
        # Semwright RationalTime serializes 64-bit numerators/denominators as
        # decimal strings to preserve integer precision in JavaScript.
        if type(v)is int:
            return v
        insist(isinstance(v,str) and re.fullmatch(r"(0|[1-9][0-9]{0,9})",v)is not None,
               f"Invalid canonical rational-time integer encoding in {label}")
        return int(v)
    numerator,denominator=integer(value["num"]),integer(value["den"])
    insist(0<=numerator<=1000000 and 0<denominator<=1000000,
           f"Invalid nonnegative rational time for {label}")
    return Fraction(numerator,denominator)
def frames(value:Fraction,rate:int,label:str)->int:
    result=value*rate
    insist(result.denominator==1 and 0<result.numerator<=MAX_SCENES*3600,
           f"{label} cannot be expressed in exact integral source frames")
    return result.numerator
def rtc(value:int,rate:int)->dict:
    return {"OTIO_SCHEMA":"RationalTime.1","rate":float(rate),"value":float(value)}
def trange(start:int,duration:int,rate:int)->dict:
    return {"OTIO_SCHEMA":"TimeRange.1","duration":rtc(duration,rate),
            "start_time":rtc(start,rate)}
def file_probe(media:Path)->dict:
    args=["ffprobe","-v","error","-select_streams","v:0",
          "-show_entries","stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames",
          "-of","json",str(media)]
    try:run=subprocess.run(args,capture_output=True,timeout=20,check=False)
    except (FileNotFoundError,subprocess.TimeoutExpired) as error:
        raise BridgeRefused("A bounded ffprobe video readback is unavailable") from error
    insist(run.returncode==0 and len(run.stdout)<MAX_JSON,
           "Media video stream could not be independently probed")
    observed=json.loads(run.stdout)
    streams=observed.get("streams",[])
    insist(len(streams)==1,"Expected exactly one probed video stream")
    item=streams[0]
    insist(item.get("codec_name")=="h264",
           "Portable first-party NLE media profile currently requires actual H.264")
    insist(type(item.get("width"))is int and type(item.get("height"))is int
           and 1<=item["width"]<=4096 and 1<=item["height"]<=4096,
           "Original media geometry exceeds receiver profile")
    try:
        fps=Fraction(item["r_frame_rate"])
        average=Fraction(item["avg_frame_rate"])
        count=int(item["nb_frames"])
    except (KeyError,ValueError,TypeError,ZeroDivisionError)as error:
        raise BridgeRefused("Media needs measured constant-rate counted frames")from error
    insist(fps==average and fps.denominator==1 and fps.numerator in RATES,
           "Only exact constant 24/25/30/60 fps sources are admitted")
    insist(str(count)==str(item["nb_frames"]) and 1<=count<=216000,
           "Media frame count must be measured and canonical")
    return {"fps":fps.numerator,"frames":count,"width":item["width"],"height":item["height"],
            "codec":"h264"}
def parse_project(root:Path,name:str)->tuple[dict,str]:
    path=owner_file(root,name,MAX_JSON)
    digest,_=fingerprint(path,MAX_JSON)
    project=json.loads(path.read_text(encoding="utf8"))
    insist(isinstance(project,dict) and
           all(isinstance(project.get(key),str) and UUID.fullmatch(project[key].lower())
               for key in ("id","generation")) and
           type(project.get("revision"))is int,"Invalid original project identity")
    scenes=project.get("scenes")
    insist(isinstance(scenes,list) and 1<=len(scenes)<=MAX_SCENES,
           "Native project has an unsupported scene count")
    ids=set()
    expected_start=Fraction(0)
    for scene in scenes:
        insist(isinstance(scene,dict)and isinstance(scene.get("id"),str)
               and UUID.fullmatch(scene["id"].lower())and scene["id"]not in ids,
               "Native project scenes require unique canonical identity")
        ids.add(scene["id"])
        start=rational(scene.get("start"),"scene start")
        length=rational(scene.get("duration"),"scene duration")
        insist(length>0 and start==expected_start,
               "Native scenes must form a contiguous cut before NLE export")
        frames(length,24,"OTIO original 24fps scene duration")
        expected_start+=length
    assets=project.get("assets",[])
    insist(isinstance(assets,list)and len(assets)<=128,"Native asset ledger is malformed")
    asset_ids=[a.get("id")for a in assets if isinstance(a,dict)]
    insist(len(asset_ids)==len(set(asset_ids)),"Project asset ledger contains duplicate identities")
    return project,digest
def validate_existing_otio(project:dict,source:dict)->list[dict]:
    insist(source.get("OTIO_SCHEMA")=="Timeline.1","Existing exporter is not an OTIO Timeline")
    meta=source.get("metadata",{}).get("motionwright",{})
    insist(meta.get("project_id")==project["id"] and
           meta.get("generation")==project["generation"] and
           meta.get("revision")==project["revision"] and
           meta.get("export_profile")=="conservative-cut-v1",
           "Existing exporter/source revision drift")
    tracks=source.get("tracks",{}).get("children",[])
    insist(isinstance(tracks,list)and len(tracks)==1 and
           tracks[0].get("OTIO_SCHEMA")=="Track.1"and tracks[0].get("kind")=="Video",
           "Existing cut requires one bounded original video track")
    clips=tracks[0].get("children",[])
    insist(len(clips)==len(project["scenes"]),"OTIO exporter omitted/added scene identity")
    for clip,scene in zip(clips,project["scenes"]):
        insist(clip.get("OTIO_SCHEMA")=="Clip.1"and
               clip.get("media_reference",{}).get("OTIO_SCHEMA")=="MissingReference.1"and
               clip.get("metadata",{}).get("motionwright",{}).get("scene_id")==scene["id"],
               "Existing OTIO exporter did not preserve source scene/placeholder")
        source_range=clip.get("source_range",{})
        insist(source_range.get("OTIO_SCHEMA")=="TimeRange.1" and
               source_range.get("duration",{}).get("rate")==24.0 and
               source_range.get("duration",{}).get("value")==float(frames(
                   rational(scene["duration"],"scene duration"),24,"scene duration")),
               "Existing OTIO 24 fps source duration changed")
    return clips

def export_portable(root:Path,request:dict,output:Path)->dict:
    root=root.resolve(strict=True)
    insist(root.is_dir() and not output.exists() and output.parent.is_dir() and
           not output.parent.is_symlink(),"Export requires an existing owner root and a new destination")
    entry=exact(request,{"schema","project","otio","bindings","owner_review"},"Portable export request")
    insist(entry["schema"]==SCHEMA,"Unknown portable OTIO export contract")
    project,project_sha=parse_project(root,entry["project"])
    original=read_json(root,entry["otio"])
    clips=validate_existing_otio(project,original)
    approval=exact(entry["owner_review"],{"reviewer","synthetic_source_allowed"},"Owner review")
    insist(isinstance(approval["reviewer"],str) and
           1<=len(approval["reviewer"].strip())<=200 and
           type(approval["synthetic_source_allowed"])is bool,
           "Explicit source reviewer and synthetic policy required")
    bindings=entry["bindings"]
    insist(isinstance(bindings,list) and len(bindings)<=len(clips),
           "Too many video bindings for original scenes")
    asset_by_id={asset["id"]:asset for asset in project.get("assets",[])}
    scene_by_id={scene["id"]:scene for scene in project["scenes"]}
    verified={}
    media_by_name={}
    total_bytes=0
    for item in bindings:
        bind=exact(item,{"scene_id","asset_id","file","sha256",
                         "rights","source_start_frame"},"Scene-media binding")
        scene_id,asset_id=bind["scene_id"],bind["asset_id"]
        insist(scene_id in scene_by_id and scene_id not in verified,
               "Binding must target exactly one original project scene")
        asset=asset_by_id.get(asset_id)
        insist(asset is not None and asset.get("media_type")=="video/mp4"
               and asset.get("content_sha256")==bind["sha256"]and
               isinstance(bind["sha256"],str) and HEX.fullmatch(bind["sha256"]),
               "Video reference does not match the exact project asset digest")
        rights=exact(bind["rights"],{"owner","license","authorized","redistributable"},
                     "Media redistribution approval")
        insist(rights["authorized"]is True and rights["redistributable"]is True
               and all(isinstance(rights[k],str)and 1<=len(rights[k].strip())<=250
                       for k in ("owner","license")),
               "Portable media may not be copied without owner and redistribution rights")
        if not approval["synthetic_source_allowed"]:
            insist("synthetic" not in rights["license"].lower(),
                   "Synthetic source needs explicit reviewer permission")
        path=owner_file(root,bind["file"],MAX_MEDIA,{".mp4"})
        hash_,size=fingerprint(path,MAX_MEDIA)
        insist(hash_==bind["sha256"],"Owner media was changed after asset admission")
        probe=file_probe(path)
        start=bind["source_start_frame"]
        insist(type(start)is int and 0<=start<=probe["frames"],
               "Owner's original video source offset must be a nonnegative frame")
        count=frames(rational(scene_by_id[scene_id]["duration"],"scene duration"),
                     probe["fps"],"media source duration")
        insist(start+count<=probe["frames"],
               "Real original video does not contain the full requested scene interval")
        relative=f"media/{hash_}.mp4"
        existing=media_by_name.get(relative)
        if existing is None:
            media_by_name[relative]=path
            total_bytes+=size
        else:
            insist(existing.stat().st_size==path.stat().st_size,
                   "Same source digest points to different video bytes")
        insist(total_bytes<=MAX_TOTAL,"Portable media exceeds aggregate 512 MiB budget")
        verified[scene_id]={
            "scene_id":scene_id,"asset_id":asset_id,"source_sha256":hash_,
            "path":relative,"owner":rights["owner"],
            "license":rights["license"],"source_start_frame":start,
            "duration_frames":count,"source_fps":probe["fps"],
            "actual_media_frames":probe["frames"],
            "codec":probe["codec"],"width":probe["width"],"height":probe["height"],
            "bytes":size,
        }
    # Copy the existing source graph. Never invent a binding for an abstract
    # scene without owner-verified, bytes-matched and licensed media.
    for clip,scene in zip(clips,project["scenes"]):
        bound=verified.get(scene["id"])
        if bound:
            clip["media_reference"]={
                "OTIO_SCHEMA":"ExternalReference.1",
                "target_url":bound["path"],
                "available_range":trange(0,bound["actual_media_frames"],bound["source_fps"]),
                "metadata":{"motionwright":{
                    "asset_id":bound["asset_id"],
                    "asset_sha256":bound["source_sha256"],
                    "owner_rights_attested":True,
                    "source_fps":bound["source_fps"],
                    "source_frame_count":bound["actual_media_frames"],
                    "source_revision":project["revision"],
                }},
                "name":scene["name"]
            }
            clip["source_range"]=trange(
                bound["source_start_frame"],bound["duration_frames"],bound["source_fps"])
            clip["metadata"]["motionwright"]["asset_id"]=bound["asset_id"]
            clip["metadata"]["motionwright"]["media_sha256"]=bound["source_sha256"]
            clip["metadata"]["motionwright"]["source_start_frame"]=bound["source_start_frame"]
            clip["metadata"]["motionwright"]["media_rate"]=bound["source_fps"]
        else:
            insist(clip["media_reference"]["OTIO_SCHEMA"]=="MissingReference.1",
                   "An absent owner media binding was silently replaced")
    unresolved=[scene["id"]for scene in project["scenes"] if scene["id"]not in verified]
    inherited=original["metadata"]["motionwright"].get("loss_report",[])
    loss=list(inherited)+[
        "An OTIO ExternalReference preserves bounded real-media source frames, not the native editable Motionwright composition.",
        "Asset rights are owner declarations and must be rechecked by the NLE or receiving workspace.",
        "Only H.264 fixed-frame-rate video and an explicit media mapping are covered; audio tracks, captions, camera rigs, effects, locks, native branch/approval state and unknown NLE import features require separate review.",
    ]
    if unresolved:
        loss.append(f"{len(unresolved)} scene(s) retain MissingReference because no authorized original media was bound; no placeholder media was invented.")
    fidelity=[
        {"property":"scene_order","status":"editable_otio","import_state":"bounded_proposal_only"},
        {"property":"scene_duration","status":"editable_otio","import_state":"bounded_frame_exact_end_trim_only"},
        {"property":"source_video_media","status":"verified_external_reference",
         "import_state":"source_sha_revalidated_no_new_media"},
        {"property":"media_source_in_point","status":"represented_in_otio",
         "import_state":"not_supported_native_scene_offset"},
        {"property":"unbound_scene_media","status":"missing_reference",
         "import_state":"cannot_invent_media","missing_count":len(unresolved)},
        {"property":"native_object_geometry_hierarchy_text_masks","status":"source_project_only",
         "import_state":"not_supported"},
        {"property":"camera_keyframes_effects_and_procedural_controls","status":"source_project_only",
         "import_state":"not_supported"},
        {"property":"source_audio_and_voice_cues","status":"source_project_only",
         "import_state":"not_roundtripped_as_nle_audio"},
        {"property":"captions_transcripts_alignment","status":"source_project_only",
         "import_state":"not_roundtripped"},
        {"property":"human_locks_creative_reviews_branches","status":"source_project_only",
         "import_state":"not_roundtripped"},
        {"property":"runtime_render_authority","status":"not_exported",
         "import_state":"requires_separate_owner_grant"},
        {"property":"publication_rights","status":"not_granted",
         "import_state":"requires_separate_owner_approval"}
    ]
    original["metadata"]["motionwright"].update({
        "property_fidelity":fidelity,
        "export_profile":"portable-media-cut-v2",
        "source_project_sha256":project_sha,
        "original_scene_count":len(clips),
        "verified_media_clip_count":len(verified),
        "unresolved_scene_ids":unresolved,
        "loss_report":loss,
        "native_editable_reimport_supported":False,
        "permission_to_publish":False,
    })
    manifest={
        "schema":SCHEMA,"source_profile":"conservative-cut-v1",
        "output_profile":"portable-media-cut-v2",
        "project_id":project["id"],"generation":project["generation"],
        "revision":project["revision"],"project_sha256":project_sha,
        "scene_order":[scene["id"]for scene in project["scenes"]],
        "bound_media":sorted(verified.values(),key=lambda row:row["scene_id"]),
        "missing_scene_ids":unresolved,
        "source_media_count":len(media_by_name),
        "loss_report":loss,
        "property_fidelity":fidelity,
        "author":"owner_reviewed_portable_media_export",
        "owner_review_declared":approval["reviewer"],
        "editable_native_source_retained":True,
        "portable_otio_source_verified":True,
        "nle_import_verified":False,
        "otio_live_receiver_readback":"NOT_RUN",
        "original_project_unchanged":True,
        "external_program_executed":False,
        "publishing_authorized":False,
    }
    archive_data=json.dumps(original,sort_keys=True,ensure_ascii=False,indent=2).encode()+b"\n"
    manifest["otio_sha256"]=sha_bytes(archive_data)
    with tempfile.TemporaryDirectory(prefix=".mw-portable-cut-",dir=output.parent)as scratch:
        stage=Path(scratch)/"payload"
        stage.mkdir()
        (stage/"media").mkdir()
        (stage/"timeline.otio").write_bytes(archive_data)
        src=owner_file(root,entry["project"],MAX_JSON)
        shutil.copyfile(src,stage/"source-project.json")
        for rel,path in sorted(media_by_name.items()):
            shutil.copyfile(path,stage/rel)
            insist(fingerprint(stage/rel,MAX_MEDIA)[0]==Path(rel).stem,
                   "Source media changed during portable export copy")
        (stage/"bridge-manifest.json").write_text(
            json.dumps(manifest,sort_keys=True,ensure_ascii=False,indent=2)+"\n")
        (stage/"README.md").write_text(
            "# Original media-bound OTIO editorial handoff\n\n"
            "Extract the directory before opening timeline.otio. All video files live under media/ "
            "and use exact SHA-256 names. A receiving NLE may need to relink relative targets. "
            "MissingReference is deliberate if no source has been authorized. "
            "This is not a native Motionwright round-trip, an approval to publish, or a "
            "validated NLE import; see bridge-manifest.json for per-property losses.\n")
        with zipfile.ZipFile(stage/"portable-cut.zip","x",compression=zipfile.ZIP_STORED)as zipped:
            for relative in ["README.md","bridge-manifest.json","source-project.json",
                             "timeline.otio",*sorted(media_by_name)]:
                content=(stage/relative).read_bytes()
                info=zipfile.ZipInfo(relative,date_time=(2026,1,1,0,0,0))
                info.compress_type=zipfile.ZIP_STORED
                info.external_attr=0o644<<16
                zipped.writestr(info,content)
        for key in ("timeline.otio","source-project.json","bridge-manifest.json"):
            insist((stage/key).stat().st_size<MAX_JSON,
                   "Portable source metadata exceeded bounded review profile")
        stage.rename(output)
    return {"status":"OWNER_BOUND_MEDIA_EXPORT_NOT_NLE_VERIFIED",
            "project_sha256":project_sha,"source_scenes":len(clips),
            "verified_media_refs":len(verified),"missing_refs":len(unresolved),
            "unvalidated_nle_claim":False,
            "portable_zip_sha256":fingerprint(output/"portable-cut.zip",MAX_TOTAL+MAX_JSON)[0],
            "owner_publication_authorized":False}

def read_time_range(value:object,label:str)->tuple[int,int,int]:
    item=exact(value,{"OTIO_SCHEMA","duration","start_time"},label)
    insist(item["OTIO_SCHEMA"]=="TimeRange.1",f"{label} requires a native OTIO TimeRange")
    def stamp(value:object)->tuple[int,int]:
        time=exact(value,{"OTIO_SCHEMA","rate","value"},label+" RationalTime")
        insist(time["OTIO_SCHEMA"]=="RationalTime.1"and
               type(time["rate"])in (float,int) and
               type(time["value"])in (float,int)and
               time["rate"]in RATES and
               float(time["value"]).is_integer() and
               0<=time["value"]<=216000,
               "Imported media cut needs an exact integral video-frame time")
        return int(time["value"]),int(time["rate"])
    begin,rate=stamp(item["start_time"])
    duration,other_rate=stamp(item["duration"])
    insist(rate==other_rate and duration>=1,"Imported source range rate/duration mismatch")
    return begin,duration,rate
def import_preview(bundle:Path,edit_name:str,current_project:Path)->dict:
    insist(current_project.is_file() and not current_project.is_symlink(),
           "A live current Project snapshot is required for a bounded edit proposal")
    insist(bundle.is_dir() and not bundle.is_symlink(),
           "A portable media bundle must be an extracted owner-controlled directory")
    root=bundle.resolve(strict=True)
    manifest=read_json(root,"bridge-manifest.json")
    insist(manifest.get("schema")==SCHEMA and
           manifest.get("output_profile")=="portable-media-cut-v2" and
           manifest.get("nle_import_verified") is False and
           manifest.get("publishing_authorized") is False,
           "Portable bridge has an unknown, already-published or forged profile")
    source,sha=parse_project(root,"source-project.json")
    current_digest,current_bytes=fingerprint(current_project,MAX_JSON)
    insist(current_bytes>0 and current_digest==sha,
           "Current source project was edited after portable NLE export; rebase/reexport before importing")
    insist(sha==manifest.get("project_sha256") and
           source["id"]==manifest.get("project_id") and
           source["generation"]==manifest.get("generation") and
           source["revision"]==manifest.get("revision"),
           "Original project/manifest generation or byte digest has changed")
    rows=manifest.get("bound_media",[])
    insist(isinstance(rows,list)and len(rows)<=MAX_SCENES,
           "Portable source media ledger exceeds receiver profile")
    per_scene={}
    original_assets={item.get("id"):item for item in source.get("assets",[]) if isinstance(item,dict)}
    source_scenes={scene["id"]:scene for scene in source["scenes"]}
    for row in rows:
        insist(isinstance(row,dict) and row.get("scene_id") in source_scenes
               and row.get("scene_id") not in per_scene
               and row.get("asset_id") in original_assets,
               "Original video binding no longer identifies a unique project scene/asset")
        original_asset=original_assets[row["asset_id"]]
        insist(original_asset.get("media_type")=="video/mp4"
               and original_asset.get("content_sha256")==row.get("source_sha256"),
               "Imported external media does not belong to the original project's SHA-bound asset")
        local=owner_file(root,row["path"],MAX_MEDIA,{".mp4"})
        insist(fingerprint(local,MAX_MEDIA)[0]==row["source_sha256"],
               "A relinked video source differs from the immutable owner media digest")
        probe=file_probe(local)
        insist(probe["fps"]==row["source_fps"] and probe["frames"]==row["actual_media_frames"],
               "Portable source media stream changed rate or length")
        per_scene[row["scene_id"]]=row
    edited=read_json(root,edit_name)
    insist(edited.get("OTIO_SCHEMA")=="Timeline.1","Edited source must be OTIO Timeline")
    inherited=edited.get("metadata",{}).get("motionwright",{})
    insist(inherited.get("project_id")==source["id"] and
           inherited.get("generation")==source["generation"] and
           inherited.get("revision")==source["revision"] and
           inherited.get("source_project_sha256")==sha and
           inherited.get("export_profile")=="portable-media-cut-v2",
           "Edited OTIO does not belong to this exact original creative revision")
    insist(isinstance(manifest.get("property_fidelity"),list)
           and len(manifest["property_fidelity"])==12 and
           inherited.get("property_fidelity")==manifest["property_fidelity"],
           "NLE cut removed or rewrote the explicit per-property fidelity/loss declarations")
    all_tracks=edited.get("tracks",{}).get("children",[])
    insist(isinstance(all_tracks,list) and len(all_tracks)==1 and
           all_tracks[0].get("OTIO_SCHEMA")=="Track.1" and
           all_tracks[0].get("kind")=="Video" and
           not all_tracks[0].get("effects") and not all_tracks[0].get("markers"),
           "Bounded import accepts one video-only clean editorial track")
    clips=all_tracks[0].get("children",[])
    scenes=source["scenes"]
    original_ids=[scene["id"]for scene in scenes]
    insist(isinstance(clips,list) and len(clips)==len(scenes),
           "Bounded OTIO import cannot add or delete original scenes")
    indexed={scene["id"]:scene for scene in scenes}
    observed_order=[]
    updates={}
    for clip in clips:
        insist(isinstance(clip,dict) and clip.get("OTIO_SCHEMA")=="Clip.1"
               and clip.get("enabled") is True
               and not clip.get("effects") and not clip.get("markers"),
               "NLE effects, disabled clips or new tracks cannot silently become native edits")
        meta=clip.get("metadata",{}).get("motionwright",{})
        scene_id=meta.get("scene_id")
        insist(scene_id in indexed and scene_id not in observed_order,
               "NLE clip lost the exact original unique scene identity")
        observed_order.append(scene_id)
        start,length,rate=read_time_range(clip.get("source_range"),"Imported cut")
        reference=clip.get("media_reference",{})
        bound=per_scene.get(scene_id)
        if bound:
            refmeta=reference.get("metadata",{}).get("motionwright",{})
            insist(reference.get("OTIO_SCHEMA")=="ExternalReference.1"
                   and reference.get("target_url")==bound["path"] and
                   refmeta.get("asset_id")==bound["asset_id"] and
                   refmeta.get("asset_sha256")==bound["source_sha256"] and
                   meta.get("asset_id")==bound["asset_id"] and
                   meta.get("media_sha256")==bound["source_sha256"] and
                   rate==bound["source_fps"],
                   "NLE attempted to substitute a different or unverified source media")
            available_start,available_duration,available_rate=read_time_range(
                reference.get("available_range"),"Original available media interval")
            insist(available_start==0 and
                   available_duration==bound["actual_media_frames"] and
                   available_rate==bound["source_fps"],
                   "NLE available-range claims differ from the actual verified media file")
            insist(start==bound["source_start_frame"],
                   "Native scene has no source-offset/trim-in field; do not silently import an NLE in-point change")
            insist(start+length<=bound["actual_media_frames"],
                   "Edited media source interval exceeds the exact original file")
        else:
            insist(reference.get("OTIO_SCHEMA")=="MissingReference.1"
                   and start==0 and rate==24,
                   "MissingReference cannot be converted to an invented or retimed clip")
        original_duration=rational(indexed[scene_id]["duration"],"original native duration")
        imported_duration=Fraction(length,rate)
        insist(imported_duration>0 and imported_duration<=Fraction(600),
               "Edited cut duration out of native scene bounds")
        if imported_duration!=original_duration:
            updates[scene_id]={"num":str(imported_duration.numerator),
                               "den":str(imported_duration.denominator)}
    insist(set(observed_order)==set(original_ids),
           "NLE omitted, repeated or invented a source scene")
    expected_source_sha=manifest["project_sha256"]
    commands=[]
    working=original_ids.copy()
    for target_index,scene_id in enumerate(observed_order):
        if working[target_index]==scene_id:continue
        source_index=working.index(scene_id)
        working.pop(source_index)
        working.insert(target_index,scene_id)
        commands.append({"type":"move_scene","scene_id":scene_id,
                         "to_index":target_index})
    for scene_id in observed_order:
        if scene_id in updates:
            commands.append({"type":"set_scene_duration","scene_id":scene_id,
                             "duration":updates[scene_id]})
    insist(len(commands)<=MAX_SCENES*2,
           "Too many source revision updates for bounded OTIO cut")
    losses=list(manifest.get("loss_report",[]))
    losses.append(
        "Import produces a native Change proposal only; no source edit, source media in-point, renderer effect or per-property lock is applied by this inspector."
    )
    return {
        "schema":"motionwright.otio-edit-preview/1",
        "source_project_sha256":expected_source_sha,
        "project_id":source["id"],"generation":source["generation"],
        "expected_project_revision":source["revision"],
        "original_scene_order":original_ids,
        "proposed_scene_order":observed_order,
        "native_change_proposals":commands,
        "media_refs_verified":len(per_scene),
        "missing_references_retained":len(scenes)-len(per_scene),
        "loss_report":losses,
        "property_fidelity":manifest["property_fidelity"],
        "edit_imported":False,
        "source_media_relinked":False,
        "owner_revision_commit_required":True,
        "human_review_required":True,
        "rendering_performed":False,
        "semantic_native_roundtrip":"NOT_SUPPORTED",
        "nle_receiver_acceptance":"NOT_VERIFIED",
        "publication_approved":False,
    }
def main()->None:
    parser=argparse.ArgumentParser()
    mode=parser.add_subparsers(dest="command",required=True)
    exporter=mode.add_parser("export")
    exporter.add_argument("--root",type=Path,required=True)
    exporter.add_argument("--request",required=True)
    exporter.add_argument("--output",type=Path,required=True)
    importer=mode.add_parser("preview-import")
    importer.add_argument("--bundle",type=Path,required=True)
    importer.add_argument("--edited",required=True)
    importer.add_argument("--current-project",type=Path,required=True)
    importer.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.command=="export":
            insist(args.root.is_dir()and not args.root.is_symlink(),"Owner root missing")
            result=export_portable(args.root,read_json(args.root,args.request),args.output)
        else:
            insist(not args.output.exists(),"Refusing to overwrite an import proposal")
            result=import_preview(args.bundle,args.edited,args.current_project)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
        print(json.dumps(result,sort_keys=True))
    except (BridgeRefused,ValueError,OSError,KeyError,
            json.JSONDecodeError,subprocess.SubprocessError) as error:
        print("otio-media-refused: "+str(error),file=sys.stderr)
        raise SystemExit(2) from error
if __name__=="__main__":
    main()
