#!/usr/bin/env python3
"""Strict read-only original Manim planar-camera oracle and future physical media gate.

Uses the existing Manim native reader for bounded process/lifetime control.
Source or oracle verification is NOT native execution or authority; physical
verification requires original, separately authorized video and getters.
Never imports generated Python, schedules render, or retries an uncertain job.
"""
from __future__ import annotations
import argparse
import copy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import manim_native_readback as base

PROFILE = "mw-manim-planar-camera-v1"
PINNED_ORACLE_SHA256 = "a1c607f2fa4f76965c381637b521b29fda8b650e195307cc12de1a11a8c1f0c6"
PINNED_REQUEST_SHA256 = "ed53566224657769d3360b010fdca5589fb31691df7171963e02c5c8f50ba4b9"
SCHEMA = 4
FPS = 30
WIDTH = 640
HEIGHT = 360
COUNT = 30
FRAMES_BYTES = WIDTH * HEIGHT * 3 * COUNT
MAX_ORACLE = 1024 * 1024
MAX_TRACE = 8 * 1024 * 1024
MAX_VIDEO = 512 * 1024 * 1024
MAX_PLAN = 256 * 1024
MAX_GETTER_ERROR = 1e-8
MAX_COLOR_CHANNEL_ERROR = 8
MIN_INTERIOR_FRACTION = .99
BORDER_MARGIN = 2
EXPECTED_PHASES = ("original", "reopened", "edited", "edited_reopened")

ReadbackError = base.ReadbackError
require = base.require
sha = base.sha
closed = base.closed_object
parse_json = base.parse_json

def rat(value, label):
    closed(value, {"num", "den"}, label)
    require(type(value["num"]) is str and type(value["den"]) is str, label)
    for item in (value["num"], value["den"]):
        require(re.fullmatch(r"-?(0|[1-9][0-9]{0,12})", item) is not None, label)
    denominator = int(value["den"])
    require(denominator > 0, label)
    return Fraction(int(value["num"]), denominator)

def approx(value, target, label, tolerance=MAX_GETTER_ERROR):
    require(type(value) in (int, float) and math.isfinite(value), label)
    require(abs(value - float(target)) <= tolerance, label)

def phase_variant(oracle: dict, phase: str):
    require(phase in EXPECTED_PHASES, "unadmitted_camera_phase")
    rows = oracle.get("variants")
    require(type(rows) is list and len(rows) == 2, "camera_oracle_variants")
    name = "edited" if phase.startswith("edited") else "original"
    items = [row for row in rows if type(row) is dict and row.get("name") == name]
    require(len(items) == 1, "camera_phase_variant_missing")
    return items[0]

