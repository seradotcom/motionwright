"""Independent decoded-pixel admission for MLT H.264/AAC with native subtitles.

CI ONLY. The existing Semwright Native SDK first renders verified native
caption subjects to source PNG, then the MLT Driver Host encodes FFV1 + MP4.
This observer only reads the ACTUAL two owner-produced video artifacts through
FFmpeg. It never adds captions, changes timelines, writes an MP4, or obtains a
provider grant. An H.264 stream is inherently lossy, so we compare only the
native caption-specific high-contrast mask with a bounded error budget.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

WIDTH, HEIGHT = 1280, 720
MAX_FRAME_BYTES = WIDTH * HEIGHT * 3
SUPPORTED = frozenset((0, 14, 15, 31, 32))
# Native text is placed by the pinned Film authoring safe-area grammar.
ROI = (80, 440, 1200, 700)


class CaptionedMasterPixelsNotVerified(AssertionError):
    pass


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _frame(path: Path, index: int, ffmpeg: Path) -> bytes:
    if index not in SUPPORTED:
        raise CaptionedMasterPixelsNotVerified("Unapproved decoded frame index")
    if path.is_symlink() or not path.is_file() or not ffmpeg.is_file():
        raise CaptionedMasterPixelsNotVerified("Original bound native video/decoder missing")
    result = subprocess.run(
        [str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
         "-i", str(path), "-an", "-sn", "-dn",
         "-vf", rf"select=eq(n\,{index})", "-vsync", "0", "-frames:v", "1",
         "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45, check=False,
    )
    if result.returncode != 0 or len(result.stdout) != MAX_FRAME_BYTES:
        raise CaptionedMasterPixelsNotVerified("Original native video frame decoder did not return one exact RGB frame")
    return result.stdout


def _caption_mask(
    previous: bytes,
    active: bytes,
    master: bytes,
    *,
    width: int = WIDTH,
    height: int = HEIGHT,
    roi: tuple[int, int, int, int] = ROI,
    minimum: int = 350,
) -> dict:
    if not (len(previous) == len(active) == len(master) == width * height * 3):
        raise CaptionedMasterPixelsNotVerified("Native decoded RGB frame size differs")
    left, top, right, bottom = roi
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise CaptionedMasterPixelsNotVerified("Caption viewing region is out of bounds")
    count = 0
    error_active = error_previous = contrast = 0
    for y in range(top, bottom):
        for x in range(left, right):
            i = (y * width + x) * 3
            delta = max(abs(previous[i + j] - active[i + j]) for j in range(3))
            if delta < 48:
                continue
            count += 1
            contrast += delta
            error_active += max(abs(master[i + j] - active[i + j]) for j in range(3))
            error_previous += max(abs(master[i + j] - previous[i + j]) for j in range(3))
    if count < minimum or count > min(100_000, (right - left) * (bottom - top) // 3):
        raise CaptionedMasterPixelsNotVerified("Real FFV1 subtitle is absent or changes too much of the native scene")
    if contrast < count * 52 or error_previous <= count * 20:
        raise CaptionedMasterPixelsNotVerified("Native source mask cannot independently distinguish active caption")
    if error_active * 3 >= error_previous * 2 or error_active > count * 48:
        raise CaptionedMasterPixelsNotVerified("Final MLT H.264 did not retain the actual native caption mask")
    return {
        "native_caption_mask_pixels": count,
        "masked_native_original_contrast_mean": round(contrast / count, 3),
        "masked_lossy_master_error_mean": round(error_active / count, 3),
        "masked_inactive_reference_error_mean": round(error_previous / count, 3),
    }


def _inactive_comparison(reference: bytes, master: bytes) -> float:
    if len(reference) != MAX_FRAME_BYTES or len(master) != MAX_FRAME_BYTES:
        raise CaptionedMasterPixelsNotVerified("Inactive source frame has invalid RGB geometry")
    # A lossy H.264 encoder must retain the *original* native image rather than
    # substituting its own blank frame or a second WebView compositor.
    diff = sum(abs(reference[i] - master[i]) for i in range(0, len(reference), 4))
    mean = diff / ((len(reference) + 3) // 4)
    if mean > 25:
        raise CaptionedMasterPixelsNotVerified("Inactive H.264 native frame materially diverges from owner FFV1")
    return round(mean, 3)


def prove_native_captioned_master(
    ffv1: Path,
    master_mp4: Path,
    ffmpeg: Path,
    *,
    source_sha: str,
    semwright_sha: str,
    selected_cues: int,
) -> dict:
    if (
        selected_cues != 2
        or any(len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha)
               for sha in (source_sha, semwright_sha))
    ):
        raise CaptionedMasterPixelsNotVerified("Captioned master source identity/cue grammar is invalid")
    original = {i: _frame(ffv1, i, ffmpeg) for i in SUPPORTED}
    mastered = {i: _frame(master_mp4, i, ffmpeg) for i in (0, 15, 32)}
    inactive = _inactive_comparison(original[0], mastered[0])
    first = _caption_mask(original[14], original[15], mastered[15])
    last = _caption_mask(original[31], original[32], mastered[32])
    return {
        "schema": "motionwright-v05-native-captioned-master-pixels/1",
        "motionwright_source_sha": source_sha,
        "semwright_source_sha": semwright_sha,
        "captioned_master_decoded_pixels": "PASS",
        "original_native_ffv1_frame_indices": [0, 14, 15, 31, 32],
        "decoded_h264_frame_indices": [0, 15, 32],
        "native_cue_count": selected_cues,
        "inactive_native_frame_mean_abs_byte_delta": inactive,
        "active_caption_frames": {"15": first, "32": last},
        "original_ffv1_sha256": _digest(ffv1),
        "actual_master_mp4_sha256": _digest(master_mp4),
        "original_owner_media_or_transcript_published": False,
        "human_readability": "NOT_RUN",
        "voice_speech_authenticity": "NOT_VERIFIED",
        "commercial_release_authorized": False,
    }



# Original native hybrid readback. The existing captioned33-frame oracle above
# is unchanged. Decoder, held-file reader and source PCM oracle are reused.
HYBRID_PIXEL_CONTRACT = {
    "schema":"motionwright.native.hybrid-pixel-policy/1",
    "width":1920, "height":1080, "frame_count":180,
    "source_segment_frames":[60,60,60], "cut_frames":[60,120],
    "active_center_rgb_minimum":96, "inactive_rgb_maximum":32,
    "other_active_channels_maximum":64, "alpha_required":255,
    "probe_offsets":[[-1,-1],[0,-1],[1,-1],[-1,0],[0,0],[1,0],[-1,1],[0,1],[1,1]],
    "source_geometry":[[400,420,180,140],[750,420,180,140],[1100,420,180,140]],
    "whole_frame_error_policy":"record_all_RGB_differences_without_quality_threshold",
    "maximum_png_decoder_invocations":540, "decode_start_deadline_seconds":1800,
    "last_decoder_and_cleanup_allowance_seconds":40,
    "maximum_png_decoder_stdout_bytes":4478976000,
    "equal_original_png_bytes_may_reuse_decode":True,
    "perceptual_quality_or_human_approval":False,
}


def _hybrid_need(condition, code):
    if not condition:
        raise AssertionError(code)


def hybrid_rgb_difference(source, observed, *, width, height):
    """Numeric loss observation, never a perceptual tolerance or approval."""
    _hybrid_need(type(width) is int and type(height) is int
                 and 1 <= width <= 1920 and 1 <= height <= 1080,
                 "hybrid_difference_dimensions")
    size = width * height * 4
    _hybrid_need(type(source) is bytes and type(observed) is bytes
                 and len(source) == len(observed) == size, "hybrid_difference_buffer")
    total = maximum = changed = 0
    # Skip equal byte blocks without dropping any pixel from the denominator.
    # This bounds the static fixture's work without assuming frames are equal.
    for start in range(0, size, 4096):
        a, b = source[start:start+4096], observed[start:start+4096]
        if a == b:
            continue
        for offset in range(0,len(a),4):
            delta = [abs(a[offset+c] - b[offset+c]) for c in (0,1,2)]
            total += sum(delta)
            maximum = max(maximum,*delta)
            changed += any(delta)
    return {"rgb_sample_count":width*height*3,"absolute_rgb_error_sum":total,
            "mean_absolute_rgb_error":total/(width*height*3),
            "maximum_absolute_rgb_error":maximum,"changed_pixel_count":changed,
            "pixel_equal":source == observed,"quality_threshold_applied":False}


def hybrid_frame_presence(rgba, frame, *, policy=HYBRID_PIXEL_CONTRACT):
    """Fixed before-render content/cut screen; not a pixel-equality claim."""
    width,height = policy["width"],policy["height"]
    _hybrid_need(type(frame) is int and 0 <= frame < policy["frame_count"]
                 and type(rgba) is bytes and len(rgba) == width*height*4,
                 "hybrid_presence_input")
    _hybrid_need(rgba[3::4] == b"\xff"*(width*height), "hybrid_nonopaque_original_frame")
    selected = frame // 60
    for index,(x,y,w,h) in enumerate(policy["source_geometry"]):
        for dx,dy in policy["probe_offsets"]:
            point = ((y+h//2+dy)*width+x+w//2+dx)*4
            rgb = rgba[point:point+3]
            if index == selected:
                _hybrid_need(rgb[index] >= policy["active_center_rgb_minimum"]
                             and all(rgb[c] <= policy["other_active_channels_maximum"]
                                     for c in (0,1,2) if c != index),
                             "hybrid_selected_native_color_or_cut_changed")
            else:
                _hybrid_need(max(rgb) <= policy["inactive_rgb_maximum"],
                             "hybrid_foreign_native_contribution_overlaps_cut")
    for x,y in [(20,20),(width-21,20),(20,height-21),(width-21,height-21)]:
        offset=(y*width+x)*4
        _hybrid_need(max(rgba[offset:offset+3]) <= policy["inactive_rgb_maximum"],
                     "hybrid_authored_black_matte_changed")
    return {"frame":frame,"selected_native_contribution":selected,
            "all_pixels_opaque":True,"predeclared_center_and_matte_screen":True}


def inspect_original_hybrid_pixels(output_root, caller_root, expected_result, *, ffmpeg,
                                   executable_sha256, retain_decode):
    """Join the original three families, decode held PNG bytes, retain differences.

    Every frame is bound to its original native report/manifest and output byte
    identity. Identical original PNG bytes can reuse an already retained decode;
    the output separately states new decodes and these exact-byte reuses.
    """
    import hashlib
    import time
    from collections import OrderedDict
    from pathlib import Path
    import native_evidence_projection as core
    from native_direct_mediated_parity import decoded_rgba_png_capture
    from native_motion_canvas_roundtrip import validate_png_crc_bytes

    _hybrid_need(callable(retain_decode),"hybrid_original_decode_custodian_required")
    _hybrid_need(type(expected_result) is dict and expected_result.get("schema") == "motionwright.native.hybrid-av-summary/1",
                 "hybrid_original_result_schema")
    for name,value in {"frame_count":180,"width":1920,"height":1080,"fps_num":30,"fps_den":1,
                       "measured_original_audio_frames":288000}.items():
        _hybrid_need(type(expected_result.get(name)) is int and expected_result[name] == value,
                     "hybrid_original_result_clock")
    _hybrid_need(expected_result.get("source_segment_frames") == [60,60,60]
                 and expected_result.get("source_renderers") == ["motion_canvas","manim_static","motion_canvas"]
                 and expected_result.get("editable_source_reopened_unchanged") is True
                 and all(expected_result.get(k) is False for k in ("automatic_retry","physical_acceptance","human_approval")),
                 "hybrid_original_result_scope")
    handles = core.RootHandles({"output":str(Path(output_root)),"caller":str(Path(caller_root))})
    primary = None
    reads = []
    def sha(raw):return hashlib.sha256(raw).hexdigest()
    def read(root,name,maximum,expected_sha=None,expected_bytes=None):
        raw,identity=handles.read(root,core.member_path(name),maximum)
        _hybrid_need(expected_sha is None or sha(raw) == expected_sha,"hybrid_original_digest_changed")
        _hybrid_need(expected_bytes is None or len(raw) == expected_bytes,"hybrid_original_size_changed")
        reads.append({"root":root,"path":name,"sha256":sha(raw),"bytes":len(raw),"held_identity":identity})
        return raw
    def document(root,name,maximum=16*1024*1024,expected_sha=None,expected_bytes=None):
        return core.strict(read(root,name,maximum,expected_sha,expected_bytes))
    def original_result(name):
        value=document("caller",name)
        _hybrid_need(type(value) is dict and value.get("encoding") == "serialized_native_return_not_wire"
                     and value.get("error") is None and type(value.get("result")) is dict
                     and value.get("automatic_retry") is False
                     and value.get("host_streams_or_receiver_closure_proved") is False,
                     "hybrid_original_return_not_successfully_retained")
        return value["result"]
    def artifact(row):
        _hybrid_need(type(row) is dict and row.get("root") == "output"
                     and type(row.get("bytes")) is int and 0 < row["bytes"] <= 256_000_000,
                     "hybrid_native_artifact_identity")
        read("output",row["path"],256_000_000,row["sha256"],row["bytes"])
        return row
    def observed_frames(observation,media,count):
        receipt=observation["original_receipt"]
        _hybrid_need(receipt["root"] == "output" and receipt["path"] == media["path"]+".color-observation.json",
                     "hybrid_native_color_receipt_path")
        observed=document("output",receipt["path"],16*1024*1024,receipt["sha256"],receipt["bytes"])
        _hybrid_need(observed == observation["document"] and observed["artifact"] == media
                     and observed["frame_count"] == count and len(observed["frames"]) == count
                     and observed["canonical_fps_num"] == 30 and observed["canonical_fps_den"] == 1
                     and observed["stream"]["width"] == 1920 and observed["stream"]["height"] == 1080
                     and observed["frame_path_prefix"] == media["path"]+"."
                     and type(observed["exact_clock"]) is bool,
                     "hybrid_complete_native_color_observation_changed")
        result=[]
        for index,row in enumerate(observed["frames"]):
            _hybrid_need(row["file"] == f"decoded-{index:06}.png", "hybrid_native_frame_order")
            result.append({"path":media["path"]+"."+row["file"],"sha256":row["png_sha256"],
                           "bytes":row["png_bytes"],"rgba_sha256":row["rgba_sha256"]})
        return result,observed
    try:
        private=document("caller","result.private.json")
        _hybrid_need(private == expected_result,"hybrid_stdout_does_not_match_private_original_result")
        project=document("caller","source-project.original.json")
        reopened=document("caller","source-project.reopened.json")
        _hybrid_need(project == reopened and project["id"] == expected_result["project_id"]
                     and project["generation"] == expected_result["generation"] and project["revision"] == expected_result["revision"],
                     "hybrid_editable_source_changed_on_reopen")
        plan=document("caller","hybrid-plan.source-only.json")
        declared=document("caller","declared-readback-contract.json")
        rendered=original_result("hybrid-render.serialized.json")
        visual=original_result("hybrid-timeline.serialized.json")
        master=original_result("hybrid-master.serialized.json")
        _hybrid_need(plan == rendered["plan"] and plan["project_sha256"] == expected_result["project_sha256"]
                     and declared["project_sha256"] == plan["project_sha256"]
                     and declared["cut_frames"] == [60,120] and declared["frame_count"] == 180
                     and declared["pixel_difference_policy"] == "retain_all_original_measurements"
                     and len(plan["contributions"]) == 3 and len(project["scenes"]) == 3,
                     "hybrid_original_source_plan_changed")
        for index,(part,scene) in enumerate(zip(plan["contributions"],project["scenes"])):
            _hybrid_need(part["output_start_frame"] == index*60 and part["frame_count"] == 60
                         and part["renderer"] == ("manim_static" if index==1 else "motion_canvas")
                         and len(scene["nodes"]) == 1,"hybrid_original_partition")
            node=scene["nodes"][0]
            _hybrid_need([node[k] for k in ("x","y","width","height")] == HYBRID_PIXEL_CONTRACT["source_geometry"][index]
                         and node["style"]["fill"] == ["#FF0000","#00FF00","#0000FF"][index],
                         "hybrid_original_geometry_or_color_changed")
        sources=[]
        mc=rendered["motion_canvas"]["segments"]
        _hybrid_need(len(mc)==2 and len(rendered["manim"])==1,"hybrid_original_source_roster")
        for index,part in enumerate(plan["contributions"]):
            if part["renderer"] == "motion_canvas":
                source=mc[0 if index==0 else 1]
                _hybrid_need(source["segment_id"] == part["segment_id"] and source["scene_ids"] == part["scene_ids"],
                             "hybrid_original_mc_identity")
                row=source["artifact"]
                observed=document("output",row["manifest"],8*1024*1024,row["manifest_sha256"])
                _hybrid_need(len(observed["frames"])==60 and observed["plan"]["width"]==1920
                             and observed["plan"]["height"]==1080 and observed["plan"]["first_frame"]==0,
                             "hybrid_original_mc_manifest")
                for local,frame in enumerate(observed["frames"]):
                    _hybrid_need(frame["index"]==local and frame["file"]==f"frames/{local:06}.png",
                                 "hybrid_original_mc_frame_order")
                    sources.append({"path":row["directory"]+"/"+frame["file"],"sha256":frame["sha256"],
                                    "bytes":frame["bytes"],"rgba_sha256":None})
            else:
                original=rendered["manim"][0]["original_native_result"]
                row=original["result"]["result"]["data"]["artifact"]
                media={"root":"output","path":row["relative_path"],"sha256":row["sha256"],"bytes":row["bytes"]}
                artifact(media)
                receipt=row["decoded_video"]
                _hybrid_need(receipt["relative_path"]==media["path"]+".decoded-video.json", "hybrid_manim_original_report_path")
                observed=document("output",receipt["relative_path"],16*1024*1024,receipt["sha256"],receipt["bytes"])
                _hybrid_need(observed["frame_count"]==60 and len(observed["frames"])==60
                             and original["plan_sha256"]==part["plan_sha256"],"hybrid_manim_original_frame_roster")
                for local,frame in enumerate(observed["frames"]):
                    _hybrid_need(frame["file"]==f"{local:06}.png","hybrid_manim_original_png_order")
                    sources.append({"path":receipt["relative_path"]+".frames/"+frame["file"],
                                    "sha256":frame["png_sha256"],"bytes":frame["png_bytes"],"rgba_sha256":frame["rgba_sha256"]})
        _hybrid_need(len(sources)==180,"hybrid_source_frames_incomplete")
        visual_media=artifact({"root":"output","path":visual["artifact_path"],"sha256":visual["artifact_sha256"],"bytes":visual["artifact_bytes"]})
        master_media=artifact(master["master"]["artifact"])
        _hybrid_need(master_media == expected_result["master_artifact"]
                     and visual_media["sha256"] == expected_result["visual_artifact_sha256"],
                     "hybrid_final_original_artifacts_differ")
        timeline,visual_document=observed_frames(visual["complete_video_observation"],visual_media,180)
        final,master_document=observed_frames(master["complete_video_observation"],master_media,180)
        started=time.monotonic(); cache=OrderedDict(); decoded_keys=set(); decode_count=reuses=0
        evidence_rows=[]
        def decode(row,label):
            nonlocal decode_count,reuses
            _hybrid_need(time.monotonic()-started <= HYBRID_PIXEL_CONTRACT["decode_start_deadline_seconds"],"hybrid_decode_start_deadline")
            raw=read("output",row["path"],32*1024*1024,row["sha256"],row["bytes"])
            key=(row["sha256"],row["bytes"])
            if key in cache:
                previous,rgba,original_label=cache.pop(key)
                _hybrid_need(previous==raw,"hybrid_same_digest_different_original_bytes")
                cache[key]=(previous,rgba,original_label); reuses+=1
            else:
                _hybrid_need(decode_count < HYBRID_PIXEL_CONTRACT["maximum_png_decoder_invocations"],"hybrid_decode_count_budget")
                validate_png_crc_bytes(raw,1920,1080,max_bytes=32*1024*1024)
                capture={}; primary_decode=None
                try:
                    rgba=decoded_rgba_png_capture(raw,1920,1080,ffmpeg=Path(ffmpeg),
                        executable_sha256=executable_sha256,capture=capture)
                except BaseException as error:
                    primary_decode=error
                    raise
                finally:
                    try:retain_decode(label,capture)
                    except BaseException:
                        if primary_decode is None:raise
                decode_count+=1; decoded_keys.add(key); original_label=label
                cache[key]=(raw,rgba,original_label)
                while len(cache)>8:cache.popitem(last=False)
            actual=sha(rgba)
            _hybrid_need(row["rgba_sha256"] is None or actual==row["rgba_sha256"],"hybrid_independent_RGBA_disagrees_with_original_report")
            return rgba,{"png_path":row["path"],"png_sha256":row["sha256"],"rgba_sha256":actual,
                         "original_decoder_capture":original_label,"exact_original_PNG_reused":original_label!=label}
        for frame in range(180):
            source,source_record=decode(sources[frame],f"hybrid-source-{frame:03}")
            rendered_frame,timeline_record=decode(timeline[frame],f"hybrid-timeline-{frame:03}")
            master_frame,master_record=decode(final[frame],f"hybrid-master-{frame:03}")
            for pixels in (source,rendered_frame,master_frame):hybrid_frame_presence(pixels,frame)
            evidence_rows.append({"frame":frame,"source":source_record,"timeline":timeline_record,"master":master_record,
                "source_to_ffv1":hybrid_rgb_difference(source,rendered_frame,width=1920,height=1080),
                "source_to_h264":hybrid_rgb_difference(source,master_frame,width=1920,height=1080)})
        _hybrid_need(handles.stable_roots(),"hybrid_original_reader_roots_changed")
        return {"schema":"motionwright.native.hybrid-pixel-observation/1","project_sha256":plan["project_sha256"],
            "policy":HYBRID_PIXEL_CONTRACT,"frame_count":180,"source_segment_frames":[60,60,60],
            "original_PNG_frames_bound":540,"original_PNG_decoder_invocations":decode_count,
            "distinct_original_PNGs_decoded":len(decoded_keys),
            "equal_original_PNG_frames_reused":reuses,"source_native_encodings":["MC_PNG","Manim_H264","MC_PNG"],
            "master_sha256":master_media["sha256"],"visual_sha256":visual_media["sha256"],
            "visual_exact_clock_observed":visual_document["exact_clock"],
            "master_exact_clock_observed":master_document["exact_clock"],
            "original_documents_and_files":reads,"frames":evidence_rows,
            "fixed_cut_and_content_screen":"PASS","differences_retained":True,
            "all180_independent_decoder_processes_claimed":False,
            "codec_loss_absence_claimed":False,"human_approval":False,"physical_acceptance":False,
            "Host_receiver_closure_proved":False,"automatic_native_retry":False}
    except BaseException as error:
        primary=error
        raise
    finally:
        try:handles.close()
        except BaseException as secondary:
            try:
                retain_decode("hybrid-original-reader-close",{
                    "state":"READ_ONLY_READER_CLOSE_FAILED",
                    "error_type":type(secondary).__name__,"error":str(secondary)[:1024],
                    "preceding_readback_error_preserved":primary is not None})
            except BaseException:pass
            if primary is None:raise
