#!/usr/bin/env python3
"""Curate only verified public synthetic SFX AV evidence; no owner Broker logs."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

FILES = {
    "original-focus-sfx.wav": 250_000,
    "original-confirm-sfx.wav": 250_000,
    "semantic-sfx-master.wav": 500_000,
    "motionwright-master.mp4": 80 * 1024 * 1024,
    "frame-first.png": 16 * 1024 * 1024,
    "frame-middle.png": 16 * 1024 * 1024,
    "frame-last.png": 16 * 1024 * 1024,
}
SHA = __import__("re").compile(r"^[0-9a-f]{40}$")


def read_json(path: Path) -> dict:
    def distinct(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for k,v in pairs:
            if k in result:
                raise ValueError("duplicate JSON member")
            result[k]=v
        return result
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256*1024:
        raise ValueError("missing, redirected or excessive evidence JSON")
    data=json.loads(path.read_text(encoding="utf8"),object_pairs_hook=distinct)
    if not isinstance(data,dict):
        raise ValueError("expected bounded evidence mapping")
    return data


def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def curate(source: Path, output: Path, sha: str, success: bool) -> dict:
    if not SHA.fullmatch(sha):
        raise ValueError("public evidence requires exact lowercase 40-digit SHA")
    output.mkdir(parents=True,exist_ok=False)
    result={
        "schema":"motionwright.semantic-sfx-native-av-public/1",
        "motionwright_sha":sha,
        "technical_result":"NOT_VERIFIED",
        "audio_source_scope":"original synthetic PCM fixtures, not licensed audio",
        "native_sdk_mux":"NOT_VERIFIED",
        "human_creative_approval":"REQUIRED",
        "rights_certification":"NOT_PERFORMED",
        "files":{},
    }
    if success:
        try:
            run=read_json(source/"result.json")
            prep=read_json(source/"sfx-prepared.json")
            decoded=read_json(source/"decoded-media-proof.json")
            assert run["motionwright_sha"]==sha
            assert run["native_av_master_e2e"]=="PASS"
            fixture=run["fixture"]
            assert fixture in ("semantic-sfx-av","semantic-sfx-composition-av")
            composition=fixture=="semantic-sfx-composition-av"
            frames=540 if composition else 60
            assert run["frame_count"]==frames
            assert prep["frames"]==frames
            assert len(prep["scene_ids"])==(3 if composition else 1)
            assert len(prep["scheduled_sfx"])==2
            assert run["semantic_sfx_source_project_revision"]==prep["revision"]
            assert prep["schema"]=="motionwright.semantic-sfx-av-prepared/1"
            assert prep["native_preparation_verified"] is True
            assert prep["native_preparation_revision"]==prep["revision"]
            assert prep["original_source_count"]==2
            assert isinstance(prep["audio_relative"],str)
            assert prep["audio_relative"].startswith("mw-sfx-")
            assert "/" not in prep["audio_relative"] and "\\" not in prep["audio_relative"]
            assert decoded["source_wav_sha256"]==prep["audio_sha256"]
            assert prep["project_id"]==decoded["project_id"]
            assert prep["generation"]==decoded["generation"]
            assert prep["revision"]==decoded["revision"]
            assert prep["audio_sha256"]==run["audio_sha256"]==decoded["source_wav_sha256"]
            assert prep["first_source_sha256"]==decoded["source_sha256"][0]
            assert prep["second_source_sha256"]==decoded["source_sha256"][1]
            assert decoded["master_sha256"]==run["master_sha256"]
            assert decoded["technical_h264_aac_mux"]=="PASS"
            assert decoded["technical_source_pcm"]=="PASS"
            assert decoded["video_frames_decoded"]==frames
            assert decoded["exact_sfx_sample_count"]==frames*1600
            assert decoded["changed_channel_bytes"]>=85
            if composition:
                assert decoded["max_absolute_onset_error_samples"]<=1024
                assert decoded["observed_onset_samples"] and len(decoded["observed_onset_samples"])==2
                assert decoded["expected_onset_samples"]==[9600,608000]
                assert decoded["original_scene_count"]==3
                assert decoded["middle_scene_silence_sample_count"]==180*1600
                assert decoded["stereo_channel_identity"]=="confirmed_scene0_440_660_scene2_740_980_hz"
            else:
                assert decoded["absolute_first_onset_error_samples"]<=1024
                assert decoded["stereo_channel_identity"]=="confirmed_scene_linked_440_660_and_740_980_hz"
            assert decoded["human_creative_approval"]=="REQUIRED"
            for name,max_bytes in FILES.items():
                if composition and name=="semantic-sfx-master.wav":
                    max_bytes=4*1024*1024
                path=source/name
                assert path.is_file() and not path.is_symlink() and 44<path.stat().st_size<=max_bytes
                result["files"][name]={"sha256":digest(path),"bytes":path.stat().st_size}
            assert result["files"]["motionwright-master.mp4"]["sha256"]==run["master_sha256"]
            assert result["files"]["semantic-sfx-master.wav"]["sha256"]==prep["audio_sha256"]
            assert result["files"]["original-focus-sfx.wav"]["sha256"]==prep["first_source_sha256"]
            assert result["files"]["original-confirm-sfx.wav"]["sha256"]==prep["second_source_sha256"]
            for name in FILES:
                shutil.copyfile(source/name,output/name)
            result["technical_result"]="PASS"
            result["native_sdk_mux"]="REAL_PINNED_DRIVER_HOST"
            result["fixture"]=fixture
            result["original_scene_count"]=3 if composition else 1
            result["native_frame_count"]=frames
            result["decoded_aac_onset_error_samples"]=(
                decoded["max_absolute_onset_error_samples"] if composition
                else decoded["absolute_first_onset_error_samples"]
            )
        except (AssertionError,KeyError,TypeError,ValueError,OSError,json.JSONDecodeError):
            # Never emit candidate media from failed or ambiguous evidence.
            for name in FILES:
                (output/name).unlink(missing_ok=True)
            result["files"]={}
    (output/"public-evidence.json").write_text(
        json.dumps(result,sort_keys=True,indent=2)+"\n",encoding="utf8")
    return result


def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("--source-sha",required=True)
    p.add_argument("--evidence-root",type=Path,required=True)
    p.add_argument("--output-root",type=Path,required=True)
    p.add_argument("--job-status",choices=("success","failure","cancelled"),required=True)
    a=p.parse_args()
    public=curate(a.evidence_root,a.output_root,a.source_sha,a.job_status=="success")
    print("semantic-sfx-av-public="+public["technical_result"])
    return 0 if a.job_status!="success" or public["technical_result"]=="PASS" else 1


if __name__=="__main__":
    raise SystemExit(main())
