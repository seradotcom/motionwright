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

# The existing shared custodian owns the sole held-FD read implementation.
# Resolve the trusted module location, never a source-data pathname.
_TOOLING_DIRECTORY = str(Path(__file__).resolve().parents[1])
if _TOOLING_DIRECTORY not in sys.path:
    sys.path.insert(0, _TOOLING_DIRECTORY)

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
def capture_original(path:Path,budget:int)->tuple[bytes,dict]:
    """Capture immutable bytes and their original held-handle observation once.

    The preliminary checks retain the bridge's existing nonempty/budget error
    contract. Only the shared regular/capture_regular result supplies content
    and identity; consumers never reopen this pathname to parse or copy it.
    """
    insist(path.is_file() and not path.is_symlink(),f"Missing or unsafe file: {path.name}")
    size=path.stat().st_size
    insist(0<size<=budget,f"File exceeds byte budget: {path.name}")
    import ci_private_evidence as custody
    from mlt_official_runtime_intake import IntakeError
    try:
        raw,observed=custody.regular(path.absolute(),budget)
    except (custody.CustodyRejected,IntakeError) as error:
        raise BridgeRefused(f"File changed or became unsafe during retained capture: {path.name}") from error
    insist(0<len(raw)<=budget,f"File exceeds byte budget: {path.name}")
    return raw,observed

def fingerprint(path:Path,budget:int)->tuple[str,int]:
    raw,observed=capture_original(path,budget)
    return observed["sha256"],len(raw)
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
    # This is a pathname locator, not an observation or content receipt.
    # Reading consumers must use capture_original and keep its immutable bytes.
    insist(path.is_file() and not path.is_symlink(),f"Missing or unsafe file: {path.name}")
    insist(0<path.stat().st_size<=budget,f"File exceeds byte budget: {path.name}")
    return path

def capture_owner_original(root:Path,name:str,budget:int,
                           extension:set[str]|None=None)->tuple[Path,bytes,dict]:
    path=owner_file(root,name,budget,extension)
    raw,observed=capture_original(path,budget)
    return path,raw,observed
def read_json(root:Path,name:str)->dict:
    _,data,_=capture_owner_original(root,name,MAX_JSON)
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
def _probe_output(args:list[str],deadline:float|None=None,*,
                  input_bytes:bytes|None=None,capture:dict|None=None)->bytes:
    """Use the existing decoder executor for one original readback attempt.

    This adapter keeps the bridge's closed public error messages. The identical
    original executor exception remains their cause; interrupts remain the same
    object. A capture is an in-memory original, not a persisted custody receipt.
    """
    from manim_native_readback import ReadbackError, run_bounded
    if deadline is None:deadline=time.monotonic()+PROBE_TIMEOUT_SECONDS
    remaining=deadline-time.monotonic()
    insist(remaining>0,"Media video readback exceeded its bounded time budget")
    insist(input_bytes is None or(type(input_bytes)is bytes and
           0<len(input_bytes)<=MAX_MEDIA),"Media video stdin exceeds its byte budget")
    insist(capture is None or(isinstance(capture,dict)and not capture),
           "Media video readback capture must be new")
    original={}
    primary=None
    executor_failure=False
    decoded=None
    try:
        decoded,_=run_bounded(args,MAX_JSON,remaining,input_bytes=input_bytes,
                             capture=original,max_stdin_bytes=MAX_MEDIA)
        # Decoder warnings/errors are private originals. An exit zero with a
        # diagnostic cannot establish an admitted clean media observation.
        insist(not original["stderr"].strip(),
               "Media video readback failed or reported a decode error")
    except BaseException as error:
        primary=error
        executor_failure=not isinstance(error,BridgeRefused)
    try:
        original["argv"]=list(args)
        original["limits"]={"stdin_bytes":MAX_MEDIA,"stdout_bytes":MAX_JSON,
                            "stderr_bytes":65536,"work_deadline":deadline,
                            "cleanup_allowance_seconds":10}
        if capture is not None:capture.update(original)
    except BaseException as custody_error:
        if primary is None:
            primary=custody_error
            executor_failure=False
        else:
            try:primary.add_note("Secondary media readback custody failure: "+
                                 type(custody_error).__name__)
            except BaseException:pass
    if primary is not None:
        try:primary.otio_probe_original=original
        except BaseException:pass
        if executor_failure and isinstance(primary,(ReadbackError,OSError,subprocess.SubprocessError)):
            # Preserve the established BridgeRefused/public-diagnostic boundary,
            # not the old `from None` loss of the original supervision failure.
            closed=BridgeRefused("A bounded ffprobe video readback failed or reported a decode error")
            closed.otio_probe_original=original
            raise closed from primary
        raise primary
    return decoded

def file_probe(media:Path,*,original_bytes:bytes|None=None)->dict:
    # The same immutable original feeds both existing phases. Filesystem
    # capture has no general wall-time guarantee; no decoder starts after the
    # existing shared work deadline. A pipe-incompatible MP4 is refused, never
    # retried through its mutable pathname or transcoded into another source.
    deadline=time.monotonic()+PROBE_TIMEOUT_SECONDS
    if original_bytes is None:original_bytes,_=capture_original(media,MAX_MEDIA)
    insist(type(original_bytes)is bytes and 0<len(original_bytes)<=MAX_MEDIA,
           "Media video stdin exceeds its byte budget")
    common=["ffprobe","-v","error","-protocol_whitelist","pipe",
            "-threads","1","-max_alloc",str(MAX_MEDIA),"-select_streams","v:0"]
    # Packet counting drains the metadata phase without a second full video
    # decode. Packet counts never substitute for the decoded frame count below.
    observed=strict_json(_probe_output(common+[
        "-count_packets","-show_entries","stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames",
        "-of","json","pipe:0"],deadline=deadline,input_bytes=original_bytes),"ffprobe")
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
        "stream=codec_name,width,height,nb_read_frames","-of","json","pipe:0"],
        deadline=deadline,input_bytes=original_bytes),
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
def _capture_project(root:Path,name:str)->tuple[dict,str,bytes,Path]:
    path,raw,observed=capture_owner_original(root,name,MAX_JSON)
    project=strict_json(raw,"Project")
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
    return project,observed["sha256"],raw,path

def parse_project(root:Path,name:str)->tuple[dict,str]:
    project,digest,_,_=_capture_project(root,name)
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

