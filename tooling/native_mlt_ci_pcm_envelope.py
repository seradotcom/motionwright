"""Additional pure source-to-decoded stereo envelope for the fixed 33-frame CI fixture.

This is NOT the MLT renderer, a decoder, a new authority, or a complete
AAC master acceptance gate. It closes the known tail-blind spot in the existing
400/600 Hz midstream inspector without changing that inspector or any CI workflow.
The canonical native Host and independent release checks must bind WAV, media, PTS, edit lists,
skip/priming and grants before release.
"""
from __future__ import annotations

import io
import struct
import wave

from native_mlt_sequence_e2e import inspect_decoded_ci_stereo

SAMPLE_RATE = 48_000
SOURCE_FRAMES = 52_800
MAX_PADDING_FRAMES = 1024
SOURCE_CHANNELS = 2
SOURCE_PCM_BYTES = SOURCE_FRAMES * SOURCE_CHANNELS * 2
WINDOW = 64
MIN_RELATIVE_ABSOLUTE_ENERGY = 0.2
MAX_RELATIVE_ABSOLUTE_ENERGY = 5.0


def _ci_expected_sample(frame: int) -> tuple[int, int]:
    return (((frame % 120) - 60) * 250, ((frame % 80) - 40) * 260)


def inspect_fixed_ci_stereo_envelope(
    source_wav_bytes: bytes, decoded_stereo_pcm16_bytes: bytes,
) -> dict:
    """Refuse missing source-channel energy at any authored timeline window.

    A lossy audio encoder can alter PCM values, so this envelope does NOT
    demand sample identity or promote a packet clock. However, 64 consecutive
    authored loud samples becoming digital silence in either channel is not
    valid AAC padding before the known source-end timestamp.
    """
    if not isinstance(source_wav_bytes, bytes) or not (
        SOURCE_PCM_BYTES < len(source_wav_bytes) <= SOURCE_PCM_BYTES + 16384
    ):
        raise AssertionError("Original CI source WAV not a bounded byte document")
    with wave.open(io.BytesIO(source_wav_bytes), "rb") as wav:
        metadata = (
            wav.getnchannels(), wav.getsampwidth(), wav.getframerate(),
            wav.getnframes(), wav.getcomptype(),
        )
        if metadata != (SOURCE_CHANNELS, 2, SAMPLE_RATE, SOURCE_FRAMES, "NONE"):
            raise AssertionError("CI source WAV media profile changed")
        source_bytes = wav.readframes(SOURCE_FRAMES)
        if len(source_bytes) != SOURCE_PCM_BYTES or wav.readframes(1):
            raise AssertionError("CI source WAV did not preserve its exact PCM frames")

    if not isinstance(decoded_stereo_pcm16_bytes, bytes) or len(decoded_stereo_pcm16_bytes) % 4:
        raise AssertionError("Decoded AAC PCM is not complete stereo s16")
    frames = len(decoded_stereo_pcm16_bytes) // 4
    if not SOURCE_FRAMES <= frames <= SOURCE_FRAMES + MAX_PADDING_FRAMES:
        raise AssertionError("Decoded AAC PCM frame count exceeds fixed CI clock budget")

    source = list(struct.iter_unpack("<hh", source_bytes))
    output = list(struct.iter_unpack("<hh", decoded_stereo_pcm16_bytes))
    if len(source) != SOURCE_FRAMES or len(output) != frames:
        raise AssertionError("Decoded source PCM length incomplete")

    for frame, pair in enumerate(source):
        if pair != _ci_expected_sample(frame):
            raise AssertionError("Measured CI stereo WAV does not match declared source fixture")

    # Preserve the existing midstream frequency/isolation gate. Passing it
    # is necessary but not sufficient: that oracle ignores the final 4,800.
    legacy_midstream = inspect_decoded_ci_stereo(decoded_stereo_pcm16_bytes)
    minima = [float("inf"), float("inf")]
    maxima = [0.0, 0.0]
    windows = 0
    for start in range(0, SOURCE_FRAMES, WINDOW):
        stop = min(start + WINDOW, SOURCE_FRAMES)
        for channel in range(SOURCE_CHANNELS):
            required = sum(abs(source[i][channel]) for i in range(start, stop))
            observed = sum(abs(output[i][channel]) for i in range(start, stop))
            if required <= 0:
                raise AssertionError("Fixed CI source has an uninformative source window")
            ratio = observed / required
            if not (MIN_RELATIVE_ABSOLUTE_ENERGY <= ratio <= MAX_RELATIVE_ABSOLUTE_ENERGY):
                side = ("left", "right")[channel]
                raise AssertionError(
                    f"ci_source_channel_envelope_lost:{side}:{start}:{stop}"
                )
            minima[channel] = min(minima[channel], ratio)
            maxima[channel] = max(maxima[channel], ratio)
        windows += 1

    return {
        "schema": "motionwright.ci.fixed-stereo-full-source-envelope/1",
        "classification": "PURE_CI_SOURCE_ENVELOPE_ONLY_NOT_NATIVE_AUDIO_ACCEPTANCE",
        "source_frames": SOURCE_FRAMES,
        "decoded_frames": frames,
        "unexplained_aac_extra_frames": frames - SOURCE_FRAMES,
        "source_windows_checked_per_channel": windows,
        "minimum_ratio_by_channel": [round(v, 6) for v in minima],
        "maximum_ratio_by_channel": [round(v, 6) for v in maxima],
        "existing_midstream_stereo_observation": legacy_midstream,
        "media_packet_priming_and_skip_verified": False,
        "native_provider_receipt_verified": False,
        "human_listening_approved": False,
        "external_rights_approved": False,
    }
