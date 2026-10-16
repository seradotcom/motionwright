#!/usr/bin/env python3
"""Independent source-and-decoded-AAC proof for 18s three-scene native SFX.

Technical-only: the application renderer remains pinned Semwright Native SDK,
and this acceptance decoder is never used for production export or mixing.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import struct
import wave
from typing import Any

from semantic_sfx_av_evidence import (
    require, sha, pcm, SOURCE_FRAMES, SOURCE_A, SOURCE_B, MIX, tone, energy,
)
from native_av_media_probe import (
    MediaProbeError, _run_decoder, inspect_streams, inspect_decoded_video,
    MAX_DECODED_PCM_BYTES, MAX_MP4_BYTES, FRAME_WIDTH, FRAME_HEIGHT,
)

FRAMES = 540
PCM_SAMPLES = FRAMES * 1600
FIRST_FRAME = 6
LAST_FRAME = 380
EXPECTED_SCHEDULE = [
    (6, 30, 9600, 38400),
    (380, 404, 608000, 38400),
]


def _master_pcm(path: Path) -> list[tuple[int, int]]:
    require(path.is_file() and not path.is_symlink(),
            "Native multiscene SFX owner master WAV must be a regular file")
    require(44 < path.stat().st_size <= 4 * 1024 * 1024,
            "Native multiscene original WAV exceeds bounded data")
    with wave.open(str(path), "rb") as audio:
        require(
            audio.getnchannels() == 2 and audio.getsampwidth() == 2
            and audio.getframerate() == 48000
            and audio.getnframes() == PCM_SAMPLES
            and audio.getcomptype() == "NONE",
            "Multiscene native SFX mix has wrong stereo 48 kHz PCM16 clock",
        )
        body = audio.readframes(PCM_SAMPLES)
    require(len(body) == PCM_SAMPLES * 4, "Multiscene WAV read was incomplete")
    return list(struct.iter_unpack("<hh", body))


def original_multiscene_pcm(
    sources: tuple[list[tuple[int, int]], list[tuple[int, int]]],
) -> list[tuple[int, int]]:
    require(all(len(source) == SOURCE_FRAMES for source in sources),
            "Original source WAV duration is not exactly 24 30-fps frames")
    out = [[0.0, 0.0] for _ in range(PCM_SAMPLES)]
    gain = 10.0 ** (-12000 / 20000)
    for source, first in zip(sources, (FIRST_FRAME, LAST_FRAME)):
        base = first * 1600
        for sample_index, (left, right) in enumerate(source):
            envelope = min(
                1.0, sample_index / (3 * 1600),
                (SOURCE_FRAMES - sample_index - 1) / (6 * 1600),
            )
            out[base + sample_index][0] += left * gain * envelope
            out[base + sample_index][1] += right * gain * envelope
    require(max(max(abs(x), abs(y)) for x,y in out) < 32767,
            "Original numeric SFX reference clips stereo PCM16")
    return [(round(left),round(right)) for left,right in out]


def inspect_original_composition(
    folder: Path, receipt: dict[str, Any],
) -> dict[str, Any]:
    require(receipt.get("schema") == "motionwright.semantic-sfx-av-prepared/1",
            "Multiscene SFX source receipt schema differs")
    require(receipt.get("frames") == FRAMES and
            receipt.get("sample_count") == PCM_SAMPLES and
            receipt.get("fps") == "30/1" and receipt.get("sample_rate") == 48000
            and receipt.get("channels") == 2, "Multiscene owner PCM clock mismatch")
    scene_ids=receipt.get("scene_ids")
    require(isinstance(scene_ids,list) and len(scene_ids)==3
            and len(set(scene_ids))==3,
            "Expected exactly three separate owner scene identities")
    require(receipt.get("native_preparation_verified") is True
            and receipt.get("native_preparation_revision") == receipt.get("revision")
            and receipt.get("original_source_count")==2,
            "Actual first-party prepared native SFX source is not authenticated")
    cues=receipt.get("scheduled_sfx")
    require(isinstance(cues,list) and len(cues)==2
            and [(x.get("absolute_start_frame"),x.get("absolute_end_frame"),
                  x.get("first_sample"),x.get("sample_count")) for x in cues]
            == EXPECTED_SCHEDULE,
            "Multiscene SFX source offsets are not exact relative to scene0 and scene2")
    require(len(receipt.get("source_cue_ids",[]))==2,
            "Both stable native source event identities are required")
    relative=receipt.get("audio_relative")
    require(isinstance(relative,str) and 0<len(relative)<160
            and relative.startswith("mw-sfx-") and relative.endswith(".wav")
            and "/" not in relative and "\\" not in relative and ".." not in relative,
            "Original PCM must use its digest-addressed owner WAV filename")
    prepared=folder/relative
    portable=folder/MIX
    original_a,original_b=folder/SOURCE_A,folder/SOURCE_B
    for original,key in (
        (original_a,"first_source_sha256"),
        (original_b,"second_source_sha256"),
        (prepared,"audio_sha256"),
        (portable,"audio_sha256"),
    ):
        reported=receipt.get(key)
        require(isinstance(reported,str) and len(reported)==64 and sha(original)==reported,
                "Source/PCM SHA-256 differs from original immutable owner receipt")
    require(prepared.read_bytes()==portable.read_bytes(),
            "Native multiscene mux WAV differs from independently reviewed original PCM")
    left=pcm(original_a,frames=SOURCE_FRAMES)
    right=pcm(original_b,frames=SOURCE_FRAMES)
    source=_master_pcm(portable)
    expected=original_multiscene_pcm((left,right))
    error=max(max(abs(x-u),abs(y-v)) for (x,y),(u,v) in zip(source,expected))
    require(error<=1, "Native multiscene PCM diverges from both original imported source WAVs")
    require(all(x==0 and y==0 for x,y in source[:FIRST_FRAME*1600]),
            "Multiscene SFX started before its first authored event")
    require(all(x==0 and y==0 for x,y in source[180*1600:360*1600]),
            "Source SFX leaked into the intentionally silent middle Film scene")
    require(all(x==0 and y==0 for x,y in source[404*1600:]),
            "Source SFX persists beyond the third scene's authored event tail")
    peak=max(max(abs(x),abs(y)) for x,y in source)
    require(peak>0 and peak==receipt.get("sample_peak_pcm16"),
            "Actual native SFX sample peak is unbound or silent")
    return {
        "source_sha256":[sha(original_a),sha(original_b)],
        "mixed_sha256":sha(portable),
        "sample_peak_pcm16":peak,
        "max_sample_difference_pcm16":error,
        "exact_sfx_sample_count":PCM_SAMPLES,
        "real_imported_original_stems":2,
        "original_scene_count":3,
        "middle_scene_silence_sample_count":180*1600,
    }


def _onset(left:list[int],right:list[int],anchor:int) -> int:
    start=max(0,anchor-4800)
    limit=min(len(left)-240,anchor+6000)
    for index in range(start,limit,240):
        begin=index/48000
        if max(energy(left,begin,begin+.005),
               energy(right,begin,begin+.005))>80:
            return index
    raise MediaProbeError("Decoded AAC original SFX onset was not audible")


def inspect_decoded_multiscene_audio(decoded:bytes) -> dict[str,Any]:
    require(len(decoded)%4==0, "Decoded multiscene AAC has partial PCM stereo samples")
    n=len(decoded)//4
    require(PCM_SAMPLES-2400<=n<=PCM_SAMPLES+4800,
            "Decoded AAC duration differs from the three-scene 18-second Film")
    samples=list(struct.iter_unpack("<hh",decoded))
    left=[l for l,_ in samples]
    right=[r for _,r in samples]
    first={
        "l440":tone(left,440,.45,.60),"l740":tone(left,740,.45,.60),
        "r660":tone(right,660,.45,.60),"r980":tone(right,980,.45,.60),
    }
    third={
        "l440":tone(left,440,12.95,13.10),"l740":tone(left,740,12.95,13.10),
        "r660":tone(right,660,12.95,13.10),"r980":tone(right,980,12.95,13.10),
    }
    require(first["l440"]>200 and first["r660"]>200
            and first["l440"]>5*max(first["l740"],1)
            and first["r660"]>5*max(first["r980"],1),
            "Decoded AAC first original scene SFX is absent/wrong channel")
    require(third["l740"]>180 and third["r980"]>180
            and third["l740"]>5*max(third["l440"],1)
            and third["r980"]>5*max(third["r660"],1),
            "Decoded AAC third scene SFX is absent/late/wrong channel")
    strong=max(energy(left,.45,.60),energy(right,.45,.60))
    silence={
        "pre":max(energy(left,.04,.16),energy(right,.04,.16)),
        "middle_scene":max(energy(left,8.4,8.8),energy(right,8.4,8.8)),
        "after":max(energy(left,15.0,15.5),energy(right,15.0,15.5)),
    }
    require(strong>280 and all(v<.035*strong for v in silence.values()),
            "AAC retained a sound tail or source cross-scene leakage")
    onsets=[_onset(left,right,frame*1600) for frame in (FIRST_FRAME,LAST_FRAME)]
    for observed,expected in zip(onsets,(FIRST_FRAME*1600,LAST_FRAME*1600)):
        require(abs(observed-expected)<=1024,
                "Native AAC moved a source event beyond one 1024-sample AAC packet")
    return {
        "decoded_pcm_frames":n,
        "stereo_channel_identity":"confirmed_scene0_440_660_scene2_740_980_hz",
        "observed_onset_samples":onsets,
        "expected_onset_samples":[FIRST_FRAME*1600,LAST_FRAME*1600],
        "max_absolute_onset_error_samples":max(abs(a-b) for a,b in zip(
            onsets,(FIRST_FRAME*1600,LAST_FRAME*1600))),
        "semantic_sfx_windows_pcm16":{
            "scene0":{k:round(v,2) for k,v in first.items()},
            "scene2":{k:round(v,2) for k,v in third.items()},
            "silence_pcm16_rms":{k:round(v,2) for k,v in silence.items()},
        },
    }


def inspect_native_multiscene_sfx_av(
    master:Path, owner:Path, receipt:dict[str,Any],
    ffmpeg:Path, ffprobe:Path,
) -> dict[str,Any]:
    source=inspect_original_composition(owner,receipt)
    require(master.is_file() and not master.is_symlink()
            and 1024<master.stat().st_size<=MAX_MP4_BYTES,
            "Actual native 18-second owner MP4 is missing or excessive")
    raw=_run_decoder([
        str(ffprobe),"-v","error","-count_frames","-show_entries",
        "stream=codec_type,codec_name,width,height,avg_frame_rate,nb_read_frames,sample_rate,channels:format=duration",
        "-of","json",str(master),
    ],max_bytes=128*1024)
    streams=inspect_streams(json.loads(raw),expected_frames=FRAMES,expected_size=(1920,1080))
    audio=_run_decoder([
        str(ffmpeg),"-nostdin","-hide_banner","-loglevel","error","-i",str(master),
        "-map","0:a:0","-ac","2","-ar","48000","-c:a","pcm_s16le","-f","s16le","pipe:1",
    ],max_bytes=MAX_DECODED_PCM_BYTES)
    decoded=inspect_decoded_multiscene_audio(audio)
    image=_run_decoder([
        str(ffmpeg),"-nostdin","-hide_banner","-loglevel","error","-i",str(master),
        "-map","0:v:0",
        "-vf",r"select=eq(n\,140)+eq(n\,480),scale=160:90:flags=bicubic,format=rgb24",
        "-vsync","0","-f","rawvideo","pipe:1",
    ],max_bytes=2*FRAME_WIDTH*FRAME_HEIGHT*3)
    video=inspect_decoded_video(image)
    return {
        "evidence_scope":"real_three_scene_540_frame_owner_source_sfx_to_native_semwright_h264_aac",
        "source_wav_sha256":source["mixed_sha256"],
        "master_sha256":sha(master),
        "technical_source_pcm":"PASS",
        "technical_h264_aac_mux":"PASS",
        "human_creative_approval":"REQUIRED",
        "rights_certification":"NOT_PERFORMED",
        "project_id":receipt["project_id"],
        "generation":receipt["generation"],
        "revision":receipt["revision"],
        **source,**streams,**decoded,**video,
    }