def export_portable(root:Path,request:dict,output:Path,
                    *,retained_alab:_RetainedAlabObservation|None=None,
                    source_notices:dict|None=None)->dict:
    root=root.resolve(strict=True)
    insist(root.is_dir() and not output.exists() and output.parent.is_dir() and
           not output.parent.is_symlink(),"Export requires an existing owner root and a new destination")
    notice_bytes=_validate_alab_source_notices(source_notices,retained_alab)
    entry=exact(request,{"schema","project","otio","bindings","owner_review"},"Portable export request")
    insist(entry["schema"]==SCHEMA,"Unknown portable OTIO export contract")
    input_originals={}
    def remember_original(path:Path,digest:str,size:int,budget:int)->None:
        previous=input_originals.get(path)
        insist(previous is None or previous[:2]==(digest,size),
               "Repeated original path changed between captures")
        # The first byte identity remains authoritative. A later use can only
        # tighten its read budget; it cannot rebase an earlier captured input.
        input_originals[path]=(digest,size,budget if previous is None else min(previous[2],budget))
    project,project_sha,project_original,project_path=_capture_project(root,entry["project"])
    remember_original(project_path,project_sha,len(project_original),MAX_JSON)
    otio_path,otio_original,otio_observed=capture_owner_original(root,entry["otio"],MAX_JSON)
    remember_original(otio_path,otio_observed["sha256"],len(otio_original),MAX_JSON)
    original=strict_json(otio_original,"owner JSON")
    clips=validate_existing_otio(project,original)
    approval=exact(entry["owner_review"],{"reviewer","synthetic_source_allowed"},"Owner review")
    insist(isinstance(approval["reviewer"],str) and
           1<=len(approval["reviewer"].strip())<=200 and
           type(approval["synthetic_source_allowed"])is bool,
           "Explicit source reviewer and synthetic policy required")
    bindings=entry["bindings"]
    if retained_alab is not None:
        _validate_alab_project_bindings(project,bindings,retained_alab)
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
        insist(asset is not None and asset.get("media_type")==("video/mp4" if retained_alab is None else "video/quicktime")
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
        extension=_admitted_video_profile(asset,retained_alab)
        path,media_original,media_observed=capture_owner_original(root,bind["file"],MAX_MEDIA,{extension})
        hash_,size=media_observed["sha256"],len(media_original)
        insist(hash_==bind["sha256"],"Owner media was changed after asset admission")
        remember_original(path,hash_,size,MAX_MEDIA)
        probe=_admitted_video_probe(path,retained_alab,original_bytes=media_original)
        insist(fingerprint(path,MAX_MEDIA)==(hash_,size),
               "Owner media changed during its existing source observation")
        start=bind["source_start_frame"]
        insist(type(start)is int and 0<=start<=probe["frames"],
               "Owner's original video source offset must be a nonnegative frame")
        count=frames(rational(scene_by_id[scene_id]["duration"],"scene duration"),
                     probe["fps"],"media source duration")
        insist(start+count<=probe["frames"],
               "Real original video does not contain the full requested scene interval")
        relative=f"media/{hash_}{extension}"
        existing=media_by_name.get(relative)
        if existing is None:
            media_by_name[relative]=(path,media_original)
            total_bytes+=size
        else:
            insist(existing[1]==media_original,
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
    if retained_alab is not None:
        loss.append("This closed benchmark cut selects two original ALab MOV occurrences. The whole original OTIO and its unselected references remain opaque/unavailable outside this cut; no MissingReference or replacement media was fabricated for them. Retained native media observations are reused by exact identity, without a new decode or receiver claim.")
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
        "export_profile":_retained_output_profile(retained_alab),
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
        "output_profile":_retained_output_profile(retained_alab),
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
        "external_program_executed":retained_alab is None and bool(verified),
        "publishing_authorized":False,
    }
    if retained_alab is not None:
        manifest["retained_source_binding"]=_retained_source_binding(retained_alab)
        original["metadata"]["motionwright"]["retained_source_binding"]=_retained_source_binding(retained_alab)
        manifest["original_source_notices"]=_alab_source_notice_records()
        original["metadata"]["motionwright"]["original_source_notices"]=_alab_source_notice_records()
    archive_data=json.dumps(original,sort_keys=True,ensure_ascii=False,indent=2).encode()+b"\n"
    manifest["otio_sha256"]=sha_bytes(archive_data)
    with tempfile.TemporaryDirectory(prefix=".mw-portable-cut-",dir=output.parent)as scratch:
        stage=Path(scratch)/"payload"
        stage.mkdir()
        (stage/"media").mkdir()
        readme=(
            "# Original media-bound OTIO editorial handoff\n\n"
            "Extract the directory before opening timeline.otio. All video files live under media/ "
            "and use exact SHA-256 names. A receiving NLE may need to relink relative targets. "
            "MissingReference is deliberate if no source has been authorized. "
            "This is not a native Motionwright round-trip, an approval to publish, or a "
            "validated NLE import; see bridge-manifest.json for per-property losses.\n")
        # Preserve the prior platform newline form of these two generated text
        # files. The original Project, MOV/MP4 and notices remain exact bytes.
        manifest_text=json.dumps(manifest,sort_keys=True,ensure_ascii=False,indent=2)+"\n"
        portable_originals={
            "timeline.otio":archive_data,
            "source-project.json":project_original,
            "bridge-manifest.json":manifest_text.replace("\n",os.linesep).encode("utf-8"),
            "README.md":readme.replace("\n",os.linesep).encode("utf-8"),
            **notice_bytes,
            **{name:raw for name,(_,raw)in media_by_name.items()},
        }
        for relative,raw in portable_originals.items():
            (stage/relative).write_bytes(raw)
        with zipfile.ZipFile(stage/"portable-cut.zip","x",compression=zipfile.ZIP_STORED)as zipped:
            for relative in ["README.md","bridge-manifest.json","source-project.json",
                             "timeline.otio",*sorted(notice_bytes),*sorted(media_by_name)]:
                info=zipfile.ZipInfo(relative,date_time=(2026,1,1,0,0,0))
                info.compress_type=zipfile.ZIP_STORED
                info.external_attr=0o644<<16
                zipped.writestr(info,portable_originals[relative])
        for relative,raw in portable_originals.items():
            insist(fingerprint(stage/relative,len(raw))==(sha_bytes(raw),len(raw)),
                   "Portable output differs from its captured original bytes")
        for key in ("timeline.otio","source-project.json","bridge-manifest.json"):
            insist(len(portable_originals[key])<MAX_JSON,
                   "Portable source metadata exceeded bounded review profile")
        for path,(digest,size,budget)in input_originals.items():
            insist(fingerprint(path,budget)==(digest,size),
                   "Original source changed after its portable capture")
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

def import_preview(bundle:Path,edit_name:str,current_project:Path,
                   *,retained_alab:_RetainedAlabObservation|None=None)->dict:
    insist(current_project.is_file() and not current_project.is_symlink(),
           "A live current Project snapshot is required for a bounded edit proposal")
    insist(bundle.is_dir() and not bundle.is_symlink(),
           "A portable media bundle must be an extracted owner-controlled directory")
    root=bundle.resolve(strict=True)
    manifest=read_json(root,"bridge-manifest.json")
    insist(manifest.get("schema")==SCHEMA and
           manifest.get("output_profile")==_retained_output_profile(retained_alab) and
           manifest.get("nle_import_verified") is False and
           manifest.get("publishing_authorized") is False,
           "Portable bridge has an unknown, already-published or forged profile")
    _require_retained_source_binding(manifest,retained_alab)
    _require_alab_source_notices(root,manifest,retained_alab)
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
        insist(original_asset.get("media_type")==("video/mp4" if retained_alab is None else "video/quicktime")
               and original_asset.get("content_sha256")==row.get("source_sha256"),
               "Imported external media does not belong to the original project's SHA-bound asset")
        extension=_admitted_video_profile(original_asset,retained_alab)
        if retained_alab is not None:
            insist(row["path"]==f"media/{row['source_sha256']}{extension}",
                   "Portable media path no longer matches its admitted container profile")
        local=owner_file(root,row["path"],MAX_MEDIA,{extension})
        media_original,media_observed=capture_original(local,MAX_MEDIA)
        insist(media_observed["sha256"]==row["source_sha256"],
               "A relinked video source differs from the immutable owner media digest")
        probe=_admitted_video_probe(local,retained_alab,original_bytes=media_original)
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
           inherited.get("export_profile")==_retained_output_profile(retained_alab),
           "Edited OTIO does not belong to this exact original creative revision")
    _require_retained_source_binding(inherited,retained_alab)
    _require_alab_source_notices(root,inherited,retained_alab)
    if retained_alab is not None:
        bindings=[{"scene_id":row["scene_id"],"asset_id":row["asset_id"],
                   "sha256":row["source_sha256"],"source_start_frame":row["source_start_frame"]}
                  for row in rows]
        _validate_alab_project_bindings(source,bindings,retained_alab)
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

