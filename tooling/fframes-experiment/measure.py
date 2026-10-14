#!/usr/bin/env python3
"""Pinned fframes upstream experiment, NOT a Motionwright renderer adapter.

Measure cold/warm build, startup, headless frame preview and a small real video
render on a disposable CI runner. Record absent audio/interactive preview and
disallow any automatic backend adoption. No project source or owner media used.
"""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[2]
PIN="e2b552892c0373a5c8dc79de9280a3289670f27b"
REPO="https://github.com/dmtrKovalenko/fframes.git"
APP="hello_world_example_bin"
PKG="hello-world-example"
OUT=ROOT/'verification/fframes-upstream-experiment'
CHECKOUT=ROOT/'verification/fframes-upstream-source'
MAX_STDOUT=32*1024
EXPERIMENT_SCHEMA="motionwright.fframes-experimental-runtime/1"
LICENSE="MIT"
def expect(condition:bool,why:str)->None:
    if not condition:raise AssertionError(why)
def sha_file(p:Path)->str:
    digest=hashlib.sha256()
    with p.open("rb")as stream:
        while chunk:=stream.read(1024*1024):digest.update(chunk)
    return digest.hexdigest()
def run(args:list[str],where:Path,limit:int,label:str,metrics:dict,env:dict[str,str]|None=None,allow_failure:bool=False)->subprocess.CompletedProcess[bytes]:
    began=time.perf_counter_ns()
    completed=subprocess.run(args,cwd=where,env=env,timeout=limit,
        capture_output=True,check=False)
    measured=round((time.perf_counter_ns()-began)/1e9,3)
    metrics[label]={"elapsed_seconds":measured,"exit_code":completed.returncode,
        "timeout_seconds":limit,"stdout_bytes":len(completed.stdout),
        "stderr_bytes":len(completed.stderr),"command_kind":args[0]}
    # Do not archive potentially private stderr from a process environment.
    # Only bounded code/step data is retained for failed upstream builds.
    if completed.returncode and not allow_failure:
        tail=completed.stderr.decode("utf8","replace")[-MAX_STDOUT:]
        raise AssertionError(f"{label}: upstream command failed (exit {completed.returncode}):\n{tail}")
    return completed
def png_metadata(path:Path)->dict:
    data=path.read_bytes()
    expect(24<=len(data)<=16*1024*1024 and data[:8]==b"\x89PNG\r\n\x1a\n",
           "Native fframes preview did not generate a bounded valid PNG")
    width,height=struct.unpack(">II",data[16:24])
    expect(1<=width<=4096 and 1<=height<=4096,"Unexpected preview dimensions")
    return {"width":width,"height":height,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}
