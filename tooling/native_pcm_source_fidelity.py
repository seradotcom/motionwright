"""Bounded, source-bound full stereo PCM content witness for native master CI.

This is a READ-ONLY source-content gate, not a renderer, source importer, codec
authority, native consent, loudness/true-peak guarantee, channel matrix policy,
or end-to-end media acceptance. The caller still must independently verify
source rights, Host grants, provider receipts, packet PTS and AAC delay/skip.

Unlike the older 400/600Hz fixture-only gate, this inspects the *actual* caller
supplied 48kHz stereo PCM16 WAVE source over its entire declared duration.
It rejects isolated lost channels/tails even if midstream frequencies match.
No shell, no network, no Semwright runtime fork, no arbitrary temporal shift.
"""
from __future__ import annotations

import hashlib
import io
import math
import re
import sys
import wave

SCHEMA = "motionwright.native.source-pcm16-stereo-content/1"
MAX_SOURCE_FRAMES = 48_000 * 60   # 60s bounded independent CI witness
MAX_PADDING_FRAMES = 1024        # only physical sample *budget*, NOT PTS approval
WINDOW_FRAMES = 256              # ~5.33 ms at 48kHz, includes terminal remainder
MIN_MEAN_ABSOLUTE_SOURCE = 64.0  # faint/zero windows need a separate noise-floor bound
MAX_QUIET_OUTPUT_ABSOLUTE_MEAN = 256.0  # conservative codec ringing allowance, not silence identity
MIN_RELATIVE_ENERGY = 0.20
MAX_RELATIVE_ENERGY = 5.0
MIN_CHANNEL_COVERAGE = 0.10      # an almost-fully-muted channel cannot be "green"
MIN_CORRELATION = 0.50           # supplementary identity/phase, not perceptual MOS
CORRELATION_SAMPLE_COUNT = 2048
HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

class PcmContentError(ValueError):
    """Fail-closed measured source PCM fidelity mismatch."""

def insist(test: bool, code: str) -> None:
    if not test:
        raise PcmContentError(code)

def digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()

def _pcm16_view(blob: bytes, name: str):
    insist(sys.byteorder == "little", "pcm16_host_not_little_endian")
    insist(len(blob) % 4 == 0, f"{name}_not_whole_stereo_frames")
    return memoryview(blob).cast("h")

def _source_wave(packed_wave: bytes):
    insist(isinstance(packed_wave, bytes), "source_wave_must_be_bounded_bytes")
    insist(44 <= len(packed_wave) <= MAX_SOURCE_FRAMES * 4 + 65_536,
           "source_wav_byte_budget")
    try:
        with wave.open(io.BytesIO(packed_wave), "rb") as input_wave:
            measured = (
                input_wave.getnchannels(),input_wave.getsampwidth(),
                input_wave.getframerate(),input_wave.getcomptype(),
            )
            insist(measured == (2,2,48000,"NONE"), "unsupported_source_audio_profile")
            count = input_wave.getnframes()
            insist(256 <= count <= MAX_SOURCE_FRAMES, "source_audio_sample_budget")
            pcm = input_wave.readframes(count)
            insist(len(pcm) == count * 4 and
                   not input_wave.readframes(1), "source_wav_frames_incomplete")
    except (wave.Error, EOFError, OSError) as exc:
        raise PcmContentError("source_wav_media_malformed") from exc
    return count,pcm

def _correlation(x, y, start: int, stop: int, col_a: int, col_b: int) -> float:
    count=stop-start
    if count < 2:
        return 0.0
    ax = [int(x[2*i+col_a]) for i in range(start, stop)]
    by = [int(y[2*i+col_b]) for i in range(start, stop)]
    mean_a = sum(ax)/count
    mean_b = sum(by)/count
    sum_cross = sum((a-mean_a)*(b-mean_b) for a,b in zip(ax,by))
    var_a = sum((a-mean_a)**2 for a in ax)
    var_b = sum((b-mean_b)**2 for b in by)
    return sum_cross/math.sqrt(var_a*var_b) if var_a>0 and var_b>0 else 0.0