def _nle_local_media(raw_parent:Path,target:object,roots:tuple[Path,...],
                     *,retained_alab:_RetainedAlabObservation|None=None)->Path:
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
    expected_extension=".mp4" if retained_alab is None else ".mov"
    _nle_require(retained_alab is None or type(retained_alab)is _RetainedAlabObservation,
                 "unsupported_media_reference","Native retained source needs validated original observations")
    _nle_require(path.suffix.lower()==expected_extension,"unsupported_media_reference",
                 "Native source must retain the admitted container extension")
    return path

def preview_kdenlive_cut(bundle:Path,raw_root:Path,request:dict,current_project:Path,
                         *,authorized_media_roots:tuple[Path,...],
                         retained_alab:_RetainedAlabObservation|None=None)->dict:
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
    def remember_snapshot(path:Path,digest:str,size:int,budget:int)->None:
        previous=snapshots.get(path)
        insist(previous is None or previous[:2]==(digest,size),
               "Repeated original path changed during bounded cut inspection")
        snapshots[path]=(digest,size,budget if previous is None else min(previous[2],budget))
    def read_bound(root:Path,name:str,expected:object)->tuple[dict,Path]:
        insist(isinstance(expected,str) and HEX.fullmatch(expected),"Expected CAS SHA-256 is missing")
        path,data,observed=capture_owner_original(root,name,MAX_JSON)
        insist(0<len(data)<=MAX_JSON and observed["sha256"]==expected,
               "Source capsule or native RAW differs from its expected CAS bytes")
        remember_snapshot(path,expected,len(data),MAX_JSON)
        try:return strict_json(data,"bound native cut data"),path
        except RecursionError:raise BridgeRefused("Native JSON nesting exceeds the bounded parser")from None
    manifest,manifest_path=read_bound(bundle,"bridge-manifest.json",request["capsule_manifest_sha256"])
    original,original_path=read_bound(bundle,"timeline.otio",manifest.get("otio_sha256"))
    # Reuse the established source/rights/fidelity/current-Project/media checks.
    # Its legacy contract and raw metadata requirements are not relaxed.
    legacy=import_preview(bundle,"timeline.otio",current_project,retained_alab=retained_alab)
    source,source_sha=parse_project(bundle,"source-project.json")
    for path in (bundle/"source-project.json",current_project):
        hash_,size=fingerprint(path,MAX_JSON)
        insist(hash_==source_sha,"Current or original Project changed during cut inspection")
        remember_snapshot(path,hash_,size,MAX_JSON)
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
        media_original,media_observed=capture_original(path,MAX_MEDIA)
        hash_,size=media_observed["sha256"],len(media_original)
        _nle_require(expected_sources is None or hash_ in expected_sources,
                     "unbound_source_or_inpoint",
                     "Native source digest is not bound by the immutable capsule")
        remember_snapshot(path,hash_,size,MAX_MEDIA)
        unique_bytes[hash_]=size
        insist(sum(unique_bytes.values())<=MAX_TOTAL,"Native cut source media exceeds aggregate budget")
        if path not in probes:
            probes[path]=_admitted_video_probe(path,retained_alab,original_bytes=media_original)
            insist(fingerprint(path,MAX_MEDIA)==(hash_,size),
                   "Physical media changed while its frames were decoded")
        return hash_,probes[path]
    try:
        source_by_id={scene["id"]:scene for scene in source["scenes"]}
        keys={}
        geometry=None
        for row in manifest["bound_media"]:
            extension=".mp4" if retained_alab is None else ".mov"
            path=owner_file(bundle,row["path"],MAX_MEDIA,{extension})
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
                path=_nle_local_media(raw_path.parent,item["target"],roots,retained_alab=retained_alab)
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

EXTERNAL_OTIO_INSPECTION_SCHEMA="motionwright.external-otio-source-inspection/1"
_EXTERNAL_MAX_DEPTH=64
_EXTERNAL_MAX_NODES=65536
_EXTERNAL_MAX_ROWS=1024
_EXTERNAL_OCCURRENCE=re.compile(
    r"/tracks/children/(0|[1-9][0-9]{0,3})/children/(0|[1-9][0-9]{0,5})"
)


