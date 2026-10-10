#!/usr/bin/env python3
"""Run the real Rust OTIO exporter -> source-checked media bridge -> OTIO receiver.

CI only: a synthetic original H.264 is decoded by FFprobe and OpenTimelineIO
parses a real ExternalReference. No actual installed Kdenlive/Shotcut project
handoff or quality/user acceptance is claimed.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

import opentimelineio as otio
from bridge import export_portable,import_preview,BridgeRefused

ROOT=Path(__file__).resolve().parents[2]
FIXTURE=ROOT/"target/debug/examples/otio_real_media_fixture"

def probe(args:list[str],timeout:int=90)->bytes:
    run=subprocess.run(args,capture_output=True,timeout=timeout,check=False)
    if run.returncode:
        raise AssertionError(f"Native fixture or independent NLE parser failed: {args[:2]}\n"+
                             run.stderr.decode("utf8","replace")[-1500:])
    return run.stdout
def sha(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb")as source:
        while b:=source.read(1024*1024):h.update(b)
    return h.hexdigest()
def main()->None:
    if os.environ.get("GITHUB_ACTIONS")!="true":
        raise SystemExit("Heavy native NLE/ffprobe integration must run in disposable CI")
    output=ROOT/"verification/otio-real-media"
    if output.exists():raise AssertionError("Native acceptance must never overwrite stale evidence")
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="mw-first-party-otio-owner-")as temp:
        root=Path(temp)
        original=root/"original.mp4"
        probe([
            "ffmpeg","-nostdin","-hide_banner","-v","error","-y",
            "-f","lavfi","-i","testsrc2=size=320x180:rate=30:duration=3",
            "-frames:v","90","-an","-c:v","libx264","-preset","ultrafast",
            "-pix_fmt","yuv420p","-movflags","+faststart",str(original)
        ],90)
        digest=sha(original)
        data=json.loads(probe([str(FIXTURE),digest],30))
        assert data["schema"]=="motionwright.real-otio-fixture/1"
        project=data["project"];original_otio=data["original_otio"]
        assert len(project["scenes"])==3 and len(project["assets"])==1
        (root/"project.json").write_text(json.dumps(project,indent=2)+"\n")
        (root/"original.otio").write_text(json.dumps(original_otio,indent=2)+"\n")
        request={
            "schema":"motionwright.otio-portable-media-bridge/1",
            "project":"project.json","otio":"original.otio",
            "bindings":[{
                "scene_id":project["scenes"][index]["id"],
                "asset_id":data["owner_media_asset_id"],
                "file":"original.mp4","sha256":digest,
                "source_start_frame":index*30,
                "rights":{"owner":"CI original owner",
                          "license":"Original synthetic H.264 generated on disposable runner",
                          "authorized":True,"redistributable":True}
            }for index in range(2)],
            "owner_review":{"reviewer":"CI original fixture owner",
                            "synthetic_source_allowed":True}
        }
        first=export_portable(root,request,output/"portable")
        assert first["verified_media_refs"]==2 and first["missing_refs"]==1
        bundle=output/"portable"
        fidelity=json.loads((bundle/"bridge-manifest.json").read_text())["property_fidelity"]
        assert len(fidelity)==12
        assert next(item for item in fidelity if item["property"]=="native_object_geometry_hierarchy_text_masks")["status"]=="source_project_only"
        assert next(item for item in fidelity if item["property"]=="publication_rights")["status"]=="not_granted"
        parsed=otio.adapters.read_from_file(str(bundle/"timeline.otio"))
        tracks=list(parsed.tracks)
        assert len(tracks)==1
        clips=list(tracks[0])
        assert len(clips)==3
        for index in range(2):
            ref=clips[index].media_reference
            assert isinstance(ref,otio.schema.ExternalReference)
            assert ref.target_url==f"media/{digest}.mp4"
            assert clips[index].source_range.duration.value==30.0
            assert clips[index].source_range.start_time.value==index*30
            assert ref.available_range.duration.value==90.0
            exact_media=bundle/ref.target_url
            assert exact_media.exists() and sha(exact_media)==digest
            stream=json.loads(probe(["ffprobe","-v","error",
                "-select_streams","v:0","-show_entries","stream=codec_name,nb_frames,r_frame_rate",
                "-of","json",str(exact_media)],15))
            assert stream["streams"][0]["codec_name"]=="h264"
            assert stream["streams"][0]["nb_frames"]=="90"
        assert isinstance(clips[2].media_reference,otio.schema.MissingReference)
        # Independently write and load an NLE-like edited OTIO: reorder two
        # actual video clips and trim the first to 15 source frames.
        encoded=json.loads((bundle/"timeline.otio").read_text())
        ordered=encoded["tracks"]["children"][0]["children"]
        encoded["tracks"]["children"][0]["children"]=[ordered[1],ordered[0],ordered[2]]
        ordered[0]["source_range"]["duration"]["value"]=15.0
        (bundle/"edited.otio").write_text(json.dumps(encoded,indent=2)+"\n")
        reloaded=otio.adapters.read_from_file(str(bundle/"edited.otio"))
        assert len(list(reloaded.tracks[0]))==3
        proposal=import_preview(bundle,"edited.otio",bundle/"source-project.json")
        assert proposal["native_change_proposals"]==[
            {"type":"move_scene","scene_id":project["scenes"][1]["id"],"to_index":0},
            {"type":"set_scene_duration","scene_id":project["scenes"][0]["id"],
             "duration":{"num":"1","den":"2"}}
        ]
        assert proposal["media_refs_verified"]==2 and proposal["missing_references_retained"]==1
        assert not proposal["edit_imported"] and proposal["owner_revision_commit_required"]
        # Each missing source must fail, never switch to an invented filler.
        (bundle/"media"/f"{digest}.mp4").rename(bundle/"media"/"quarantined.mp4")
        try:
            import_preview(bundle,"edited.otio",bundle/"source-project.json")
        except BridgeRefused:
            pass
        else:
            raise AssertionError("Missing ExternalReference source was silently replaced")
        (bundle/"media"/"quarantined.mp4").rename(bundle/"media"/f"{digest}.mp4")
        # Deterministic portable ZIP. Exact original source + real-media bytes.
        with zipfile.ZipFile(bundle/"portable-cut.zip")as archive:
            assert archive.testzip() is None
            assert archive.read(f"media/{digest}.mp4")==original.read_bytes()
            assert archive.read("source-project.json")== (bundle/"source-project.json").read_bytes()
        (output/"import-preview.json").write_text(json.dumps(proposal,indent=2)+"\n")
        metrics={
            "schema":"motionwright.otio-real-media-native-e2e/1",
            "motionwright_sha":probe(["git","rev-parse","HEAD"],10).decode().strip(),
            "source_project_sha256":first["project_sha256"],
            "media_sha256":digest,
            "renderer":"first-party Rust OTIO exporter + actual original H264 + OTIO Python receiver",
            "nle_receiver":"opentimelineio_python_schema",
            "real_video_stream_decode_and_rational_time":"PASS",
            "source_references_located":2,
            "MissingReference_preserved":1,
            "bounded_cut_import_preview":"PASS_NOT_APPLIED",
            "audio_and_native_effects":"NOT_ROUNDTRIPPED",
            "external_nle_editor":"NOT_RUN",
            "human_creative_approval":"NOT_REVIEWED",
            "native_editing_changes_committed":False,
            "release":"BLOCKED_REVIEW"
        }
        (output/"result.json").write_text(json.dumps(metrics,indent=2)+"\n")
        print(json.dumps({"otio_real_media":"PASS","actual_source_refs":2,
                          "missing_refs_preserved":1,"import_changes_proposed":2,
                          "external_editor_used":"NOT_RUN"}))
if __name__=="__main__":main()