def inspect_source_pcm16_stereo_content(
    original_wav: bytes, decoded_pcm: bytes,
    *, original_wav_sha256: str, decoded_pcm_sha256: str,
) -> dict:
    """Measure loss against original source at fixed zero-offset positions.

    SHA inputs identify the caller's *owned* bytes, not an external grant. An
    unknown AAC priming/encoder delay is NOT guessed or automatically removed.
    48k stereo WAV source and raw interleaved s16le stereo decoder output must
    be independently tied to an original authorized job, separately.
    """
    insist(isinstance(original_wav_sha256,str) and
           HEX_SHA256.fullmatch(original_wav_sha256) is not None,
           "source_sha256_missing")
    insist(isinstance(decoded_pcm_sha256,str) and
           HEX_SHA256.fullmatch(decoded_pcm_sha256) is not None,
           "decoded_sha256_missing")
    insist(isinstance(decoded_pcm,bytes), "decoded_pcm_must_be_bounded_bytes")
    insist(digest(original_wav)==original_wav_sha256, "source_digest_changed")
    insist(digest(decoded_pcm)==decoded_pcm_sha256, "decoded_digest_changed")
    source_frames, source_pcm = _source_wave(original_wav)
    insist(0<len(decoded_pcm)<= (MAX_SOURCE_FRAMES+MAX_PADDING_FRAMES)*4,
           "decoded_pcm_byte_budget")
    x = _pcm16_view(source_pcm, "source")
    y = _pcm16_view(decoded_pcm, "decoded")
    decoded_frames = len(y)//2
    insist(source_frames <= decoded_frames <= source_frames+MAX_PADDING_FRAMES,
           "decoded_pcm_duration_budget")

    total_informative=[0,0]
    window_identity_checked=[0,0]
    min_window_correlation=[math.inf,math.inf]
    max_window_correlation=[-math.inf,-math.inf]
    total_source_l1=[0,0]
    total_decoded_l1=[0,0]
    observed_min=[math.inf,math.inf]
    observed_max=[0.0,0.0]
    last_window=[None,None]
    terminal_informative=[False,False]
    inspected=0
    skipped_ambiguous=0
    for first in range(0,source_frames,WINDOW_FRAMES):
        final=min(first+WINDOW_FRAMES,source_frames)
        sample_count=final-first
        inspected+=1
        for channel in range(2):
            original_sum=sum(abs(int(x[2*idx+channel])) for idx in range(first,final))
            decoded_sum=sum(abs(int(y[2*idx+channel])) for idx in range(first,final))
            total_source_l1[channel]+=original_sum
            total_decoded_l1[channel]+=decoded_sum
            original_mean=original_sum/sample_count
            if original_mean < MIN_MEAN_ABSOLUTE_SOURCE:
                # Source silence is part of the composition. The output cannot
                # invent a loud SFX in an authored silent/very quiet interval
                # merely because this window lacks enough information to
                # compute a meaningful waveform correlation.
                observed_mean=decoded_sum/sample_count
                insist(
                    observed_mean <= MAX_QUIET_OUTPUT_ABSOLUTE_MEAN
                        + MAX_RELATIVE_ENERGY*original_mean,
                    f"source_audio_unapproved_sound_in_quiet_window:"
                    f"channel={channel}:start={first}:end={final}:"
                    f"mean={observed_mean:.6f}",
                )
                skipped_ambiguous+=1
                continue
            total_informative[channel]+=1
            ratio=decoded_sum/original_sum
            insist(MIN_RELATIVE_ENERGY<=ratio<=MAX_RELATIVE_ENERGY,
                   f"source_audio_content_missing_or_scaled:channel={channel}:"
                   f"start={first}:end={final}:ratio={ratio:.6f}")
            # The energy ratio alone is insufficient. A reversed or reordered
            # 256-frame segment has exactly the same channel L1 energy but is
            # not the authored waveform. The legacy five-point identity test
            # missed such corruption between its sparse anchors.
            # One terminal frame cannot define a correlation by itself. Keep
            # its own energy check above, and include it with the preceding
            # complete window for waveform identity; no source frame is skipped.
            correlation_first=max(0,first-WINDOW_FRAMES) if sample_count==1 else first
            corr=_correlation(x,y,correlation_first,final,channel,channel)
            insist(corr>=MIN_CORRELATION,
                   f"source_audio_content_temporally_changed:channel={channel}:"
                   f"start={first}:end={final}:correlation={corr:.6f}")
            window_identity_checked[channel]+=1
            min_window_correlation[channel]=min(min_window_correlation[channel],corr)
            max_window_correlation[channel]=max(max_window_correlation[channel],corr)
            observed_min[channel]=min(observed_min[channel],ratio)
            observed_max[channel]=max(observed_max[channel],ratio)
            if final==source_frames:
                terminal_informative[channel]=True
            last_window[channel] = {"from":first,"to":final,
                 "ratio":round(ratio,6),"informative":True}

    for channel in range(2):
        insist(total_informative[channel] >=
               max(1,math.ceil(inspected*MIN_CHANNEL_COVERAGE)),
               f"source_audio_channel_not_informative:{channel}")
        # Important: aggregate still independently rules out globally muted
        # output, but every informative short source window was checked first.
        overall_ratio=total_decoded_l1[channel]/max(1,total_source_l1[channel])
        insist(MIN_RELATIVE_ENERGY<=overall_ratio<=MAX_RELATIVE_ENERGY,
               f"source_audio_channel_overall_energy:{channel}")

    # Supplementary waveform identity and phase: 3+ aligned windows must
    # retain original per-channel correlation. A shuffled/silent tail alone
    # can still fail earlier on its actual source-active short windows.
    anchors=sorted(set(max(0,min(source_frames-CORRELATION_SAMPLE_COUNT,k))
        for k in [0,source_frames//4,source_frames//2,
                  (source_frames*3)//4,source_frames-CORRELATION_SAMPLE_COUNT]))
    correlation=[]
    for first in anchors:
        stop=min(first+CORRELATION_SAMPLE_COUNT,source_frames)
        same=[_correlation(x,y,first,stop,chan,chan) for chan in (0,1)]
        exchanged=[_correlation(x,y,first,stop,chan,1-chan) for chan in (0,1)]
        correlation.append({"from":first,"to":stop,
            "same_channels":[round(v,6) for v in same],
            "swapped_channels":[round(v,6) for v in exchanged]})
    for channel in (0,1):
        informative_correlation=[row["same_channels"][channel]
                                for row in correlation]
        insist(sum(v>=MIN_CORRELATION for v in informative_correlation)>=3,
               f"source_audio_channel_identity_not_correlated:{channel}")

    return {
        "schema":SCHEMA,
        "classification":"SOURCE_CONTENT_SCREEN_ONLY_NOT_AV_MASTER_ACCEPTANCE",
        "original_wav_sha256":original_wav_sha256,
        "decoded_pcm_sha256":decoded_pcm_sha256,
        "source_stereo_frames":source_frames,
        "decoded_stereo_frames":decoded_frames,
        "raw_decoded_frames_after_source":decoded_frames-source_frames,
        "source_start_offset_frames_assumed":0,
        "source_to_decoded_PTS_and_AAC_priming_verified":False,
        "source_declared_channel_map_verified":False,
        "full_source_window_count":inspected,
        "uninformative_source_channel_windows":skipped_ambiguous,
        "maximum_admitted_quiet_output_absolute_mean":MAX_QUIET_OUTPUT_ABSOLUTE_MEAN,
        "quiet_source_windows_checked_against_codec_floor":True,
        "informative_source_windows_by_channel":total_informative,
        "source_window_temporal_identity_checked_by_channel":window_identity_checked,
        "minimum_source_window_correlation":[round(v,6) for v in min_window_correlation],
        "maximum_source_window_correlation":[round(v,6) for v in max_window_correlation],
        "minimum_admitted_window_correlation":MIN_CORRELATION,
        "minimum_source_window_energy_ratio":[round(v,6) for v in observed_min],
        "maximum_source_window_energy_ratio":[round(v,6) for v in observed_max],
        "last_informative_window_by_channel":last_window,
        "terminal_source_window_informative_by_channel":terminal_informative,
        "identity_correlations":correlation,
        "not_a_perceptual_quality_measure":True,
        "source_permissions_or_licensing_verified":False,
        "native_Provider_effect_or_audio_job_run":False,
        "human_audition_approved":False,
    }