def inspect_external_otio(source:bytes,expected_sha256:str,
                          occurrence_pointers:list[str])->dict:
    """Inspect owned CAS bytes without resolving references or adopting a cut.

    The caller obtains bytes and expected digest from its existing CAS boundary.
    Matching a supplied digest proves byte identity only, never caller authority.
    Selected occurrences are returned in source order. Declared RationalTimes,
    available ranges, names and foreign metadata never establish file offsets,
    handles, observed media, native scene identity or permission to execute.
    The complete source remains the caller's original immutable bytes; opaque
    properties are addressed within those bytes and are not rewritten here.
    """
    insist(type(source)is bytes and 0<len(source)<=MAX_JSON,
           "External OTIO requires bounded immutable source bytes")
    insist(type(expected_sha256)is str and HEX.fullmatch(expected_sha256) is not None,
           "External OTIO requires an expected source digest")
    insist(sha_bytes(source)==expected_sha256,"External OTIO source digest changed")
    insist(type(occurrence_pointers)is list
           and 1<=len(occurrence_pointers)<=MAX_SCENES
           and all(type(pointer)is str for pointer in occurrence_pointers),
           "External OTIO selection must contain bounded occurrence pointers")
    insist(len(set(occurrence_pointers))==len(occurrence_pointers),
           "External OTIO selection repeats an occurrence")
    selected=[]
    for pointer in occurrence_pointers:
        match=_EXTERNAL_OCCURRENCE.fullmatch(pointer)
        insist(match is not None,"External OTIO occurrence pointer is not canonical")
        selected.append((int(match[1]),int(match[2]),pointer))
    selected.sort()
    try:
        decoded_source=source.decode("utf-8")
    except UnicodeDecodeError:
        raise BridgeRefused("External OTIO source is not valid UTF-8")from None
    try:
        document=strict_json(decoded_source,"external OTIO")
    except RecursionError:
        raise BridgeRefused("External OTIO exceeds its structural budget")from None

    pending=[(document,0)]
    visited=0
    while pending:
        value,depth=pending.pop()
        visited+=1
        insist(depth<=_EXTERNAL_MAX_DEPTH and visited<=_EXTERNAL_MAX_NODES,
               "External OTIO exceeds its structural budget")
        if isinstance(value,(dict,list)):
            children=value.values() if isinstance(value,dict) else value
            insist(visited+len(pending)+len(value)<=_EXTERNAL_MAX_NODES,
                   "External OTIO exceeds its structural budget")
            pending.extend((child,depth+1)for child in children)
        elif type(value)is float:
            insist(value==value and abs(value)<=sys.float_info.max,
                   "External OTIO contains a non-finite JSON number")

    insist(document.get("OTIO_SCHEMA")=="Timeline.1",
           "External OTIO inspector requires Timeline.1")
    stack=document.get("tracks")
    insist(isinstance(stack,dict) and stack.get("OTIO_SCHEMA")=="Stack.1",
           "External OTIO inspector requires a tracks Stack.1")
    tracks=stack.get("children")
    insist(isinstance(tracks,list) and len(tracks)<=MAX_SCENES,
           "External OTIO track collection exceeds the inspection profile")

    opaque=[]
    blockers=[]
    seen_opaque=set()
    seen_blockers=set()
    def issue(pointer:str,code:str)->None:
        key=(pointer,code)
        if key not in seen_blockers:
            insist(len(blockers)<_EXTERNAL_MAX_ROWS,
                   "External OTIO diagnostic inventory exceeds its budget")
            seen_blockers.add(key)
            blockers.append({"pointer":pointer,"code":code})
    def preserve(pointer:str,reason:str,**details:object)->None:
        key=(pointer,reason)
        if key not in seen_opaque:
            insist(len(pointer)<=1024 and len(opaque)<_EXTERNAL_MAX_ROWS,
                   "External OTIO opaque inventory exceeds its budget")
            seen_opaque.add(key)
            opaque.append({"pointer":pointer,"reason":reason,**details})
    def child_pointer(pointer:str,key:str)->str:
        return pointer+"/"+key.replace("~","~0").replace("/","~1")
    def text(value:object,pointer:str,maximum:int=250)->str|None:
        if value is None:return None
        if isinstance(value,str) and len(value)<=maximum:
            return value
        preserve(pointer,"uninterpreted_text_property")
        issue(pointer,"text_property_outside_profile")
        return None
    def properties(value:dict,pointer:str,known:set[str])->None:
        for key in value:
            if key not in known:
                at=child_pointer(pointer,key)
                preserve(at,"uninterpreted_property")
                issue(at,"property_requires_adoption_review")
        for key in ("metadata","effects","markers","color"):
            if key not in value:continue
            item=value[key]
            empty=(isinstance(item,dict) and not item) if key=="metadata" else (
                (isinstance(item,list) and not item) if key in ("effects","markers")
                else item is None)
            if not empty:
                at=child_pointer(pointer,key)
                preserve(at,"uninterpreted_metadata_or_control")
                issue(at,"property_requires_adoption_review")
        if "enabled" in value and value["enabled"]is not True:
            at=child_pointer(pointer,"enabled")
            preserve(at,"disabled_or_uninterpreted_state")
            issue(at,"occurrence_or_composition_not_enabled")
    def stamp(value:object,pointer:str)->dict|None:
        if (isinstance(value,dict)
                and set(value)=={"OTIO_SCHEMA","rate","value"}
                and value["OTIO_SCHEMA"]=="RationalTime.1"
                and type(value["rate"])in (int,float)
                and type(value["value"])in (int,float)):
            return value.copy()
        preserve(pointer,"uninterpreted_rational_time")
        issue(pointer,"rational_time_unavailable")
        return None
    def interval(value:object,pointer:str)->tuple[dict|None,bool]:
        if value is None:
            issue(pointer,"declared_range_unavailable")
            return None,False
        if (not isinstance(value,dict)
                or set(value)!={"OTIO_SCHEMA","duration","start_time"}
                or value.get("OTIO_SCHEMA")!="TimeRange.1"):
            preserve(pointer,"uninterpreted_time_range")
            issue(pointer,"declared_range_unavailable")
            return None,False
        start=stamp(value["start_time"],pointer+"/start_time")
        duration=stamp(value["duration"],pointer+"/duration")
        if start is None or duration is None:return None,False
        declared={"OTIO_SCHEMA":"TimeRange.1","start_time":start,"duration":duration}
        try:
            read_time_range(declared,"External declared source range")
        except (BridgeRefused,OverflowError):
            issue(pointer,"range_outside_existing_cut_profile")
            return declared,False
        return declared,True

    properties(document,"",{"OTIO_SCHEMA","name","metadata","tracks","global_start_time"})
    common={"OTIO_SCHEMA","name","metadata","source_range","effects","markers",
            "enabled","color","children"}
    properties(stack,"/tracks",common)
    if stack.get("source_range")is not None:
        preserve("/tracks/source_range","composition_range_not_projected")
        issue("/tracks/source_range","composition_range_requires_adoption_review")
    global_time=document.get("global_start_time")
    if global_time is not None:
        global_time=stamp(global_time,"/global_start_time")
    selected_by_track={}
    for track_index,clip_index,pointer in selected:
        insist(track_index<len(tracks),"External OTIO selected track is absent")
        track=tracks[track_index]
        insist(isinstance(track,dict) and isinstance(track.get("children"),list),
               "External OTIO selected track has no inspectable children")
        insist(clip_index<len(track["children"]),"External OTIO selected occurrence is absent")
        selected_by_track.setdefault(track_index,set()).add(clip_index)
    for track_index,track in enumerate(tracks):
        at=f"/tracks/children/{track_index}"
        if track_index not in selected_by_track:
            preserve(at,"unselected_track")
            continue
        properties(track,at,common|{"kind"})
        if track.get("OTIO_SCHEMA")!="Track.1" or track.get("kind")!="Video":
            preserve(at,"track_outside_video_cut_profile")
            issue(at,"selected_track_outside_video_cut_profile")
        if track.get("source_range")is not None:
            preserve(at+"/source_range","composition_range_not_projected")
            issue(at+"/source_range","composition_range_requires_adoption_review")
        omitted=len(track["children"])-len(selected_by_track[track_index])
        if omitted:
            preserve(at+"/children","unselected_occurrences",count=omitted)

    observations=[]
    for track_index,clip_index,pointer in selected:
        track=tracks[track_index]
        clip=track["children"][clip_index]
        observation={"pointer":pointer,"track_pointer":f"/tracks/children/{track_index}",
                     "track_kind":text(track.get("kind"),f"/tracks/children/{track_index}/kind"),
                     "schema":None,"name":None,"declared_source_range":None,
                     "range_supported_by_existing_cut_parser":False,
                     "active_reference":None}
        observations.append(observation)
        if not isinstance(clip,dict):
            preserve(pointer,"uninterpreted_occurrence")
            issue(pointer,"occurrence_outside_clip_profile")
            continue
        schema=clip.get("OTIO_SCHEMA")
        observation["schema"]=text(schema,pointer+"/OTIO_SCHEMA")
        observation["name"]=text(clip.get("name"),pointer+"/name")
        declared,supported=interval(clip.get("source_range"),pointer+"/source_range")
        observation["declared_source_range"]=declared
        observation["range_supported_by_existing_cut_parser"]=supported
        if schema not in ("Clip.1","Clip.2"):
            preserve(pointer,"uninterpreted_occurrence")
            issue(pointer,"occurrence_outside_clip_profile")
            continue
        clip_fields={"OTIO_SCHEMA","name","metadata","source_range","effects",
                     "markers","enabled","color"}
        reference=None
        reference_pointer=None
        if schema=="Clip.1":
            properties(clip,pointer,clip_fields|{"media_reference"})
            reference=clip.get("media_reference")
            reference_pointer=pointer+"/media_reference"
        else:
            properties(clip,pointer,clip_fields|{"media_references","active_media_reference_key"})
            refs=clip.get("media_references")
            active=clip.get("active_media_reference_key")
            if (not isinstance(refs,dict) or not isinstance(active,str)
                    or active not in refs or not 1<=len(active)<=250):
                preserve(pointer+"/media_references","unresolved_active_reference")
                issue(pointer+"/active_media_reference_key","active_reference_unavailable")
                continue
            reference=refs[active]
            reference_pointer=child_pointer(pointer+"/media_references",active)
            for key in refs:
                if key!=active:
                    preserve(child_pointer(pointer+"/media_references",key),
                             "inactive_media_reference")
        if not isinstance(reference,dict):
            preserve(reference_pointer,"uninterpreted_media_reference")
            issue(reference_pointer,"media_reference_unavailable")
            continue
        ref_schema=text(reference.get("OTIO_SCHEMA"),reference_pointer+"/OTIO_SCHEMA")
        properties(reference,reference_pointer,{"OTIO_SCHEMA","name","metadata",
                   "target_url","available_range","available_image_bounds"})
        declared_available=None
        if reference.get("available_range")is not None:
            declared_available,_=interval(reference["available_range"],
                                          reference_pointer+"/available_range")
        if reference.get("available_image_bounds")is not None:
            preserve(reference_pointer+"/available_image_bounds","image_bounds_not_projected")
            issue(reference_pointer+"/available_image_bounds","property_requires_adoption_review")
        target=None
        if ref_schema=="ExternalReference.1":
            target=text(reference.get("target_url"),reference_pointer+"/target_url",2048)
            if not target:
                issue(reference_pointer+"/target_url","external_target_unavailable")
            issue(reference_pointer,"media_requires_authorized_observation")
        elif ref_schema=="MissingReference.1":
            issue(reference_pointer,"missing_reference_observed")
        else:
            preserve(reference_pointer,"uninterpreted_media_reference")
            issue(reference_pointer,"media_reference_outside_profile")
        observation["active_reference"]={
            "pointer":reference_pointer,"schema":ref_schema,"target_url":target,
            "declared_available_range":declared_available}
    issue("/tracks","media_not_observed")
    issue("/tracks","media_origin_unknown")
    result={
        "schema":EXTERNAL_OTIO_INSPECTION_SCHEMA,
        "status":"SOURCE_INSPECTED_NOT_ADOPTED",
        "source":{"sha256":expected_sha256,"bytes":len(source),
                  "schema":"Timeline.1","name":text(document.get("name"),"/name"),
                  "declared_global_start_time":global_time},
        "observations":observations,"opaque_regions":opaque,"blockers":blockers,
        "verified_media":False,"media_origin":"unknown"}
    insist(len(json.dumps(result,ensure_ascii=True,allow_nan=False).encode("ascii"))<=MAX_JSON,
           "External OTIO inspection output exceeds its byte budget")
    return result