def validate_oracle(oracle: dict, phase: str):
    require(type(oracle) is dict and
            oracle.get("request_sha256") == PINNED_REQUEST_SHA256,
            "original_camera_oracle_missing_request")
    output = oracle.get("output")
    require(type(output) is dict and
            (output.get("width"), output.get("height"), output.get("frame_count")) ==
            (WIDTH, HEIGHT, COUNT), "camera_oracle_output")
    require(output.get("frame_rate") == {"num": "30", "den": "1"}, "camera_oracle_frame_rate")
    require(output.get("audio") == "none_scene_contribution", "camera_oracle_has_audio")
    nodes = oracle.get("source_nodes")
    require(type(nodes) is list and len(nodes) == 2, "camera_oracle_source_nodes")
    require({node["kind"] for node in nodes} == {"rectangle", "circle"}, "camera_source_kinds")
    variant = phase_variant(oracle, phase)
    move = variant["reframe"]
    require(move["component_version"] == 2 and move["duration_frames"] == 24 and
            move["start_center_x"] == 960 and move["start_center_y"] == 540,
            "camera_oracle_reframe")
    require(move["end_center_x"] == (1012 if phase.startswith("edited") else 1000)
            and move["end_center_y"] == 520, "camera_oracle_edited_pose")
    require(variant["scene_terminal_camera"]["center_x"] == move["end_center_x"] and
            variant["scene_terminal_camera"]["center_y"] == move["end_center_y"],
            "camera_terminal_authoring")
    rows = variant.get("frames")
    require(type(rows) is list and len(rows) == COUNT, "camera_oracle_frame_count")
    for index, row in enumerate(rows):
        require(type(row) is dict and type(row["index"]) is int
                and row["index"] == index, "camera_oracle_frame_index")
        require(rat(row["local_time"], "camera_oracle_pts") == Fraction(index, FPS),
                "camera_oracle_time_grid")
        progress = Fraction(min(index,24),24)
        require(rat(row["progress"], "camera_oracle_progress") == progress,
                "camera_oracle_progress_grid")
        cx = Fraction(960,1)+(Fraction(move["end_center_x"]-960,1)*progress)
        cy = Fraction(540,1)+(Fraction(520-540,1)*progress)
        center = row["canonical_camera_center"]
        require(rat(center["x"], "camera_center_x") == cx and
                rat(center["y"], "camera_center_y") == cy, "camera_source_timing_shift")
        native = row["manim_camera_center"]
        require(rat(native["x"],"manim_x")==(cx-960)/135 and
                rat(native["y"],"manim_y")==(540-cy)/135 and
                rat(native["z"],"manim_z")==0, "camera_world_mapping")
        observed = row.get("projected_objects")
        require(type(observed) is list and len(observed)==2, "camera_oracle_frame_objects")
        for source, got in zip(nodes,observed,strict=True):
            require(got["node_id"]==source["node_id"],"camera_node_identity_changed")
            top=got["output_top_left"]
            size=got["output_size"]
            require(rat(top["x"],"shape_x")==(Fraction(source["x"])+960-cx)/3
                    and rat(top["y"],"shape_y")==(Fraction(source["y"])+540-cy)/3,
                    "camera_projected_object_offset")
            require(rat(size["width"],"shape_width")==Fraction(source["width"],3)
                    and rat(size["height"],"shape_height")==Fraction(source["height"],3),
                    "camera_world_object_size_drift")
            world=got["manim_world_center_invariant"]
            require(rat(world["x"],"world_node_x")==
                    Fraction(2*source["x"]+source["width"]-1920,270)
                    and rat(world["y"],"world_node_y")==
                    Fraction(1080-2*source["y"]-source["height"],270)
                    and rat(world["z"],"world_node_z")==0,
                    "camera_world_objects_moved_instead_of_frame")
    return {"source_phase":phase,"frame_count":COUNT,"all_native_frame_positions_predeclared":True,
            "oracle_source_only":True,"physical_native_execution":False,
            "source_node_ids":[n["node_id"] for n in nodes]}

def validate_plan(plan: dict, oracle: dict, phase: str):
    require(type(plan) is dict and type(plan.get("schema_version")) is int
            and plan["schema_version"]==SCHEMA and plan.get("profile_id")==PROFILE,
            "camera_plan_schema_not_admitted")
    require(plan.get("output_role")=="scene_video_contribution_without_audio_or_captions",
            "camera_plan_output_role")
    for name in ("binding","clock","render_profile","camera","reframe","nodes"):
        require(type(plan.get(name)) in (dict,list), "camera_plan_field_missing")
    binding=plan["binding"]
    for name in ("project_id","generation","scene_id","deliverable_id"):
        base.uuid_text(binding.get(name), "camera_binding_invalid")
    base.uint(binding.get("revision"),2**64-1,"camera_revision_invalid",1)
    profile=plan["render_profile"]
    require(profile == {"width":WIDTH,"height":HEIGHT,"frame_rate":FPS},
            "camera_plan_render_profile")
    clock=plan["clock"]
    require(clock.get("frame_count")==COUNT and clock.get("frame_rate")=={"num":30,"den":1}
            and clock.get("local_start_frame")==0, "camera_plan_frame_clock")
    variant=phase_variant(oracle,phase)
    require(plan["reframe"] == variant["reframe"],"camera_plan_authoring_differs_from_oracle")
    cam=plan["camera"]
    require(cam.get("center_x")==variant["reframe"]["end_center_x"] and
            cam.get("center_y")==variant["reframe"]["end_center_y"] and
            cam.get("zoom")==1 and cam.get("rotation_deg")==0,
            "camera_plan_terminal_camera")
    nodes=plan["nodes"]
    source=oracle["source_nodes"]
    require(type(nodes) is list and len(nodes)==len(source),"camera_plan_nodes")
    for index,(node,ref) in enumerate(zip(nodes,source,strict=True)):
        require(node.get("node_id")==ref["node_id"] and
                node.get("canonical_ordinal")==index and
                node.get("source_kind")==ref["kind"] and
                node.get("z_index")==ref["z_index"] and
                node.get("x_px")==ref["x"] and node.get("y_px")==ref["y"] and
                node.get("width_px")==ref["width"] and node.get("height_px")==ref["height"] and
                node.get("fill_hex")==ref["fill"],
                "camera_plan_modified_source_or_world_objects")
    return {"plan_sha256":sha(json.dumps(plan,sort_keys=True,separators=(",",":")).encode()),
            "full_project_stamp_read":True,"provider_authority_verified":False}

