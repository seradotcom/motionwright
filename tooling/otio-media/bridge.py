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
import stat
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit
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
PROBE_TIMEOUT_SECONDS=20

class BridgeRefused(ValueError):pass
def insist(ok:bool,message:str)->None:
    if not ok:raise BridgeRefused(message)
def strict_json(blob:bytes|str,label:str)->dict:
    """Reject ambiguous duplicate source/OTIO fields, including nested objects."""
    def unique(pairs:list[tuple[str,object]])->dict:
        result={}
        for key,value in pairs:
            insist(key not in result,f"Duplicate JSON key in {label}")
            result[key]=value
        return result
    try:
        decoded=json.loads(blob,object_pairs_hook=unique)
    except (ValueError,UnicodeError,TypeError)as error:
        if isinstance(error,BridgeRefused):raise
        raise BridgeRefused(f"Invalid canonical JSON for {label}")from error
    insist(isinstance(decoded,dict),f"{label} must be a JSON object")
    return decoded

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
        remaining=size
        while remaining:
            block=stream.read(min(1024*1024,remaining))
            insist(bool(block),f"File shortened during digest verification: {path.name}")
            digest.update(block)
            remaining-=len(block)
        insist(not stream.read(1),f"File grew during digest verification: {path.name}")
    insist(path.stat().st_size==size,f"File changed during digest verification: {path.name}")
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
    result=strict_json(data,"owner JSON")
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
def _probe_output(args:list[str],deadline:float|None=None)->bytes:
    """Bound readback output while the process runs, including corrupt-file logs."""
    if deadline is None:deadline=time.monotonic()+PROBE_TIMEOUT_SECONDS
    insist(time.monotonic()<deadline,"Media video readback exceeded its bounded time budget")
    try:
        process=subprocess.Popen(args,stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    except OSError:
        raise BridgeRefused("A bounded ffprobe video readback is unavailable")from None
    output=[b"",b""]
    overflow=threading.Event()
    read_failed=threading.Event()
    def stop()->None:
        try:process.kill()
        except OSError:pass
    def collect(stream,index:int)->None:
        try:
            output[index]=stream.read(MAX_JSON+1)
            if len(output[index])>MAX_JSON:
                overflow.set()
                stop()
        except OSError:
            read_failed.set()
            stop()
        finally:
            stream.close()
    readers=[threading.Thread(target=collect,args=(stream,index),daemon=True)
             for index,stream in enumerate((process.stdout,process.stderr))]
    timed_out=False
    started=[]
    with process:
        try:
            for reader in readers:
                reader.start()
                started.append(reader)
            process.wait(timeout=max(0,deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out=True
        finally:
            if process.poll() is None:
                stop()
                process.wait()
            for reader in started:reader.join()
    insist(not timed_out and not overflow.is_set() and not read_failed.is_set(),
           "Media video readback exceeded its bounded time or output budget")
    # ffprobe may return zero after reporting a damaged packet or decoder error.
    # Never include those diagnostics, which can contain private paths, in errors.
    insist(process.returncode==0 and not output[1].strip(),
           "Media video readback failed or reported a decode error")
    return output[0]

def file_probe(media:Path)->dict:
    # Metadata and complete decode share the existing whole-source time budget.
    deadline=time.monotonic()+PROBE_TIMEOUT_SECONDS
    common=["ffprobe","-v","error","-protocol_whitelist","file",
            "-threads","1","-max_alloc",str(MAX_MEDIA),"-select_streams","v:0"]
    observed=strict_json(_probe_output(common+[
        "-show_entries","stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames",
        "-of","json",str(media)],deadline=deadline),"ffprobe")
    streams=observed.get("streams",[])
    insist(isinstance(streams,list) and len(streams)==1 and isinstance(streams[0],dict),
           "Expected exactly one probed video stream")
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
    except (KeyError,ValueError,TypeError,ZeroDivisionError,OverflowError)as error:
        raise BridgeRefused("Media needs measured constant-rate counted frames")from error
    insist(fps==average and fps.denominator==1 and fps.numerator in RATES,
           "Only exact constant 24/25/30/60 fps sources are admitted")
    insist(str(count)==str(item["nb_frames"]) and 1<=count<=216000,
           "Media frame count must be measured and canonical")
    # Only after the existing profile limits pass, decode the entire selected
    # video stream. Container nb_frames is a declaration, not observed media.
    decoded=strict_json(_probe_output(common+[
        "-err_detect","explode","-count_frames","-show_entries",
        "stream=codec_name,width,height,nb_read_frames","-of","json",str(media)],
        deadline=deadline),
        "decoded ffprobe")
    readback=decoded.get("streams",[])
    insist(isinstance(readback,list) and len(readback)==1 and isinstance(readback[0],dict),
           "Expected exactly one completely decoded video stream")
    readback=readback[0]
    insist(all(readback.get(key)==item[key] for key in ("codec_name","width","height")),
           "Decoded video geometry or codec differs from the admitted source profile")
    try:read_count=int(readback["nb_read_frames"])
    except (KeyError,ValueError,TypeError,OverflowError):
        raise BridgeRefused("Media has no complete decoded video frame count")from None
    insist(str(read_count)==str(readback["nb_read_frames"]) and read_count==count,
           "Decoded media frame count differs from the declared source length")
    return {"fps":fps.numerator,"frames":read_count,"width":item["width"],"height":item["height"],
            "codec":"h264"}
def parse_project(root:Path,name:str)->tuple[dict,str]:
    path=owner_file(root,name,MAX_JSON)
    digest,_=fingerprint(path,MAX_JSON)
    project=strict_json(path.read_bytes(),"Project")
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
        # Windows indexers can transiently lock a freshly closed ZIP.
        # Retry only the local atomic rename, never media work or external jobs.
        for attempt in range(8):
            insist(not output.exists(), "Portable OTIO destination appeared during staging")
            try:
                stage.rename(output)
                break
            except PermissionError as error:
                if os.name != "nt" or getattr(error, "winerror", None) != 5 or attempt == 7:
                    raise
                time.sleep(0.12)
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

def _native_cut_commands(original_ids:list[str],observed_order:list[str],
                         updates:dict)->list[dict]:
    """The same bounded Change proposal algorithm for both import profiles."""
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
    return commands

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
        insist(imported_duration<=original_duration,
               "Imported OTIO cut cannot extend native scene duration; only exact end trims are supported")
        if imported_duration!=original_duration:
            updates[scene_id]={"num":str(imported_duration.numerator),
                               "den":str(imported_duration.denominator)}
    insist(set(observed_order)==set(original_ids),
           "NLE omitted, repeated or invented a source scene")
    expected_source_sha=manifest["project_sha256"]
    commands=_native_cut_commands(original_ids,observed_order,updates)
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
KDENLIVE_REQUEST_SCHEMA="motionwright.kdenlive-bound-cut-request/1"
KDENLIVE_PREVIEW_SCHEMA="motionwright.kdenlive-bound-cut-preview/1"
KDENLIVE_PROFILE="kdenlive-bound-video-cut-v1"

class _NleCutLoss(BridgeRefused):
    def __init__(self,code:str,message:str):
        super().__init__(message)
        self.code=code

def _nle_require(ok:bool,code:str,message:str)->None:
    if not ok:raise _NleCutLoss(code,message)

def _nle_fields(value:object,required:set[str],optional:set[str],label:str)->dict:
    _nle_require(isinstance(value,dict) and required<=set(value)
                 and set(value)<=required|optional,"unsupported_schema",
                 f"{label} has unsupported or missing fields")
    return value

def _nle_clean(value:dict,label:str)->None:
    _nle_require(value.get("metadata",{})=={} and value.get("effects",[])==[]
                 and value.get("markers",[])==[] and value.get("enabled",True)is True
                 and value.get("color")is None,"unsupported_native_control",
                 f"{label} contains metadata, effects, markers or disabled content outside the bounded profile")

def _nle_name(value:object)->str:
    _nle_require(isinstance(value,str) and 0<len(value)<=250
                 and not any(ord(c)<32 or ord(c)==127 for c in value),
                 "native_name_unavailable","Native clip name must be observed and bounded")
    return value

def _nle_clips(timeline:dict)->list[dict]:
    """Read a closed Clip.1 / single-reference Clip.2 data profile, never rewrite RAW."""
    _nle_fields(timeline,{"OTIO_SCHEMA","tracks"},
                {"name","metadata","global_start_time"},"NLE Timeline")
    _nle_require(timeline["OTIO_SCHEMA"]=="Timeline.1","unsupported_schema",
                 "Native return is not an admitted Timeline.1")
    _nle_clean(timeline,"NLE Timeline")
    common={"name","metadata","effects","markers","enabled","color","source_range"}
    stack=_nle_fields(timeline["tracks"],{"OTIO_SCHEMA","children"},common,"NLE Stack")
    _nle_require(stack["OTIO_SCHEMA"]=="Stack.1","unsupported_schema","NLE stack schema changed")
    _nle_clean(stack,"NLE Stack")
    tracks=stack["children"]
    _nle_require(isinstance(tracks,list) and len(tracks)==1,"track_set_changed",
                 "Bounded return needs exactly one video track, without audio or subtitle tracks")
    track=_nle_fields(tracks[0],{"OTIO_SCHEMA","kind","children"},common,"NLE Track")
    _nle_require(track["OTIO_SCHEMA"]=="Track.1" and track["kind"]=="Video",
                 "track_set_changed","Bounded return needs an admitted video track")
    _nle_clean(track,"NLE Track")
    clips=track["children"]
    _nle_require(isinstance(clips,list) and len(clips)<=MAX_SCENES,"scene_set_changed",
                 "Native clip set exceeds the bounded scene profile")
    observations=[]
    total=0
    common_rate=None
    for clip in clips:
        _nle_require(isinstance(clip,dict),"unsupported_schema","Native child is not an object")
        schema=clip.get("OTIO_SCHEMA")
        _nle_require(schema!="Gap.1","native_gap_observed",
                     "Native receiver contains a Gap; no missing timeline interval is filled")
        _nle_require(schema in ("Clip.1","Clip.2"),"unsupported_schema",
                     "Native return contains a transition or an unadmitted clip schema")
        required={"OTIO_SCHEMA","name","source_range","enabled"}
        optional={"metadata","effects","markers","color"}
        if schema=="Clip.1":
            _nle_fields(clip,required|{"media_reference"},optional,"NLE Clip.1")
            reference=clip["media_reference"]
        else:
            _nle_fields(clip,required|{"media_references","active_media_reference_key"},
                        optional,"NLE Clip.2")
            refs=clip["media_references"]
            _nle_require(isinstance(refs,dict) and set(refs)=={"DEFAULT_MEDIA"}
                         and clip["active_media_reference_key"]=="DEFAULT_MEDIA",
                         "ambiguous_media_reference","Clip.2 needs one active DEFAULT_MEDIA reference")
            reference=refs["DEFAULT_MEDIA"]
        _nle_clean(clip,"NLE Clip")
        _nle_require(isinstance(reference,dict),"unsupported_schema","Native media reference is absent")
        _nle_require(reference.get("OTIO_SCHEMA")!="MissingReference.1",
                     "native_missing_reference_observed",
                     "Native return contains MissingReference; no media or scene is invented")
        _nle_fields(reference,{"OTIO_SCHEMA","target_url","available_range"},
                    {"name","metadata","available_image_bounds"},"Native external media")
        _nle_require(reference["OTIO_SCHEMA"]=="ExternalReference.1"
                     and reference.get("metadata",{})=={}
                     and reference.get("available_image_bounds")is None,
                     "unsupported_media_reference","Native external media has unadmitted parameters")
        start,duration,rate=read_time_range(clip["source_range"],"Native source interval")
        available_start,available_duration,available_rate=read_time_range(
            reference["available_range"],"Native available media")
        _nle_require(available_start==0 and available_rate==rate,
                     "source_clock_changed","Native available range has a different clock or start")
        _nle_require(common_rate is None or common_rate==rate,"source_clock_changed",
                     "Native timeline mixes source frame rates")
        common_rate=rate
        total+=duration
        observations.append({"name":_nle_name(clip["name"]),"target":reference["target_url"],
                             "start":start,"duration":duration,"fps":rate,
                             "available_frames":available_duration,"schema":schema})
    if not observations:
        raise _NleCutLoss("scene_set_changed","Native receiver omitted every source scene")
    for composition in (stack,track):
        if composition.get("source_range")is not None:
            _nle_require(read_time_range(composition["source_range"],"Native composition interval")
                         ==(0,total,common_rate),"timeline_interval_changed",
                         "Native composition range does not equal its complete contiguous cut")
    if timeline.get("global_start_time")is not None:
        stamp=timeline["global_start_time"]
        global_interval={"OTIO_SCHEMA":"TimeRange.1","start_time":stamp,
                         "duration":rtc(1,common_rate)}
        _nle_require(read_time_range(global_interval,"Native global clock")== (0,1,common_rate),
                     "timeline_interval_changed",
                     "Native global clock is shifted or has a different rate")
    return observations

def _nle_resolve_plain_path(path:Path,message:str,*,directory:bool=False)->Path:
    try:
        # Inspect from the filesystem root down before resolve can erase a
        # junction. Windows reparse points need not use symlink mode.
        for item in (*reversed(path.parents),path):
            observed=item.lstat()
            insist(not stat.S_ISLNK(observed.st_mode)
                   and not getattr(observed,"st_file_attributes",0)&0x400,message)
        insist(not directory or stat.S_ISDIR(observed.st_mode),message)
        return path.resolve(strict=True)
    except (OSError,RuntimeError,ValueError):
        raise BridgeRefused(message)from None

def _nle_local_media(raw_parent:Path,target:object,roots:tuple[Path,...])->Path:
    _nle_require(isinstance(target,str) and 1<=len(target)<=250
                 and not any(ord(c)<32 for c in target),"unsafe_media_target",
                 "Native media target is absent or outside path bounds")
    parsed=urlsplit(target)
    if parsed.scheme=="file":
        _nle_require(not parsed.netloc and not parsed.query and not parsed.fragment,
                     "unsafe_media_target","Only local file URLs without authority are admitted")
        text=unquote(parsed.path)
        if os.name=="nt" and re.match(r"^/[A-Za-z]:/",text):text=text[1:]
    elif os.name=="nt" and re.match(r"^[A-Za-z]:[\\/]",target):
        text=target
    else:
        _nle_require(not parsed.scheme and not parsed.netloc and not parsed.query
                     and not parsed.fragment,"unsafe_media_target",
                     "Network/protocol media references are not admitted")
        text=target
    _nle_require(bool(text) and not any(ord(c)<32 for c in text),
                 "unsafe_media_target","Decoded native path contains an invalid character")
    relative=Path(text)
    _nle_require(".." not in relative.parts and not text.startswith(("//","\\\\")),
                 "unsafe_media_target","Native target traversal or network path is not admitted")
    path=relative if relative.is_absolute() else raw_parent/relative
    path=path.absolute()
    _nle_require(any(path.is_relative_to(root)for root in roots),"unauthorized_media_root",
                 "Native media target lies outside the backend-authorized roots")
    try:
        path=_nle_resolve_plain_path(path,"Native media target cannot be resolved safely")
    except BridgeRefused:
        raise _NleCutLoss("unsafe_media_target",
            "Native media target contains a link, reparse point or unavailable path")from None
    _nle_require(any(path.is_relative_to(root)for root in roots),"unauthorized_media_root",
                 "Resolved native media target lies outside the backend-authorized roots")
    _nle_require(path.suffix.lower()==".mp4","unsupported_media_reference",
                 "Native source must remain the admitted MP4")
    return path

def preview_kdenlive_cut(bundle:Path,raw_root:Path,request:dict,current_project:Path,
                         *,authorized_media_roots:tuple[Path,...])->dict:
    """Source-bound data inspection only: no NLE receipt, grant or Project mutation.

    A backend resolves the expected hashes and roots from CAS/authority. Neither
    RAW supplies UUID authority. Initial RAW supplies corroborating names only;
    a backend native-receiver receipt is still required before claiming receipt.
    """
    request=exact(request,{"schema","capsule_manifest_sha256","received_otio",
                           "received_sha256","edited_otio","edited_sha256"},
                  "Kdenlive bounded cut request")
    insist(request["schema"]==KDENLIVE_REQUEST_SCHEMA,"Unknown bounded NLE cut request")
    insist(bundle.is_dir() and not bundle.is_symlink() and raw_root.is_dir()
           and not raw_root.is_symlink(),"Source capsule and RAW roots must be owner-controlled directories")
    bundle=bundle.resolve(strict=True)
    raw_root=raw_root.resolve(strict=True)
    insist(isinstance(authorized_media_roots,tuple) and 1<=len(authorized_media_roots)<=8,
           "Backend must supply one to eight authorized local media roots")
    roots=[]
    for root in authorized_media_roots:
        insist(isinstance(root,Path) and root.is_absolute(),
               "Invalid backend-authorized media root")
        roots.append(_nle_resolve_plain_path(root,
            "Invalid backend-authorized media root",directory=True))
    roots=tuple(roots)
    snapshots={}
    def read_bound(root:Path,name:str,expected:object)->tuple[dict,Path]:
        insist(isinstance(expected,str) and HEX.fullmatch(expected),"Expected CAS SHA-256 is missing")
        path=owner_file(root,name,MAX_JSON)
        with path.open("rb")as stream:data=stream.read(MAX_JSON+1)
        insist(0<len(data)<=MAX_JSON and sha_bytes(data)==expected,
               "Source capsule or native RAW differs from its expected CAS bytes")
        snapshots[path]=(expected,len(data),MAX_JSON)
        try:return strict_json(data,"bound native cut data"),path
        except RecursionError:raise BridgeRefused("Native JSON nesting exceeds the bounded parser")from None
    manifest,manifest_path=read_bound(bundle,"bridge-manifest.json",request["capsule_manifest_sha256"])
    original,original_path=read_bound(bundle,"timeline.otio",manifest.get("otio_sha256"))
    # Reuse the established source/rights/fidelity/current-Project/media checks.
    # Its legacy contract and raw metadata requirements are not relaxed.
    legacy=import_preview(bundle,"timeline.otio",current_project)
    source,source_sha=parse_project(bundle,"source-project.json")
    for path in (bundle/"source-project.json",current_project):
        hash_,size=fingerprint(path,MAX_JSON)
        insist(hash_==source_sha,"Current or original Project changed during cut inspection")
        snapshots[path]=(hash_,size,MAX_JSON)
    received,received_path=read_bound(raw_root,request["received_otio"],request["received_sha256"])
    edited,edited_path=read_bound(raw_root,request["edited_otio"],request["edited_sha256"])
    original_ids=[scene["id"]for scene in source["scenes"]]
    result={
        "schema":KDENLIVE_PREVIEW_SCHEMA,"profile":KDENLIVE_PROFILE,
        "capsule_manifest_sha256":request["capsule_manifest_sha256"],
        "original_otio_sha256":manifest["otio_sha256"],"source_project_sha256":source_sha,
        "received_otio_sha256":request["received_sha256"],"edited_otio_sha256":request["edited_sha256"],
        "project_id":source["id"],"generation":source["generation"],
        "expected_project_revision":source["revision"],"original_scene_order":original_ids,
        "original_missing_scene_ids":manifest.get("missing_scene_ids",[]),
        "native_change_proposals":[],"loss_report":list(legacy["loss_report"]),
        "property_fidelity":legacy["property_fidelity"],
        "nle_receiver_acceptance":"NOT_VERIFIED","native_project_reopened":"NOT_VERIFIED",
        "native_final_frames_verified":False,"edit_imported":False,
        "source_media_relinked":False,"raw_files_rewritten":False,
        "owner_revision_commit_required":True,"human_review_required":True,
        "semantic_native_roundtrip":"NOT_SUPPORTED","publication_approved":False,
    }
    probes={}
    unique_bytes={}
    def media(path:Path,expected_sources:set[str]|None=None)->tuple[str,dict]:
        hash_,size=fingerprint(path,MAX_MEDIA)
        _nle_require(expected_sources is None or hash_ in expected_sources,
                     "unbound_source_or_inpoint",
                     "Native source digest is not bound by the immutable capsule")
        previous=snapshots.get(path)
        insist(previous is None or previous==(hash_,size,MAX_MEDIA),
               "Physical media changed during native cut inspection")
        snapshots[path]=(hash_,size,MAX_MEDIA)
        unique_bytes[hash_]=size
        insist(sum(unique_bytes.values())<=MAX_TOTAL,"Native cut source media exceeds aggregate budget")
        if path not in probes:
            probes[path]=file_probe(path)
            insist(fingerprint(path,MAX_MEDIA)==(hash_,size),
                   "Physical media changed while its frames were decoded")
        return hash_,probes[path]
    try:
        source_by_id={scene["id"]:scene for scene in source["scenes"]}
        keys={}
        geometry=None
        for row in manifest["bound_media"]:
            path=owner_file(bundle,row["path"],MAX_MEDIA,{".mp4"})
            hash_,probe=media(path)
            _nle_require(hash_==row["source_sha256"] and
                         all(probe[k]==row[m]for k,m in (("fps","source_fps"),
                         ("frames","actual_media_frames"),("width","width"),("height","height"),
                         ("codec","codec"))) and snapshots[path][1]==row["bytes"],
                         "capsule_media_changed","Capsule media observation disagrees with its immutable ledger")
            frame_count=frames(rational(source_by_id[row["scene_id"]]["duration"],"source scene duration"),
                               probe["fps"],"original native cut duration")
            _nle_require(type(row["source_start_frame"])is int and row["source_start_frame"]>=0
                         and row["duration_frames"]==frame_count
                         and row["source_start_frame"]+frame_count<=probe["frames"],
                         "capsule_interval_changed","Capsule does not bind the complete original source interval")
            shape=(probe["fps"],probe["width"],probe["height"])
            _nle_require(geometry is None or shape==geometry,"source_clock_changed",
                         "Bounded native cut requires one source rate and geometry")
            geometry=shape
            key=(hash_,probe["fps"],row["source_start_frame"])
            _nle_require(key not in keys,"ambiguous_source_identity",
                         "Repeated media with the same original inpoint cannot identify a scene uniquely")
            keys[key]=row
        first=_nle_clips(received)
        last=_nle_clips(edited)
        result["received_clip_count"]=len(first)
        result["edited_clip_count"]=len(last)
        _nle_require(not result["original_missing_scene_ids"] and len(keys)==len(original_ids),
                     "original_missing_reference_not_receivable",
                     "Original MissingReference cannot be received as a complete native cut; omissions are retained as rejected loss")
        def map_raw(observations:list[dict],raw_path:Path,initial:bool)->list[dict]:
            mapped=[]
            used=set()
            for item in observations:
                path=_nle_local_media(raw_path.parent,item["target"],roots)
                hash_,probe=media(path,{key[0]for key in keys})
                key=(hash_,item["fps"],item["start"])
                row=keys.get(key)
                _nle_require(row is not None,"unbound_source_or_inpoint",
                             "Native source digest, clock or original inpoint has no unique capsule binding")
                scene_id=row["scene_id"]
                _nle_require(scene_id not in used,"duplicate_native_instance",
                             "Native return duplicated a source instance; no second scene is invented")
                used.add(scene_id)
                _nle_require((probe["fps"],probe["width"],probe["height"])==geometry
                             and probe["frames"]==row["actual_media_frames"]==item["available_frames"],
                             "native_media_range_changed","Native available range differs from completely decoded source media")
                _nle_require(0<item["duration"]<=row["duration_frames"]
                             and item["start"]+item["duration"]<=probe["frames"],
                             "native_end_trim_exceeded","Native return extends the authored source scene")
                _nle_require(not initial or item["duration"]==row["duration_frames"],
                             "initial_receipt_already_edited","Initial native observation already changes a source duration")
                mapped.append({**item,"scene_id":scene_id,"asset_id":row["asset_id"],
                               "source_sha256":hash_,"resolved_path":str(path),
                               "original_target":row["path"]})
            _nle_require(used==set(original_ids),"scene_set_changed",
                         "Native receiver omitted, added or replaced an original scene")
            return mapped
        first=map_raw(first,received_path,True)
        _nle_require([item["scene_id"]for item in first]==original_ids,
                     "initial_receipt_already_edited","Initial native observation already changes source order")
        last=map_raw(last,edited_path,False)
        first_by_id={item["scene_id"]:item for item in first}
        updates={}
        mapping=[]
        for item in last:
            initial=first_by_id[item["scene_id"]]
            _nle_require(item["name"]==initial["name"],"native_clip_renamed",
                         "Native clip name changed after receipt; names are corroboration, never UUID authority")
            duration=Fraction(item["duration"],item["fps"])
            if duration!=rational(source_by_id[item["scene_id"]]["duration"],"original scene duration"):
                updates[item["scene_id"]]={"num":str(duration.numerator),"den":str(duration.denominator)}
            mapping.append({"scene_id":item["scene_id"],"asset_id":item["asset_id"],
                            "source_sha256":item["source_sha256"],"source_fps":item["fps"],
                            "original_inpoint":item["start"],"duration_frames":item["duration"],
                            "native_name_observed":item["name"],"original_target":item["original_target"],
                            "received_target":initial["target"],"returned_target":item["target"],
                            "same_source_bytes_relocated":item["resolved_path"]!=initial["resolved_path"]})
        order=[item["scene_id"]for item in last]
        result.update(status="BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED",proposed_scene_order=order,
                      native_change_proposals=_native_cut_commands(original_ids,order,updates),
                      source_identity_mapping=mapping,media_refs_verified=len(mapping),
                      same_source_bytes_relocated=any(row["same_source_bytes_relocated"]for row in mapping))
        result["loss_report"].extend([
            "Native OTIO metadata does not preserve Motionwright identity; this inspector derives it from the immutable capsule and a unique physical SHA/FPS/original-inpoint map.",
            "Initial native names are corroborating RAW data only; neither RAW file authenticates a Kdenlive execution, native reopen, final-frame fidelity or authority to commit.",
            "Same-byte media relocation is recorded separately and never changes the Project asset ledger or grants publication rights.",
        ])
    except _NleCutLoss as error:
        result.update(status="REJECTED_LOSS",rejection_code=error.code,
                      native_change_proposals=[],rejection_reason=str(error))
        result["loss_report"].append(str(error))
    for path,(expected,size,budget)in snapshots.items():
        insist(fingerprint(path,budget)==(expected,size),
               "Source capsule, RAW, Project or media changed during bounded cut inspection")
    return result

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
    native=mode.add_parser("preview-kdenlive-cut")
    native.add_argument("--bundle",type=Path,required=True)
    native.add_argument("--raw-root",type=Path,required=True)
    native.add_argument("--request",required=True)
    native.add_argument("--current-project",type=Path,required=True)
    native.add_argument("--media-root",type=Path,action="append",required=True)
    native.add_argument("--output",type=Path,required=True)
    alab=mode.add_parser("external-alab-readonly")
    alab.add_argument("--request",type=Path,required=True)
    alab.add_argument("--evidence-root",type=Path,required=True)
    alab.add_argument("--ffprobe",type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.command=="external-alab-readonly":
            from alab_readonly import external_alab_readonly, Unknown
            try:
                result=external_alab_readonly(args.request,args.evidence_root,args.ffprobe)
            except Unknown as error:
                print("otio-alab-unknown-no-retry: "+str(error),file=sys.stderr)
                raise SystemExit(3) from error
            print(json.dumps(result,sort_keys=True))
            raise SystemExit(0 if result["status"]=="READONLY_METADATA_ORIGINALS_RETAINED"
                             else 3 if result["status"]=="UNKNOWN_NO_RETRY" else 2)
        elif args.command=="export":
            insist(args.root.is_dir()and not args.root.is_symlink(),"Owner root missing")
            result=export_portable(args.root,read_json(args.root,args.request),args.output)
        elif args.command=="preview-kdenlive-cut":
            insist(not args.output.exists() and args.output.parent.is_dir()
                   and not args.output.parent.is_symlink(),"A new native cut proposal destination is required")
            result=preview_kdenlive_cut(args.bundle,args.raw_root,
                read_json(args.raw_root,args.request),args.current_project,
                authorized_media_roots=tuple(args.media_root))
            with args.output.open("x",encoding="utf-8")as stream:
                stream.write(json.dumps(result,sort_keys=True,indent=2)+"\n")
        else:
            insist(not args.output.exists(),"Refusing to overwrite an import proposal")
            result=import_preview(args.bundle,args.edited,args.current_project)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
        print(json.dumps(result,sort_keys=True))
        if args.command=="preview-kdenlive-cut" and result["status"]=="REJECTED_LOSS":
            raise SystemExit(3)
    except (BridgeRefused,ValueError,OSError,KeyError,
            json.JSONDecodeError,subprocess.SubprocessError) as error:
        print("otio-media-refused: "+str(error),file=sys.stderr)
        raise SystemExit(2) from error
if __name__=="__main__":
    main()