# This is an explicitly closed benchmark profile, not generic MOV support.
ALAB_RETAINED_PROFILE="alab-two-retained-mov-24-v1"
_ALAB_RETAINED_ORIGINALS=(
    ("otio",218949,"6c90cbc64000e5de1d6450c95b1f5845db2862c053fa2591d02d370505f3bd28"),
    ("readback",107354,"2c5739cb39dc7233b955428450939585f68b743cb418d0d2afb1c12f79023c24"),
    ("container0",1301,"390c157862ede63b25583502eaac6b3603e4ed9632630267315d2b68bc6d8218"),
    ("container1",1304,"52a988b9db2d9d771cbf42e2b24f026b772651a9936459b3859a0f7940baf21a"),
)
_ALAB_RETAINED_MEDIA=(
    ("/tracks/children/0/children/0","ALab_h264_MOVs/mk020_0010.mov",7627921,
     "86c40f8271339a4bab948d11ddbdd172588816df5ff52d056bd4e54378069e1a",1002,52),
    ("/tracks/children/0/children/1","ALab_h264_MOVs/mk020_0020.mov",20227644,
     "08aac3e29ea980e80993aa6baaef1b83b4b0598c3ae95685434886a1c326b2f6",1008,182),
)


_ALAB_SOURCE_NOTICES=(
    ("license","ALab-LICENSE.original.txt",2622,
     "4263d9c4ad26b95bda3398c406ef9787f696f236fb88ab509693371983174021"),
    ("readme","ALab-README.original.txt",8729,
     "e5964177d9826d6f80c3318a01e3404d3bc2f24d8cdd143a4b566b0b3ff295a0"),
    ("selection_notice","ALab-SELECTION-NOTICE.original.txt",902,
     "0d90946e0b723a499bf516275d3407100d022de9c76935bcc6fa368f70daa376"),
)