def main()->None:
    expect(os.getenv("GITHUB_ACTIONS")=="true","Only run upstream Rust source on an isolated CI runner")
    expect(not OUT.exists() and not CHECKOUT.exists(),"Refuse to overwrite a benchmark or pinned checkout")
    OUT.mkdir(parents=True)
    metrics={}
    evidence={
        "schema":EXPERIMENT_SCHEMA,"status":"STARTED","runtime":"disposable_ubuntu_24_04",
        "fframes_repository":REPO,"fframes_exact_sha":PIN,
        "fframes_license":LICENSE,
        "rust_svg_experimental":True,
        "source_code_admitted_to_motionwright":False,
        "semwright_driver_host_executed":False,
        "owner_install_permission":False,
        "backend_release_admitted":False,
        "artistic_quality_reviewed":False,
        "hyperframes_same_fixture_comparison":"NOT_RUN",
        "interactive_gpu_preview":"NOT_RUN_HEADLESS_CI",
        "audio_score_composition":"NOT_RUN_NO_AUDIO_IN_HELLO_FIXTURE",
        "compile_time_svg_correctness":"NOT_EQUIVALENT_TO_MOTIONWRIGHT_SOURCE",
        "cold_build_measured":False,"warm_build_measured":False,
        "frame_preview_measured":False,"full_video_render_measured":False,
        "measurements":metrics
    }
    try:
        run(["git","clone","--quiet","--filter=blob:none",REPO,str(CHECKOUT)],
            ROOT,120,"source_checkout",metrics)
        run(["git","checkout","--detach","--quiet",PIN],
            CHECKOUT,45,"exact_source_checkout",metrics)
        actual=subprocess.check_output(["git","rev-parse","HEAD"],cwd=CHECKOUT,text=True).strip()
        expect(actual==PIN,"Unpinned or mutated upstream fframes checkout")
        assert shutil.which("cargo") and shutil.which("ffmpeg") and shutil.which("ffprobe")
        metrics["tool_versions"]={
            "rustc":subprocess.check_output(["rustc","--version"],text=True).strip(),
            "ffmpeg":subprocess.check_output(["ffmpeg","-version"],text=True).splitlines()[0],
            "architecture":subprocess.check_output(["uname","-m"],text=True).strip()
        }
        build=["cargo","build","--locked","--release","-p",PKG,"--bin",APP]
        env=os.environ.copy()
        env.update({"CARGO_INCREMENTAL":"0","CARGO_PROFILE_RELEASE_DEBUG":"0"})
        run(build,CHECKOUT,1350,"cold_release_build",metrics,env)
        evidence["cold_build_measured"]=True
        binary=CHECKOUT/'target/release'/APP
        expect(binary.is_file() and binary.stat().st_size<512*1024*1024,
               "Expected compiled pinned Rust source executable absent/oversized")
        evidence["binary_bytes"]=binary.stat().st_size
        evidence["binary_sha256"]=sha_file(binary)
        run(build,CHECKOUT,90,"warm_release_build",metrics,env)
        evidence["warm_build_measured"]=True
        helper=subprocess.run([str(binary),"--help"],cwd=CHECKOUT,
            capture_output=True,timeout=15,check=False)
        expect(helper.returncode==0 and b"render" in helper.stdout,
               "Pinned fframes CLI is missing its documented command set")
        metrics["binary_startup"]={"elapsed_seconds":None,
            "probe":"help_started_and_exited","exit_code":helper.returncode}
        for i in range(2):
            # Consistent exact binary startup times (no cargo build in this phase).
            run([str(binary),"--help"],CHECKOUT,15,f"cli_startup_{i}",metrics)
        output_frame=OUT/'frame-preview'
        output_frame.mkdir()
        run([str(binary),"frame","1s","-o",str(output_frame),"--scale","0.25"],
            CHECKOUT,180,"cold_headless_native_preview",metrics)
        frame_candidates=sorted(output_frame.rglob("*.png"))
        expect(1<=len(frame_candidates)<=16,"First-party headless preview yielded no bounded PNG artifact")
        evidence["preview_png"]=png_metadata(frame_candidates[0])
        image_probe=run(["ffprobe","-v","error","-show_entries",
              "stream=codec_name,width,height","-of","json",str(frame_candidates[0])],
              CHECKOUT,30,"independent_png_decode_probe",metrics)
        image_data=json.loads(image_probe.stdout)
        image_stream=image_data["streams"][0]
        expect(image_stream["codec_name"]=="png"
            and image_stream["width"]==evidence["preview_png"]["width"]
            and image_stream["height"]==evidence["preview_png"]["height"],
            "Independent FFprobe failed to decode exact native PNG source")
        evidence["preview_png"]["decoded_by_ffprobe"]=True
        evidence["frame_preview_measured"]=True
        repeated_frame=OUT/'frame-preview-repeat'
        repeated_frame.mkdir()
        run([str(binary),"frame","1s","-o",str(repeated_frame),"--scale","0.25"],
            CHECKOUT,180,"warm_headless_native_preview",metrics)
        repeated_candidates=sorted(repeated_frame.rglob("*.png"))
        expect(1<=len(repeated_candidates)<=16,"Repeated native preview did not produce a new bounded frame")
        evidence["repeated_native_preview_sha256"]=sha_file(repeated_candidates[0])
        evidence["repeated_source_pixels_identical"]=(
            evidence["preview_png"]["sha256"]==evidence["repeated_native_preview_sha256"])
        movie=OUT/'upstream-source-preview.mp4'
        run([str(binary),"render","0s..1s","--draft","-o",str(movie)],
            CHECKOUT,360,"cold_bounded_video_render",metrics)
        expect(movie.is_file() and 1024<movie.stat().st_size<80*1024*1024,
               "Actual fframes H.264 one-second video was not produced")
        probe=run(["ffprobe","-v","error","-show_entries",
                   "stream=codec_name,width,height,r_frame_rate,nb_frames",
                   "-show_entries","format=duration",
                   "-of","json",str(movie)],
                  CHECKOUT,60,"independent_mp4_probe",metrics)
        decoded=json.loads(probe.stdout)
        video=next((s for s in decoded["streams"]if s.get("width")),None)
        expect(video is not None and int(video["width"])>=1,
               "Independent FFprobe could not inspect the fframes video source")
        evidence["video"]={"bytes":movie.stat().st_size,"sha256":sha_file(movie),
                           "codec":video["codec_name"],
                           "fps":video.get("r_frame_rate"),
                           "width":video["width"],"height":video["height"],
                           "duration_seconds":decoded["format"]["duration"]}
        evidence["full_video_render_measured"]=True
        evidence["status"]="EXPERIMENTAL_UPSTREAM_SOURCE_RENDER_PASS"
    except BaseException as error:
        evidence["status"]="EXPERIMENTAL_UPSTREAM_SOURCE_BLOCKED"
        evidence["failure_type"]=type(error).__name__
        evidence["failure_summary"]=str(error)[-1400:]
    finally:
        evidence["completed_utc"]=datetime.now(timezone.utc).isoformat()
        evidence["motionwright_sha"]=subprocess.check_output(
            ["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
        evidence["backend_release_admitted"]=False
        (OUT/'result.json').write_text(json.dumps(evidence,indent=2)+"\n")
        print(json.dumps({"fframes_upstream":evidence["status"],
            "source_sha":PIN,"release_admitted":False,
            "cold_build_measured":evidence["cold_build_measured"],
            "video_render_measured":evidence["full_video_render_measured"]}))
        if evidence["status"]!="EXPERIMENTAL_UPSTREAM_SOURCE_RENDER_PASS":
            raise SystemExit(2)
if __name__=="__main__":main()