def verify_getters(blob: bytes, oracle: dict, phase: str, plan: dict):
    trace=parse_json(blob)
    require(type(trace) is dict and trace.get("profile_id")==PROFILE
            and trace.get("binding")==plan["binding"],"camera_getter_source_identity")
    samples=trace.get("frames")
    require(type(samples) is list and len(samples)==COUNT,"camera_getter_incomplete")
    frames=phase_variant(oracle,phase)["frames"]
    for i,(actual,expected) in enumerate(zip(samples,frames,strict=True)):
        require(type(actual) is dict and type(actual.get("index")) is int and
                actual["index"]==i,"camera_getter_frame_order")
        camera=actual.get("camera")
        require(type(camera) is dict,"camera_getter_camera_missing")
        center=camera.get("frame_center")
        require(type(center) is list and len(center)==3,"camera_getter_vector_malformed")
        for axis,n in enumerate(("x","y","z")):
            approx(center[axis],rat(expected["manim_camera_center"][n],"camera_center"),
                   "camera_getter_world_pose_changed")
        approx(camera.get("frame_width"),Fraction(128,9),"camera_width_drift")
        approx(camera.get("frame_height"),8,"camera_height_drift")
        objects=actual.get("objects")
        require(type(objects) is list and len(objects)==2,"camera_getter_world_nodes")
        for j,(obj,source) in enumerate(zip(objects,oracle["source_nodes"],strict=True)):
            require(obj.get("node_id")==source["node_id"] and
                    obj.get("kind")==source["kind"] and
                    obj.get("fill_hex")==source["fill"],"camera_getter_world_identity")
            pos=obj.get("world_center")
            invariant=expected["projected_objects"][j]["manim_world_center_invariant"]
            require(type(pos) is list and len(pos)==3,"camera_getter_object_vector")
            for axis,n in enumerate(("x","y","z")):
                approx(pos[axis],rat(invariant[n],"world_node"),
                       "camera_getter_world_node_was_moved")
    return {"getter_samples":COUNT,"native_frame_center_checked":True,
            "world_objects_invariant":True,"provider_authority_verified":False}