def _alab_source_notice_records()->list[dict]:
    return [{"role":role,"file":name,"bytes":size,"sha256":digest}
            for role,name,size,digest in _ALAB_SOURCE_NOTICES]

def _validate_alab_source_notices(notices:object,
                                  retained:_RetainedAlabObservation|None)->dict:
    if retained is None:
        insist(notices is None,"Ordinary media profile does not admit an ALab notice contract")
        return {}
    insist(type(retained)is _RetainedAlabObservation and type(notices)is dict
           and set(notices)=={row[0]for row in _ALAB_SOURCE_NOTICES},
           "Closed ALab export requires its original license, README and selection notice")
    result={}
    for role,name,size,digest in _ALAB_SOURCE_NOTICES:
        raw=notices[role]
        insist(type(raw)is bytes and len(raw)==size and sha_bytes(raw)==digest,
               "ALab original source notice identity changed: "+role)
        result[name]=raw
    return result

def _require_alab_source_notices(root:Path,value:dict,
                                 retained:_RetainedAlabObservation|None)->None:
    if retained is None:
        insist("original_source_notices"not in value,
               "Ordinary media profile cannot self-admit an ALab notice scope")
        return
    insist(type(retained)is _RetainedAlabObservation
           and value.get("original_source_notices")==_alab_source_notice_records(),
           "ALab original source notice roster differs from its fixed benchmark contract")
    raw={}
    for role,name,size,digest in _ALAB_SOURCE_NOTICES:
        _,raw[role],observed=capture_owner_original(root,name,size)
        insist((observed["sha256"],len(raw[role]))==(digest,size),
               "ALab original source notice changed during import readback: "+role)
    _validate_alab_source_notices(raw,retained)


class _RetainedAlabObservation:
    """Immutable validated originals; an in-process value, never a source grant.

    No JSON boolean can select this path. Only the closed benchmark constructor
    below is called by the existing owned caller. JSON receipts are derived
    observations and cannot be passed back as this value.
    """
    __slots__=("_originals","_original_pins","_media","_descriptor","_sealed")

    def __init__(self,originals:tuple[bytes,...],original_pins:tuple,
                 media:tuple,descriptor:bytes):
        object.__setattr__(self,"_originals",originals)
        object.__setattr__(self,"_original_pins",original_pins)
        object.__setattr__(self,"_media",media)
        object.__setattr__(self,"_descriptor",descriptor)
        object.__setattr__(self,"_sealed",True)

    def __setattr__(self,name,value):
        raise AttributeError("Retained ALab observations are immutable")

    def descriptor(self)->dict:
        return strict_json(self._descriptor,"retained ALab derived descriptor")

    def probe(self,media:Path)->dict:
        raw,_=capture_original(media,MAX_MEDIA)
        return self._probe_original_bytes(media,raw)

    def _probe_original_bytes(self,media:Path,raw:bytes)->dict:
        insist(media.suffix.lower()==".mov",
               "Retained ALab source must remain a MOV, without extension relabelling")
        insist(type(raw)is bytes and 0<len(raw)<=MAX_MEDIA,
               "Retained ALab source requires bounded captured original bytes")
        digest,size=sha_bytes(raw),len(raw)
        rows=[row for row in self._media if row[3]==digest and row[2]==size]
        insist(len(rows)==1,"MOV bytes do not match one of the admitted retained ALab originals")
        row=rows[0]
        return {"fps":24,"frames":row[5],"width":1920,"height":1080,"codec":"h264"}


def _alab_exact_fraction(value:object,label:str)->Fraction:
    exact(value,{"num","den"},label)
    insist(type(value["num"])is int and type(value["den"])is int
           and value["den"]>0 and abs(value["num"])<=2**53
           and value["den"]<=2**53,label+" is not a bounded rational number")
    result=Fraction(value["num"],value["den"])
    insist(result.numerator==value["num"] and result.denominator==value["den"],
           label+" is not in canonical rational form")
    return result


def _alab_ndf24(value:object)->int:
    insist(type(value)is str and re.fullmatch(r"[0-9]{2}:[0-9]{2}:[0-9]{2}:[0-9]{2}",value),
           "ALab origin requires an exact 24fps non-drop-frame timecode")
    hour,minute,second,frame=map(int,value.split(":"))
    insist(hour<24 and minute<60 and second<60 and frame<24,
           "ALab timecode is outside the closed 24fps clock")
    return ((hour*60+minute)*60+second)*24+frame


def _alab_bounded_json(raw:bytes,label:str)->dict:
    try:result=strict_json(raw,label)
    except RecursionError:raise BridgeRefused(label+" exceeds its structural budget")from None
    pending=[(result,0)]
    count=0
    while pending:
        value,depth=pending.pop()
        count+=1
        insist(depth<=64 and count<=65536,label+" exceeds its structural budget")
        if isinstance(value,(dict,list)):
            insist(count+len(pending)+len(value)<=65536,label+" exceeds its structural budget")
            pending.extend((child,depth+1)for child in (value.values()if isinstance(value,dict)else value))
        elif type(value)is float:
            insist(value==value and abs(value)<=sys.float_info.max,label+" contains a non-finite number")
    return result


