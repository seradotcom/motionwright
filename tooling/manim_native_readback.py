#!/usr/bin/env python3
"""Independent bounded reader for the fixed Manim static-source native fixtures.

Reads the entire real media file with FFprobe and a separate FFmpeg RGB decode.
Does not import/execute Manim or scene.py, compile a scene, mix audio, or launch a
replacement render. Thresholds come from the pre-render fixture specification.
A passing report is technical fixture evidence; it is not execution authority,
a full Manim capability claim, an audio master, or human aesthetic acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import shutil
import struct
import subprocess
import threading
import time
import unittest
import zipfile
import zlib
from fractions import Fraction
from pathlib import Path

PROFILE = "mw-manim-static-shapes-v2"
COMPILER = "motionwright.manim.rust.static_shapes.v2.1"
MIME = "application/vnd.motionwright.manim-source+zip"
WIDTH, HEIGHT = 640, 360
FRAME_BYTES = WIDTH * HEIGHT * 3
COLORS = ((0, 0, 0), (32, 64, 224), (224, 32, 64), (32, 204, 64))
IDS = [f"10000000-0000-4000-8000-{n:012d}" for n in (1, 2, 3)]
MAX_VIDEO_BYTES = 512 * 1024 * 1024
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_ENTRY_BYTES = 256 * 1024
MAX_REPORT_INPUT = 4 * 1024 * 1024
MAX_CHANNEL_ERROR = 12
BOUNDARY_MARGIN = 2
MIN_CORE_FRACTION = 0.995
MIN_BACKGROUND_FRACTION = 0.999
BBOX_EDGE_ERROR = 2
MEMBERS = {"plan.json", "scene.py", "render-config.json", "assets.lock.json", "manifest.json"}


class ReadbackError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise ReadbackError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate_json_key:{key}")
        result[key] = value
    return result


def parse_json(data):
    try:
        return json.loads(data, object_pairs_hook=strict_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ReadbackError("nonfinite_json")))
    except (ValueError, UnicodeError) as error:
        raise ReadbackError("invalid_json") from error


def read_regular(path, limit):
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), "input_not_regular")
    size = path.stat().st_size
    require(0 < size <= limit, "input_size_out_of_bounds")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    require(len(data) == size and path.stat().st_size == size, "input_changed_while_reading")
    return data


def rational(value):
    require(isinstance(value, dict) and set(value) == {"num", "den"}, "rational_schema")
    require(type(value["num"]) is str and type(value["den"]) is str, "rational_wire_must_be_strings")
    n, d = int(value["num"]), int(value["den"])
    require(str(n) == value["num"] and str(d) == value["den"] and d > 0,
            "rational_wire_not_canonical")
    require(-(2**63) <= n < 2**63 and d < 2**63 and math.gcd(n, d) == 1, "rational_not_reduced")
    return Fraction(n, d)


def run_bounded(argv, max_stdout, timeout_seconds=120):
    """Supervise only this new read-only decoder, with bounded memory and stderr."""
    started = time.monotonic()
    process = subprocess.Popen([str(arg) for arg in argv], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    outputs = [bytearray(), bytearray()]
    overflow = threading.Event()
    errors = []

    def drain(stream, index, limit):
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                room = max(0, limit + 1 - len(outputs[index]))
                outputs[index].extend(chunk[:room])
                if len(outputs[index]) > limit:
                    overflow.set()
                    process.kill()
                    break
        except OSError as error:
            errors.append(str(error))
        finally:
            stream.close()

    threads = [threading.Thread(target=drain, args=(process.stdout, 0, max_stdout), daemon=True),
               threading.Thread(target=drain, args=(process.stderr, 1, 65536), daemon=True)]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait(timeout=10)
    for thread in threads:
        thread.join(timeout=10)
    require(not any(thread.is_alive() for thread in threads), "decoder_pipe_not_closed")
    require(not timed_out, "readonly_decoder_timeout")
    require(not overflow.is_set(), "readonly_decoder_output_budget")
    require(not errors, "readonly_decoder_pipe_error")
    require(process.returncode == 0,
            f"readonly_decoder_failed:{process.returncode}:{bytes(outputs[1]).decode('utf-8', 'replace')[:2000]}")
    return bytes(outputs[0]), {"exit_code": process.returncode, "elapsed_seconds": time.monotonic() - started,
                              "stdout_bytes": len(outputs[0]), "stderr_bytes": len(outputs[1]),
                              "stderr_sha256": sha(outputs[1]), "whole_process_completed": True}


def expected_fixture(phase):
    require(phase in {"original", "reopen", "edit", "edited-reopen", "edge25"}, "unknown_fixture_phase")
    changed = phase in {"edit", "edited-reopen"}
    fps, count = (25, 29) if phase == "edge25" else (12, 24)
    return changed, fps, count


def shape_distances(x, y, changed):
    def rectangle(x0, y0, x1, y1):
        if x0 <= x <= x1 and y0 <= y <= y1:
            return min(x - x0, x1 - x, y - y0, y1 - y)
        return -math.hypot(max(x0-x, 0, x-x1), max(y0-y, 0, y-y1))
    red_left = 110 if changed else 80
    return (rectangle(460, 40, 560, 90), rectangle(red_left, 60, red_left + 200, 180),
            40 - math.hypot(x-290, y-170))


def expected_masks(changed):
    labels = bytearray(WIDTH * HEIGHT)
    cores = bytearray(WIDTH * HEIGHT)
    allowed = bytearray(WIDTH * HEIGHT)
    counts = [0, 0, 0, 0]
    for y in range(HEIGHT):
        for x in range(WIDTH):
            index = y * WIDTH + x
            distances = shape_distances(x + .5, y + .5, changed)
            label = 0
            for shape, distance in enumerate(distances, start=1):
                if distance >= 0:
                    label = shape
                if distance >= -BOUNDARY_MARGIN:
                    allowed[index] |= 1 << shape
            labels[index] = label
            if all(abs(distance) > BOUNDARY_MARGIN for distance in distances):
                cores[index] = 1
                counts[label] += 1
    return labels, cores, allowed, counts


def check_pixels(frame, masks, changed):
    require(len(frame) == FRAME_BYTES, "decoded_frame_size")
    labels, cores, allowed, counts = masks
    matched = [0, 0, 0, 0]
    boxes = [[WIDTH, HEIGHT, -1, -1] for _ in range(4)]
    stray = [0, 0, 0, 0]
    for index, label in enumerate(labels):
        offset = index * 3
        rgb = frame[offset:offset+3]
        expected = COLORS[label]
        if cores[index] and all(abs(rgb[channel]-expected[channel]) <= MAX_CHANNEL_ERROR for channel in range(3)):
            matched[label] += 1
        observed = None
        for shape in (1, 2, 3):
            color = COLORS[shape]
            if all(abs(rgb[channel]-color[channel]) <= MAX_CHANNEL_ERROR for channel in range(3)):
                observed = shape
                break
        if observed is not None:
            y, x = divmod(index, WIDTH)
            box = boxes[observed]
            box[0] = min(box[0], x); box[1] = min(box[1], y)
            box[2] = max(box[2], x+1); box[3] = max(box[3], y+1)
            if not (allowed[index] & (1 << observed)):
                stray[observed] += 1
    fractions = [matched[i]/counts[i] for i in range(4)]
    require(fractions[0] >= MIN_BACKGROUND_FRACTION, f"background_content_mismatch:{fractions[0]}")
    for i in (1, 2, 3):
        require(fractions[i] >= MIN_CORE_FRACTION, f"shape_{i}_content_mismatch:{fractions[i]}")
        require(stray[i] == 0, f"shape_{i}_outside_expected_geometry:{stray[i]}")
    left = 110 if changed else 80
    expected_boxes = [None, [460,40,560,90], [left,60,left+200,180], [250,130,330,210]]
    for i in (1, 2, 3):
        require(all(abs(a-b) <= BBOX_EDGE_ERROR for a,b in zip(boxes[i],expected_boxes[i])),
                f"shape_{i}_bbox_mismatch:{boxes[i]}")
    for x,y,label in [(120,90,2),(271,163,3),(500,60,1),(400,280,0)]:
        for yy in range(y-3,y+4):
            for xx in range(x-3,x+4):
                offset=(yy*WIDTH+xx)*3
                require(all(abs(frame[offset+c]-COLORS[label][c]) <= MAX_CHANNEL_ERROR for c in range(3)),
                        f"mandatory_neighborhood_mismatch:{x}:{y}:{label}")
    if changed:
        for x0,y0,x1,y1,label in [(83,80,107,120,0),(283,80,307,120,2)]:
            for y in range(y0,y1):
                for x in range(x0,x1):
                    offset=(y*WIDTH+x)*3
                    require(all(abs(frame[offset+c]-COLORS[label][c]) <= MAX_CHANNEL_ERROR for c in range(3)),
                            "edited_source_difference_not_visible")
    return {"matching_core_fractions": fractions, "observed_visible_bounds": boxes[1:],
            "stray_saturated_pixels": stray[1:]}


def inspect_sources(producer, bundle, native_bytes, phase):
    changed, fps, count = expected_fixture(phase)
    require(producer.get("schema") == "motionwright.manim.native-source-producer/2" and producer.get("phase") == phase,
            "producer_schema_or_phase")
    source = producer["result"]["source"]
    carrier = source["source_artifact"]
    require(carrier["sha256"] == sha(bundle) and carrier["bytes"] == len(bundle) and carrier["media_type"] == MIME,
            "source_carrier_digest_mismatch")
    require(source["native_readback_artifact"]["sha256"] == sha(native_bytes)
            and source["native_readback_artifact"]["bytes"] == len(native_bytes), "native_readback_digest_mismatch")
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        entries = archive.infolist()
        require(len(entries) == 5 and {entry.filename for entry in entries} == MEMBERS and not archive.comment,
                "source_archive_entry_set")
        data = {}
        for entry in entries:
            require(entry.compress_type == zipfile.ZIP_STORED and not entry.is_dir() and not entry.flag_bits & 1
                    and 0 < entry.file_size <= MAX_ENTRY_BYTES and not entry.comment, "source_archive_entry_invalid")
            require(entry.filename not in data, "source_archive_duplicate_entry")
            data[entry.filename] = archive.read(entry)
    closure = source["source_closure"]
    require(closure["plan_schema_version"] == 2 and closure["compiler_contract_id"] == COMPILER, "source_compiler_contract")
    rows = {row["path"]: row for row in closure["entries"]}
    require(len(closure["entries"]) == 5 and set(rows) == MEMBERS, "source_closure_entry_set")
    for path, content in data.items():
        require(rows[path]["sha256"] == sha(content) and rows[path]["bytes"] == len(content), "source_closure_content_mismatch")
    plan = parse_json(data["plan.json"])
    require(plan == source["source_plan"] and closure["plan_sha256"] == sha(data["plan.json"]), "source_plan_digest_mismatch")
    require(plan["schema_version"] == 2 and plan["profile_id"] == PROFILE, "source_plan_schema")
    require(plan["render_profile"] == {"width":WIDTH,"height":HEIGHT,"frame_rate":fps}, "source_render_profile")
    clock = plan["clock"]
    require(rational(clock["timeline_start"]) == 0 and rational(clock["duration"]) == Fraction(count,fps)
            and rational(clock["frame_rate"]) == fps and clock["local_start_frame"] == 0
            and clock["frame_count"] == count and clock["project_start_frame"] == 0
            and clock["project_end_frame_exclusive"] == count, "source_exact_clock")
    project = producer["project"]
    require(plan["binding"]["project_id"] == project["id"] and plan["binding"]["generation"] == project["generation"]
            and plan["binding"]["revision"] == project["revision"], "source_current_project_binding")
    scenes = [scene for scene in project["scenes"] if scene["id"] == plan["binding"]["scene_id"]]
    require(len(scenes) == 1 and len(scenes[0]["nodes"]) == 3, "source_current_scene")
    require([node["node_id"] for node in plan["nodes"]] == IDS, "source_full_uuid_order")
    expected = [(750,390,240,240,"#20CC40",20,"circle"),
                (330 if changed else 240,180,600,360,"#E02040",10,"rectangle"),
                (1380,120,300,150,"#2040E0",0,"rectangle")]
    for ordinal, (node, canonical, row) in enumerate(zip(plan["nodes"], scenes[0]["nodes"], expected)):
        x,y,w,h,fill,z,kind = row
        require((node["x_px"],node["y_px"],node["width_px"],node["height_px"],node["fill_hex"],node["z_index"],node["source_kind"]) == row,
                "source_plan_geometry_or_style")
        require(node["canonical_ordinal"] == ordinal and node["display_name"] == canonical["name"], "source_identity_projection")
        require((canonical["id"],canonical["x"],canonical["y"],canonical["width"],canonical["height"],canonical["style"]["fill"],canonical["z_index"],canonical["kind"])
                == (node["node_id"],x,y,w,h,fill,z,kind), "source_canonical_content")
        require(canonical["opacity"] == 1 and canonical["rotation_deg"] == 0 and not canonical["keyframes"]
                and canonical["parent_id"] is None and not canonical["relations"] and canonical["text"] is None
                and canonical["style"]["stroke"] is None and canonical["style"]["stroke_width"] == 0, "unrepresented_canonical_control")
    observed = parse_json(native_bytes)
    require(observed == source["native_readback"], "native_getter_record_changed")
    require(observed["schema_version"] == 1 and observed["profile_id"] == PROFILE and observed["scene_class"] == "MotionwrightScene",
            "native_getter_record_schema")
    require([item["name"] for item in observed["objects"]] == ["mw_"+IDS[index].replace("-", "") for index in (2,1,0)],
            "native_identity_or_z_order")
    for actual, index in zip(observed["objects"], (2,1,0)):
        x,y,w,h,fill,z,kind = expected[index]
        center = [(x+w/2-960)/135, (540-y-h/2)/135, 0]
        require(all(abs(a-b) <= 1e-9 for a,b in zip(actual["center"],center))
                and abs(actual["width"]-w/135) <= 1e-9 and abs(actual["height"]-h/135) <= 1e-9
                and actual["primitive"].lower() == kind and actual["z_index"] == z
                and actual["fill_hex"].upper() == fill and actual["fill_opacity"] == 1
                and actual["stroke_width"] == 0 and actual["stroke_opacity"] == 0, "native_getter_content")
    return {"source_bundle_sha256":sha(bundle),"source_plan_sha256":sha(data["plan.json"]),
            "native_readback_sha256":sha(native_bytes),"origin_binding":producer["source_origin"]["origin_binding"],
            "current_binding":plan["binding"],"source_entry_count":5,"source_projection_executed_by_this_reader":False}


def inspect_probe(probe, fps, count):
    streams = probe.get("streams", [])
    require(len(streams) == 1 and streams[0].get("codec_type") == "video", "contribution_has_audio_or_other_streams")
    video = streams[0]
    require(video.get("codec_name") == "h264" and video.get("pix_fmt") == "yuv420p"
            and (video.get("width"),video.get("height")) == (WIDTH,HEIGHT), "native_video_codec_or_dimensions")
    require(Fraction(video["r_frame_rate"]) == fps and Fraction(video["avg_frame_rate"]) == fps, "native_video_frame_rate")
    base = Fraction(video["time_base"])
    require(base > 0 and int(video.get("start_pts", -1)) == 0
            and int(video["duration_ts"])*base == Fraction(count,fps), "native_stream_interval")
    frames = probe.get("frames", [])
    require(len(frames) == count, f"native_frame_count:{len(frames)}:{count}")
    for index, frame in enumerate(frames):
        require(frame.get("media_type") == "video" and frame.get("stream_index") == video["index"], "native_foreign_frame")
        require(int(frame["best_effort_timestamp"])*base == Fraction(index,fps), f"native_frame_pts:{index}")
        if "pts" in frame:
            require(int(frame["pts"])*base == Fraction(index,fps), f"native_frame_exact_pts:{index}")
    return {"frame_count":len(frames),"frame_rate":f"{fps}/1","time_base":str(base),
            "first_pts":"0/1","end_exclusive":str(Fraction(count,fps)),"audio_stream_count":0,
            "codec":video["codec_name"],"pixel_format":video["pix_fmt"],"all_frame_timestamps_verified":True}


def png(rgb):
    def chunk(kind, content):
        return struct.pack(">I",len(content))+kind+content+struct.pack(">I",zlib.crc32(kind+content)&0xffffffff)
    scan=b"".join(b"\0"+rgb[y*WIDTH*3:(y+1)*WIDTH*3] for y in range(HEIGHT))
    return b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",WIDTH,HEIGHT,8,2,0,0,0))+chunk(b"IDAT",zlib.compress(scan,9))+chunk(b"IEND",b"")


def inspect(args):
    changed,fps,count=expected_fixture(args.phase)
    evidence_bytes=read_regular(args.producer,MAX_REPORT_INPUT)
    producer=parse_json(evidence_bytes)
    bundle=read_regular(args.source,MAX_SOURCE_BYTES)
    native=read_regular(args.native_readback,MAX_ENTRY_BYTES)
    source=inspect_sources(producer,bundle,native,args.phase)
    video=Path(args.video).resolve(strict=True)
    data=read_regular(video,MAX_VIDEO_BYTES)
    digest=sha(data)
    expected=producer["result"]["artifact"]
    require(expected["sha256"]==digest and expected["bytes"]==len(data),"native_video_not_bound_to_producer")
    del data
    ffprobe=Path(shutil.which(args.ffprobe) or args.ffprobe).resolve(strict=True)
    ffmpeg=Path(shutil.which(args.ffmpeg) or args.ffmpeg).resolve(strict=True)
    versions={}
    for name,tool in [("ffprobe",ffprobe),("ffmpeg",ffmpeg)]:
        text,run=run_bounded([tool,"-version"],65536,15)
        versions[name]={"sha256":sha(read_regular(tool,MAX_VIDEO_BYTES)),"version":text.decode("utf-8","replace").splitlines()[0],"process":run}
    probe_bytes,probe_run=run_bounded([ffprobe,"-v","error","-show_streams","-show_format","-show_frames","-of","json",video],8*1024*1024)
    media=inspect_probe(parse_json(probe_bytes),fps,count)
    raw,decode_run=run_bounded([ffmpeg,"-v","error","-xerror","-nostdin","-i",video,
                               "-map","0:v:0","-vsync","0","-pix_fmt","rgb24","-f","rawvideo","pipe:1"],FRAME_BYTES*(count+1))
    require(len(raw)==FRAME_BYTES*count,"full_decode_frame_count_mismatch")
    masks=expected_masks(changed)
    measurements=[]
    for index in range(count):
        try:
            frame=raw[index*FRAME_BYTES:(index+1)*FRAME_BYTES]
            measurement=check_pixels(frame,masks,changed)
            measurement["index"]=index;measurement["rgb24_sha256"]=sha(frame);measurements.append(measurement)
        except ReadbackError as error:
            raise ReadbackError(f"decoded_frame_{index}:{error}") from error
    require(sha(read_regular(video,MAX_VIDEO_BYTES))==digest,"native_video_changed_during_readback")
    images=[]
    if args.frames:
        destination=Path(args.frames)
        require(destination.is_dir() and not destination.is_symlink(),"frame_output_directory")
        for index in sorted({0,count-1}):
            name=f"manim-{args.phase}-frame-{index:04d}.png"
            content=png(raw[index*FRAME_BYTES:(index+1)*FRAME_BYTES])
            with (destination/name).open("xb") as stream:stream.write(content)
            images.append({"name":name,"bytes":len(content),"sha256":sha(content)})
    return {"schema":"motionwright.manim.static-source-readback/1","status":"PASS","phase":args.phase,
            "classification":"REAL_DECODED_NATIVE_FIXTURE_CONTENT_READBACK_NOT_RELEASE_ACCEPTANCE",
            "producer_record_sha256":sha(evidence_bytes),"video_sha256":digest,"video_bytes":expected["bytes"],
            "source":source,"media":media,"decoder_identity":versions,"probe_process":probe_run,"decode_process":decode_run,
            "decoded_rgb24_bytes":len(raw),"decoded_rgb24_sha256":sha(raw),"frames":measurements,"images":images,
            "thresholds":{"rgb_channel_error":MAX_CHANNEL_ERROR,"boundary_margin":BOUNDARY_MARGIN,
                          "min_shape_core_fraction":MIN_CORE_FRACTION,"min_background_fraction":MIN_BACKGROUND_FRACTION,"bbox_edge_error":BBOX_EDGE_ERROR},
            "losses":["static shapes only","opaque black matte","no audio or captions","binary64 geometry projection",
                      "static identical-frame permutations are not observable animation evidence"],
            "human_acceptance":False,"external_rights_certified":False,"native_job_retried":False}


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.masks=expected_masks(False)
        cls.frame=b"".join(bytes(COLORS[label]) for label in cls.masks[0])

    def test_complete_analytic_control_and_all_black_rejection(self):
        check_pixels(self.frame,self.masks,False)
        with self.assertRaises(ReadbackError):check_pixels(bytes(FRAME_BYTES),self.masks,False)

    def test_wrong_z_and_wrong_color_cannot_pass(self):
        for label in (0,2):
            frame=bytearray(self.frame)
            for y in range(150,180):
                for x in range(255,278):
                    offset=(y*WIDTH+x)*3;frame[offset:offset+3]=bytes(COLORS[label])
            with self.assertRaises(ReadbackError):check_pixels(frame,self.masks,False)

    def test_unedited_source_does_not_pass_edit_oracle(self):
        with self.assertRaises(ReadbackError):check_pixels(self.frame,expected_masks(True),True)

    def test_rational_wire_rejects_numbers_and_duplicate_json(self):
        self.assertEqual(rational({"num":"29","den":"25"}),Fraction(29,25))
        for value in ({"num":29,"den":25},{"num":"2","den":"2"},{"num":"01","den":"1"}):
            with self.assertRaises(ReadbackError):rational(value)
        with self.assertRaises(ReadbackError):parse_json(b'{"a":1,"a":2}')

    def test_missing_audio_and_pts_negative_cases_are_explicit(self):
        video={"index":0,"codec_type":"video","codec_name":"h264","pix_fmt":"yuv420p","width":640,"height":360,
               "r_frame_rate":"12/1","avg_frame_rate":"12/1","time_base":"1/12","start_pts":0,"duration_ts":24}
        frames=[{"media_type":"video","stream_index":0,"best_effort_timestamp":i,"pts":i} for i in range(24)]
        control={"streams":[video],"frames":frames};inspect_probe(control,12,24)
        with self.assertRaises(ReadbackError):inspect_probe({"streams":[video,{"codec_type":"audio"}],"frames":frames},12,24)
        with self.assertRaises(ReadbackError):inspect_probe({"streams":[video],"frames":frames[:-1]},12,24)
        bad=parse_json(json.dumps(control));bad["frames"][12]["pts"]=11
        with self.assertRaises(ReadbackError):inspect_probe(bad,12,24)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test",action="store_true")
    parser.add_argument("--phase",choices=["original","reopen","edit","edited-reopen","edge25"])
    for name in ("video","producer","source","native-readback","report","frames"):
        parser.add_argument("--"+name,type=Path)
    parser.add_argument("--ffmpeg",default="ffmpeg");parser.add_argument("--ffprobe",default="ffprobe")
    args=parser.parse_args(argv)
    if args.self_test:
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ReaderTests))
        print(json.dumps({"classification":"SELF_TEST_NO_NATIVE_PROVIDER","passed":result.testsRun-len(result.failures)-len(result.errors),
                          "failed":len(result.failures)+len(result.errors),"native_execution_performed":False}))
        return 0 if result.wasSuccessful() else 1
    require(all(getattr(args,name) is not None for name in ("phase","video","producer","source","native_readback","report")), "missing_reader_arguments")
    require(args.report.parent.is_dir() and not args.report.exists(), "report_destination_must_be_new")
    try:
        report=inspect(args);code=0
    except (ReadbackError,OSError,KeyError,TypeError,ValueError,zipfile.BadZipFile) as error:
        report={"schema":"motionwright.manim.static-source-readback/1","status":"FAIL","phase":args.phase,
                "error":str(error),"native_job_retried":False,"human_acceptance":False};code=1
    with args.report.open("x",encoding="utf-8") as stream:json.dump(report,stream,indent=2,allow_nan=False);stream.write("\n")
    print(json.dumps({"status":report["status"],"report":str(args.report),"native_job_retried":False}))
    return code


if __name__=="__main__":
    raise SystemExit(main())