def measured_camera_phase_from_pixels(pixels: bytes, original_oracle: dict,
                                      phase: str, at: int) -> dict:
    """Fix <0.2-frame phase acceptance BEFORE native execution, using BOTH shapes.

    Independent physical RGB centroids cannot be substituted by plan/getter
    values. If H264 quantization hides the required bound, fail INCOMPLETE;
    never widen the threshold after an unexplained native result.
    """
    require(type(pixels) is bytes and len(pixels) == WIDTH*HEIGHT*3,
            "camera_phase_raw_frame_size")
    variant=phase_variant(original_oracle,phase)
    camera=variant["reframe"]
    step=math.hypot(
        (camera["end_center_x"]-camera["start_center_x"])/(3*camera["duration_frames"]),
        (camera["end_center_y"]-camera["start_center_y"])/(3*camera["duration_frames"]),
    )
    require(step>0,"camera_phase_zero_displacement")
    result={"frame":at,"measured":{}}
    sample=variant["frames"][at]["projected_objects"]
    for source,prediction in zip(original_oracle["source_nodes"],sample,strict=True):
        shape=source["kind"]
        top=prediction["output_top_left"]
        size=prediction["output_size"]
        px=float(rat(top["x"],"predicted_bbox_x"))
        py=float(rat(top["y"],"predicted_bbox_y"))
        w=float(rat(size["width"],"predicted_bbox_w"))
        h=float(rat(size["height"],"predicted_bbox_h"))
        firstx=max(0,int(math.floor(px-5)))
        lastx=min(WIDTH,int(math.ceil(px+w+5)))
        firsty=max(0,int(math.floor(py-5)))
        lasty=min(HEIGHT,int(math.ceil(py+h+5)))
        color=bytes.fromhex(source["fill"][1:])
        require(len(color)==3 and
                ((shape=="rectangle" and color[0]>0) or
                 (shape=="circle" and color[1]>0 and color[2]>0)),
                "camera_phase_admitted_source_signal")
        area=sx=sy=0.0
        for y in range(firsty,lasty):
            for x in range(firstx,lastx):
                offset=(y*WIDTH+x)*3
                red,green,blue=pixels[offset:offset+3]
                # H.264 4:2:0 spreads antialiased edge colors across pixels.
                # A hard color-dominance cutoff systematically shifts their
                # centroid. Instead infer bounded alpha coverage from source
                # channels. The 2-code black-level floor only rejects codec
                # background noise; it does not change the <0.2-frame gate.
                if shape=="rectangle":
                    weight=min(1.0,max(0.0,(red-2)/color[0]))
                else:
                    weight=min(1.0,max(0.0,
                        min(green/color[1],blue/color[2])-2/255))
                if weight:
                    area+=weight
                    sx+=(x+.5)*weight
                    sy+=(y+.5)*weight
        # A plausible centroid still requires almost the entire declared
        # opaque shape's coverage, not one bright pixel or an empty frame.
        target_area=w*h*(math.pi/4 if shape=="circle" else 1.0)
        require(area>=target_area*0.6,"camera_phase_shape_signal_incomplete")
        observed_x=sx/area
        observed_y=sy/area
        center=prediction["output_center"]
        predicted_x=float(rat(center["x"],"predicted_camera_object_cx"))
        predicted_y=float(rat(center["y"],"predicted_camera_object_cy"))
        phase_error=math.hypot(observed_x-predicted_x,observed_y-predicted_y)/step
        require(phase_error<0.2,f"camera_temporal_phase_undetermined_{at}_{shape}")
        result["measured"][source["node_id"]]={
            "kind":shape,"centroid_xy":[observed_x,observed_y],
            "oracle_xy":[predicted_x,predicted_y],
            "estimated_phase_error_frames":phase_error,
            "max_admitted_phase_error_frames":0.2,
        }
    return result