def _admit_alab_retained_originals(originals:tuple[bytes,...],original_pins:tuple,
                                   media_pins:tuple)->_RetainedAlabObservation:
    """Shared validator; synthetic pin values are used only by its unit tests."""
    insist(type(originals)is tuple and len(originals)==4
           and type(original_pins)is tuple and len(original_pins)==4,
           "ALab requires all four independently admitted structured originals")
    insist(all(type(row)is tuple and len(row)==3 for row in original_pins)
           and tuple(row[0]for row in original_pins)==("otio","readback","container0","container1"),
           "ALab original roles changed")
    for raw,(_,size,digest)in zip(originals,original_pins):
        insist(type(raw)is bytes and type(size)is int and 0<size<=MAX_JSON
               and type(digest)is str and HEX.fullmatch(digest)
               and len(raw)==size and sha_bytes(raw)==digest,
               "ALab original differs from its independently admitted bytes")
    insist(type(media_pins)is tuple and len(media_pins)==2
           and all(type(row)is tuple and len(row)==6 for row in media_pins)
           and tuple((row[0],row[1],row[4],row[5])for row in media_pins)
               ==tuple((row[0],row[1],row[4],row[5])for row in _ALAB_RETAINED_MEDIA),
           "ALab selected source occurrence profile changed")
    for row in media_pins:
        insist(len(row)==6 and type(row[2])is int and 0<row[2]<=MAX_MEDIA
               and type(row[3])is str and HEX.fullmatch(row[3]),
               "ALab source identity is not bounded")
    insist(media_pins[0][3]!=media_pins[1][3],"ALab selected source identities are ambiguous")
    # Every size/digest is checked before the first parser invocation.
    inspection=inspect_external_otio(originals[0],original_pins[0][2],
                                     [row[0]for row in media_pins])
    readback=_alab_bounded_json(originals[1],"ALab native readback original")
    containers=[_alab_bounded_json(raw,"ALab container original")for raw in originals[2:]]
    exact(readback,{"schema","status","media","new_render","nle_opened",
                   "canonical_scene_ids_created","assets_adopted","unknown_replayed",
                   "human_acceptance","formal_acceptance","source_origin","runtime",
                   "inputs_stable","elapsed_seconds","ended_at_utc"},"ALab native readback")
    insist(readback["schema"]=="motionwright.a4.alab-native-readback/1"
           and readback["status"]=="PASS_TWO_RETAINED_ORIGINALS_NATIVE_DECODE"
           and readback["inputs_stable"]is True
           and readback["source_origin"]=="NOT_OBSERVED"
           and all(readback[name]is False for name in ("new_render","nle_opened",
               "canonical_scene_ids_created","assets_adopted","unknown_replayed",
               "human_acceptance","formal_acceptance")),
           "ALab readback cannot acquire unobserved source or execution claims")
    insist(type(readback["media"])is list and len(readback["media"])==2,
           "ALab requires the two original native media observations")
    selected=[]
    for index,(pin,clip,media,container)in enumerate(zip(
            media_pins,inspection["observations"],readback["media"],containers)):
        pointer,target,size,digest,origin,count=pin
        insist(clip["pointer"]==pointer and clip["schema"]=="Clip.2"
               and clip["track_kind"]=="Video"
               and clip["range_supported_by_existing_cut_parser"]is True
               and clip["active_reference"]=={
                   "pointer":pointer+"/media_references/DEFAULT_MEDIA",
                   "schema":"ExternalReference.1","target_url":target,
                   "declared_available_range":None}
               and clip["declared_source_range"]==trange(origin,count,24),
               "ALab source selection/reference/range differs from the original handoff")
        exact(media,{"source_path","source_sha256","source_bytes","decoded_frame_count",
                     "decoded_pts","pixel_samples","source_origin","container_metadata_original",
                     "physical_observation"},"ALab native media observation")
        insist(media["source_sha256"]==digest and type(media["source_bytes"])is int
               and media["source_bytes"]==size and type(media["decoded_frame_count"])is int
               and media["decoded_frame_count"]==count and media["source_origin"]=="NOT_OBSERVED",
               "ALab native media identity or measured frame count changed")
        original=exact(media["container_metadata_original"],{"path","bytes","sha256"},
                       "ALab container-original binding")
        insist(type(original["bytes"])is int and original["bytes"]==original_pins[index+2][1]
               and original["sha256"]==original_pins[index+2][2],
               "ALab container role is not bound to the original native observation")
        exact(container,{"format","duration_microseconds","start_time_microseconds",
                         "metadata","streams"},"ALab container")
        metadata=container["metadata"]
        insist(type(metadata)is dict and container["format"]=="mov,mp4,m4a,3gp,3g2,mj2"
               and metadata.get("major_brand")=="qt  "
               and metadata.get("compatible_brands")=="qt  "
               and type(container["start_time_microseconds"])is int
               and container["start_time_microseconds"]==0,
               "ALab source is not the retained QuickTime container")
        streams=container["streams"]
        insist(type(streams)is list and len(streams)==2
               and all(type(stream)is dict for stream in streams)
               and [stream.get("type")for stream in streams]==["video","data"]
               and [stream.get("index")for stream in streams]==[0,1],
               "ALab requires the retained video and timecode streams")
        video,tmcd=streams
        codec=exact(video.get("codec"),{"name","profile","width","height","sample_rate","channels"},
                    "ALab video codec")
        insist(codec["name"]=="h264" and codec["profile"]=="Main"
               and type(codec["width"])is int and codec["width"]==1920
               and type(codec["height"])is int and codec["height"]==1080,
               "ALab retained video codec/geometry changed")
        for stream in streams:
            insist(_alab_exact_fraction(stream.get("time_base"),"ALab stream timebase")==Fraction(1,2400)
                   and type(stream.get("start_time"))is int and stream["start_time"]==0
                   and type(stream.get("duration"))is int and stream["duration"]==count*100,
                   "ALab retained stream clock changed")
        insist(type(video.get("container_declared_frames"))is int
               and video["container_declared_frames"]==count
               and type(tmcd.get("container_declared_frames"))is int
               and tmcd["container_declared_frames"]==1
               and type(tmcd.get("metadata"))is dict
               and tmcd["metadata"].get("handler_name")=="Timecode Handler",
               "ALab container frame/timecode declaration changed")
        format_timecode=metadata.get("timecode")
        insist(tmcd["metadata"].get("timecode")==format_timecode
               and _alab_ndf24(format_timecode)==origin,
               "ALab format/tmcd timecode does not establish the selected source origin")
        observation=exact(media["physical_observation"],{"width","height","codec","average_rate",
            "container_declared_frames","declared_count_matches_decoded","first_presentation_time",
            "last_presentation_time","cadence_matches_average_rate","timecode_fields","source_origin"},
            "ALab physical observation")
        insist(type(observation["width"])is int and observation["width"]==1920
               and type(observation["height"])is int and observation["height"]==1080
               and observation["codec"]=="h264"
               and _alab_exact_fraction(observation["average_rate"],"ALab measured rate")==24
               and type(observation["container_declared_frames"])is int
               and observation["container_declared_frames"]==count
               and observation["declared_count_matches_decoded"]is True
               and observation["cadence_matches_average_rate"]is True
               and observation["source_origin"]=="NOT_OBSERVED"
               and _alab_exact_fraction(observation["first_presentation_time"],"ALab first PTS")==0
               and _alab_exact_fraction(observation["last_presentation_time"],"ALab last PTS")==Fraction(count-1,24),
               "ALab measured source clock/geometry changed")
        pts=media["decoded_pts"]
        insist(type(pts)is list and len(pts)==count,
               "ALab retained decode is not a complete frame sequence")
        for frame,row in enumerate(pts):
            exact(row,{"index","pts","time_base","presentation_time"},"ALab decoded frame clock")
            insist(type(row["index"])is int and row["index"]==frame
                   and type(row["pts"])is int and row["pts"]==frame*100
                   and _alab_exact_fraction(row["time_base"],"ALab decoded timebase")==Fraction(1,2400)
                   and _alab_exact_fraction(row["presentation_time"],"ALab decoded PTS")==Fraction(frame,24),
                   "ALab retained frame clock is missing, duplicated, reordered or nonuniform")
        # Pixel samples corroborate three instants; no per-frame pixel claim.
        samples=media["pixel_samples"]
        insist(type(samples)is list and len(samples)==3
               and all(type(sample)is dict for sample in samples)
               and [sample.get("index")for sample in samples]==[0,count//2,count-1],
               "ALab three retained RGB samples changed")
        for sample in samples:
            exact(sample,{"index","presentation_time","width","height","rgb24_bytes",
                          "rgb24_sha256","source_pixel_format","nonconstant_rgb"},"ALab RGB sample")
            insist(type(sample["index"])is int and type(sample["width"])is int
                   and sample["width"]==1920 and type(sample["height"])is int
                   and sample["height"]==1080 and type(sample["rgb24_bytes"])is int
                   and sample["rgb24_bytes"]==1920*1080*3
                   and type(sample["rgb24_sha256"])is str and HEX.fullmatch(sample["rgb24_sha256"])
                   and sample["source_pixel_format"]=="yuv420p" and sample["nonconstant_rgb"]is True
                   and _alab_exact_fraction(sample["presentation_time"],"ALab sample PTS")==Fraction(sample["index"],24),
                   "ALab retained RGB sample descriptor changed")
        selected.append({"pointer":pointer,"target_url":target,"source_sha256":digest,"source_bytes":size,
                         "media_type":"video/quicktime","source_fps":24,"width":1920,"height":1080,
                         "codec":"h264","source_origin_frame":origin,"source_start_frame":0,
                         "duration_frames":count,"actual_media_frames":count,
                         "container_timecode":format_timecode,"rgb_samples_retained":3,
                         "all_decoded_frame_pixel_hashes":False})
    derived={"schema":"motionwright.alab-retained-original-cut/1","profile":ALAB_RETAINED_PROFILE,
             "status":"RETAINED_MEDIA_ORIGIN_DERIVED_NOT_ADOPTED",
             "originals":[{"role":role,"bytes":size,"sha256":digest}for role,size,digest in original_pins],
             "source_inspection":inspection,"selected":selected,
             "source_origin_basis":"matching original OTIO range, retained complete video PTS and matching format/tmcd 24fps non-drop-frame timecodes",
             "historical_source_origin":"NOT_OBSERVED","historical_readback_rewritten":False,
             "new_decode":False,"new_render":False,"assets_adopted":False,
             "canonical_scene_ids_created":False,"nle_opened":False,"formal_acceptance":False,
             "remaining_source_references":"opaque/unavailable for this selected two-occurrence profile",
             "later_edit_proposal":{"occurrence":media_pins[1][0],"from_frames":182,"to_frames":170,"applied":False}}
    encoded=json.dumps(derived,sort_keys=True,ensure_ascii=True,allow_nan=False).encode("ascii")
    insist(len(encoded)<=MAX_JSON,"ALab retained-origin result exceeds its bounded profile")
    return _RetainedAlabObservation(originals,original_pins,media_pins,encoded)


def admit_alab_retained_originals(source:bytes,readback:bytes,container0:bytes,
                                  container1:bytes)->_RetainedAlabObservation:
    """Join the four fixed benchmark originals without native/media IO.

    This does not grant caller authority, adopt media or promote the historical
    native observation. Its independent source binding is fixed above; a TAR or
    manifest cannot supply its own admission.
    """
    return _admit_alab_retained_originals((source,readback,container0,container1),
                                         _ALAB_RETAINED_ORIGINALS,_ALAB_RETAINED_MEDIA)


def _admitted_video_profile(asset:dict,retained:_RetainedAlabObservation|None)->str:
    if retained is None:
        insist(asset.get("media_type")=="video/mp4","Ordinary portable media requires its admitted MP4 asset")
        return ".mp4"
    insist(type(retained)is _RetainedAlabObservation
           and asset.get("media_type")=="video/quicktime"
           and asset.get("content_sha256")in {row[3]for row in retained._media},
           "Retained ALab media requires one of its original QuickTime assets")
    return ".mov"


def _admitted_video_probe(media:Path,retained:_RetainedAlabObservation|None,
                          *,original_bytes:bytes|None=None)->dict:
    if retained is None:return file_probe(media,original_bytes=original_bytes)
    insist(type(retained)is _RetainedAlabObservation,"Retained media requires validated original observation bytes")
    if original_bytes is not None:return retained._probe_original_bytes(media,original_bytes)
    return retained.probe(media)


def _retained_output_profile(retained:_RetainedAlabObservation|None)->str:
    insist(retained is None or type(retained)is _RetainedAlabObservation,
           "Retained profile cannot be admitted by a JSON flag or foreign object")
    return "portable-media-cut-v2" if retained is None else "alab-two-retained-mov-cut-v1"


def _retained_source_binding(retained:_RetainedAlabObservation)->dict:
    insist(type(retained)is _RetainedAlabObservation,"Retained source binding requires original observations")
    descriptor=retained.descriptor()
    return {"profile":ALAB_RETAINED_PROFILE,"originals":descriptor["originals"],
            "derived_descriptor_sha256":sha_bytes(retained._descriptor),
            "occurrences":[row[0]for row in retained._media],
            "new_decode":False,"historical_readback_rewritten":False}


def _require_retained_source_binding(value:dict,retained:_RetainedAlabObservation|None)->None:
    if retained is None:
        insist("retained_source_binding" not in value,
               "A capsule cannot self-admit retained native observations")
    else:
        insist(value.get("retained_source_binding")==_retained_source_binding(retained),
               "Retained source binding differs from independently admitted originals")


def _validate_alab_project_bindings(project:dict,bindings:object,
                                    retained:_RetainedAlabObservation)->None:
    insist(type(retained)is _RetainedAlabObservation and type(bindings)is list
           and len(bindings)==2 and type(project.get("scenes"))is list
           and len(project["scenes"])==2,
           "Closed ALab source requires exactly its two canonical scenes and bindings")
    assets={asset.get("id"):asset for asset in project.get("assets",[])if type(asset)is dict}
    observed=set()
    for scene,pin in zip(project["scenes"],retained._media):
        rows=[row for row in bindings if type(row)is dict and row.get("scene_id")==scene.get("id")]
        insist(len(rows)==1,"ALab binding does not identify exactly one original canonical scene")
        row=rows[0]
        asset=assets.get(row.get("asset_id"))
        insist(asset is not None and asset.get("media_type")=="video/quicktime"
               and asset.get("content_sha256")==pin[3] and row.get("sha256")==pin[3]
               and type(row.get("source_start_frame"))is int and row["source_start_frame"]==0
               and rational(scene.get("duration"),"ALab source scene duration")==Fraction(pin[5],24),
               "ALab canonical scene/asset does not match its selected retained source window")
        observed.add(row["asset_id"])
    insist(len(observed)==2,"ALab selected originals cannot share one substituted asset")


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