def verify_physical_media(video: Path, oracle: dict, phase: str,
                          ffprobe: Path, ffmpeg: Path):
    raw_video=base.read_regular(video,MAX_VIDEO)
    media_hash=sha(raw_video)
    del raw_video
    probe_json,_=base.run_bounded(
        [ffprobe,"-v","error","-show_streams","-show_format","-show_frames","-of","json",video],
        MAX_TRACE,30)
    probe=base.parse_json(probe_json)
    meta=base.inspect_probe(probe,FPS,COUNT)
    raw,_=base.run_bounded(
        [ffmpeg,"-nostdin","-v","error","-xerror","-err_detect","explode",
         "-i",video,"-map","0:v:0","-vsync","0","-pix_fmt","rgb24",
         "-f","rawvideo","pipe:1"],FRAMES_BYTES+1,60)
    require(len(raw)==FRAMES_BYTES,"camera_video_frame_count")
    require(sha(base.read_regular(video,MAX_VIDEO))==media_hash,"camera_video_custody_changed")
    source=oracle["source_nodes"]
    frames=phase_variant(oracle,phase)["frames"]
    reports=[]
    phase_reports=[]
    for frame_index in range(COUNT):
        pixels=raw[frame_index*WIDTH*HEIGHT*3:(frame_index+1)*WIDTH*HEIGHT*3]
        phase_reports.append(measured_camera_phase_from_pixels(
            pixels,oracle,phase,frame_index))
        rectangles=[]
        for item,node in zip(frames[frame_index]["projected_objects"],source,strict=True):
            top=item["output_top_left"];size=item["output_size"]
            rectangles.append((node["kind"],float(rat(top["x"],"screen_x")),
                               float(rat(top["y"],"screen_y")),
                               float(rat(size["width"],"width")),
                               float(rat(size["height"],"height")),
                               tuple(bytes.fromhex(node["fill"][1:]))))
        groups={"background":[0,0],"rectangle":[0,0],"circle":[0,0]}
        border=0
        for y in range(HEIGHT):
            for x in range(WIDTH):
                sample_x=x+.5;sample_y=y+.5
                kind="background";expected=(0,0,0);edge=False
                for shape,left,top,width,height,fill in rectangles:
                    if shape=="circle":
                        distance=width/2-math.hypot(sample_x-(left+width/2),
                                                    sample_y-(top+height/2))
                    else:
                        distance=min(sample_x-left,left+width-sample_x,
                                     sample_y-top,top+height-sample_y)
                    if abs(distance)<=BORDER_MARGIN:
                        edge=True;break
                    if distance>0:
                        kind=shape;expected=fill
                if edge:
                    border+=1;continue
                offset=(y*WIDTH+x)*3
                accepted=max(abs(pixels[offset+k]-expected[k]) for k in range(3))<=MAX_COLOR_CHANNEL_ERROR
                groups[kind][0]+=1;groups[kind][1]+=int(accepted)
        for kind,(total,matched) in groups.items():
            require(total>= (250 if kind!="background" else WIDTH*HEIGHT//10),
                    "camera_frame_missing_colored_regions")
            require(matched/total>=MIN_INTERIOR_FRACTION,
                    f"camera_frame_pixel_content_{frame_index}_{kind}")
        reports.append({"index":frame_index,"decoded_rgb24_sha256":sha(pixels),
                        "regions":groups,"excluded_border_pixels":border})
    return {"video_sha256":media_hash,"frame_count":COUNT,"full_frames_physically_decoded":True,
            "frame_readback":reports,"probe":meta,
            "independent_two_shape_phase_readback":phase_reports,
            "verified_temporal_subframe_precision":True,
            "phase_error_frames_strict_upper_bound":0.2,
            "runtime_execution_performed_by_reader":False,
            "native_permissions_verified":False}

def source_plan_fixture(oracle, phase):
    """Test-only data mirror, NEVER an admissible native source or Host receipt."""
    variant=phase_variant(oracle,phase)
    nodes=oracle["source_nodes"]
    return {
        "schema_version":SCHEMA, "profile_id":PROFILE,
        "output_role":"scene_video_contribution_without_audio_or_captions",
        "binding":{
            "project_id":"10000000-0000-4000-8000-000000000001",
            "generation":"10000000-0000-4000-8000-000000000002",
            "revision":8,
            "scene_id":variant["reframe"]["scene_id"],
            "deliverable_id":"10000000-0000-4000-8000-000000000003",
        },
        "clock":{"frame_count":COUNT,"frame_rate":{"num":30,"den":1},"local_start_frame":0},
        "render_profile":{"width":WIDTH,"height":HEIGHT,"frame_rate":FPS},
        "camera":copy.deepcopy(variant["scene_terminal_camera"]),
        "reframe":copy.deepcopy(variant["reframe"]),
        "nodes":[{"node_id":node["node_id"],"canonical_ordinal":ordinal,
                  "source_kind":node["kind"],"z_index":node["z_index"],
                  "x_px":node["x"],"y_px":node["y"],
                  "width_px":node["width"],"height_px":node["height"],
                  "fill_hex":node["fill"]} for ordinal,node in enumerate(nodes)],
    }

def self_test(oracle):
    for phase in ("original","edited"):
        assert validate_oracle(oracle,phase)["frame_count"]==30
        validate_plan(source_plan_fixture(oracle,phase),oracle,phase)
    variants={"original":0,"edited":1}
    def variant_mutation(value,phase,mutation):
        mutation(value["variants"][variants[phase]])
    negatives=[
        ("index_repeat","original",lambda v:v["frames"][5].update(index=4)),
        ("pts_retimed","original",lambda v:v["frames"][5]["local_time"].update(num="6")),
        ("progress_retimed","original",lambda v:v["frames"][5]["progress"].update(num="3")),
        ("center_retimed","original",lambda v:v["frames"][5]["canonical_camera_center"]["x"].update(num="1")),
        ("manim_world_center_drift","original",lambda v:v["frames"][5]["manim_camera_center"]["x"].update(num="1")),
        ("screen_frame_geometry","original",lambda v:v["frames"][5]["projected_objects"][0]["output_top_left"]["x"].update(num="1")),
        ("moved_world_rectangle","original",lambda v:v["frames"][5]["projected_objects"][0]["manim_world_center_invariant"]["x"].update(num="1")),
        ("terminal_hold_drift","original",lambda v:v["frames"][27]["canonical_camera_center"]["x"].update(num="1")),
        ("missing_frame","original",lambda v:v["frames"].pop()),
        ("duration_half_frame","original",lambda v:v["reframe"].update(duration_frames=25)),
        ("terminal_not_saved","original",lambda v:v["scene_terminal_camera"].update(center_x=990)),
        ("edited_terminal_reverted","edited",lambda v:v["reframe"].update(end_center_x=1000)),
        ("wrong_native_z","edited",lambda v:v["frames"][14]["manim_camera_center"]["z"].update(num="2")),
    ]
    rejects=[]
    for name,phase,mutation in negatives:
        altered=copy.deepcopy(oracle)
        variant_mutation(altered,phase,mutation)
        try:
            validate_oracle(altered,phase)
        except ReadbackError:
            rejects.append(name)
        else:
            raise AssertionError(f"altered oracle survived original rational oracle: {name}")
    wrong_plans=[
        ("source_id_mismatch",lambda p:p["nodes"][0].update(node_id="40000000-0000-4000-8000-000000000003")),
        ("foreign_profile",lambda p:p.update(profile_id="other-profile")),
        ("bool_revision",lambda p:p["binding"].update(revision=True)),
        ("bad_fps",lambda p:p["clock"]["frame_rate"].update(num=60)),
        ("fake_world_shift",lambda p:p["nodes"][0].update(x_px=540)),
        ("wrong_camera_terminal",lambda p:p["camera"].update(center_x=1100)),
        ("unsupported_audio_role",lambda p:p.update(output_role="full_master")),
    ]
    for name,mutation in wrong_plans:
        plan=source_plan_fixture(oracle,"original")
        mutation(plan)
        try:
            validate_plan(plan,oracle,"original")
        except ReadbackError:
            rejects.append(name)
        else:
            raise AssertionError(f"invalid source plan accepted: {name}")
    getters_verified=0
    for phase in ("original","edited"):
        plan=source_plan_fixture(oracle,phase)
        frames=phase_variant(oracle,phase)["frames"]
        trace={
            "profile_id":PROFILE,"binding":plan["binding"],"frames":[
                {"index":i,
                 "camera":{"frame_center":[float(rat(row["manim_camera_center"][key],"center"))
                                          for key in ("x","y","z")],
                           "frame_width":128/9,"frame_height":8},
                 "objects":[
                     {"node_id":object_["node_id"],"kind":source["kind"],
                      "fill_hex":source["fill"],
                      "world_center":[float(rat(object_["manim_world_center_invariant"][key],
                                               "world_position")) for key in ("x","y","z")]}
                     for object_,source in zip(row["projected_objects"],
                                               oracle["source_nodes"],strict=True)]
                }
                for i,row in enumerate(frames)]}
        trace_blob=json.dumps(trace,sort_keys=True).encode("utf8")
        getter=verify_getters(trace_blob,oracle,phase,plan)
        require(getter["getter_samples"]==30,"test_getter_count")
        getters_verified+=1
        negative_getters=[
            ("stuck_world_camera",lambda t:t["frames"][5]["camera"]["frame_center"].__setitem__(0,0)),
            ("half_frame_sampling",lambda t:t["frames"][1]["camera"]["frame_center"].__setitem__(
                0,t["frames"][1]["camera"]["frame_center"][0]+0.004)),
            ("moving_world_node",lambda t:t["frames"][6]["objects"][0]["world_center"].__setitem__(
                0,t["frames"][6]["objects"][0]["world_center"][0]+0.015)),
            ("stale_native_source_generation",lambda t:t["binding"].update(generation=
                "10000000-0000-4000-8000-000000000009")),
            ("missing_terminal_hold_sample",lambda t:t["frames"].pop()),
            ("stale_shape_color",lambda t:t["frames"][9]["objects"][0].update(fill_hex="#000000")),
            ("unapproved_zoomed_frame",lambda t:t["frames"][13]["camera"].update(frame_width=14)),
            ("reused_wrong_sample_index",lambda t:t["frames"][13].update(index=12)),
        ]
        for name,mutate in negative_getters:
            modified=copy.deepcopy(trace)
            mutate(modified)
            try:
                verify_getters(json.dumps(modified,sort_keys=True).encode("utf8"),
                               oracle,phase,plan)
            except ReadbackError:
                rejects.append(f"{phase}:{name}")
            else:
                raise AssertionError(f"forged getter data passed: {phase}:{name}")
    # A generated raster is only a test of the independent geometry oracle,
    # never evidence that Manim rendered this content. One whole-frame shift
    # must fail the predeclared <0.2-frame threshold rather than being excused
    # by the two-pixel anti-aliased-border exclusion.
    def synthetic_frame0(shift: int) -> bytes:
        rgb=bytearray(WIDTH*HEIGHT*3)
        for y in range(60,270):
            for x in range(105,460):
                fill=None
                if 140+shift <= x+0.5 < 230+shift and 90 <= y+0.5 < 150:
                    fill=bytes.fromhex("E53935")
                elif math.hypot(x+0.5-(405+shift),y+0.5-210)<30:
                    fill=bytes.fromhex("2BBBAD")
                if fill is not None:
                    pos=(y*WIDTH+x)*3
                    rgb[pos:pos+3]=fill
        return bytes(rgb)
    measured=measured_camera_phase_from_pixels(synthetic_frame0(0),oracle,"original",0)
    require(len(measured["measured"])==2,"source_geometry_fixture_incomplete")
    try:
        measured_camera_phase_from_pixels(synthetic_frame0(1),oracle,"original",0)
    except ReadbackError:
        rejects.append("raster_synthetic_one_pixel_phase_shift")
    else:
        raise AssertionError("one-pixel shifted camera geometry wrongly passed the strict phase bound")
    return {"positive_oracle_variants":2,"positive_source_plan_mirrors":2,
            "synthetic_source_getter_contract_cases":getters_verified,
            "synthetic_phase_estimator_test_only":True,
            "negative_source_oracle_plan_getter_cases":len(rejects),
            "negative_cases":rejects,
            "actual_native_execution":"NOT_RUN",
            "native_rendered_30_frames":"NOT_RUN",
            "actual_getter_readback":"NOT_RUN",
            "human_and_rights_acceptance":"NOT_RUN"}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle",type=Path,required=True)
    parser.add_argument("--self-test",action="store_true")
    parser.add_argument("--plan",type=Path)
    parser.add_argument("--getters",type=Path)
    parser.add_argument("--video",type=Path)
    parser.add_argument("--ffprobe",type=Path)
    parser.add_argument("--ffmpeg",type=Path)
    parser.add_argument("--phase",choices=EXPECTED_PHASES,default="original")
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    oracle_bytes=base.read_regular(args.oracle,MAX_ORACLE)
    require(sha(oracle_bytes)==PINNED_ORACLE_SHA256, "camera_oracle_original_bytes_changed")
    oracle=base.parse_json(oracle_bytes)
    if args.self_test:
        result=self_test(oracle)
        print(json.dumps({"status":"SOURCE_ORACLE_ONLY_PASS",**result}))
        return
    require(all([args.plan,args.getters,args.video,args.ffprobe,args.ffmpeg,args.output]),
            "physical_verification_arguments_required")
    require(not args.output.exists() and args.output.parent.is_dir(),
            "new_camera_receipt_destination_required")
    validate_oracle(oracle,args.phase)
    plan=base.parse_json(base.read_regular(args.plan,MAX_PLAN))
    source=validate_plan(plan,oracle,args.phase)
    getters_bytes=base.read_regular(args.getters,MAX_TRACE)
    getter_document=base.parse_json(getters_bytes)
    require(getter_document.get("plan_sha256")==sha(base.read_regular(args.plan,MAX_PLAN)),
            "native_getter_plan_bytes_not_bound")
    getter=verify_getters(getters_bytes,oracle,args.phase,plan)
    media=verify_physical_media(args.video,oracle,args.phase,args.ffprobe,args.ffmpeg)
    report={"schema":"motionwright.manim.planar-camera.readback/1",
            "classification":"SOURCE_AND_PHYSICAL_CONTENT_ONLY_NEVER_NATIVE_AUTHORITY",
            "phase":args.phase,"source":source,"getter":getter,"media":media,
            "original_runtime_execution_verified":False,
            "human_acceptance":False,"rights_external_acceptance":False}
    with args.output.open("xb") as stream:
        stream.write((json.dumps(report,sort_keys=True,indent=2)+"\n").encode())

if __name__=="__main__":
    main()
