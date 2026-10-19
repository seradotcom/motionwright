#!/usr/bin/env python3
"""Generate a source-locked MLT 7.22.0 planar-tail repair for isolated review.

This modifies a COPY of the original LGPL MLT consumer, not the Semwright
runtime, installed provider, job lifecycle or project source. It neither
builds a provider nor authorizes or retries any native effect.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ORIGINAL_SHA256 = "c1c8850ff049a8775bb3d24c64be9b9365659773ce019902585c056f5be2f910"
ORIGINAL_BYTES = 96690
MAX_READ = 256 * 1024
SENTINEL_START = "static uint8_t *interleaved_to_planar("
SENTINEL_END = "/** Add an audio output stream"
SAMPLE_TYPES = ("float", "int16_t", "int32_t", "uint8_t")
OLD_PREFIX = "p = interleaved_to_planar(samples, ctx->channels, p, sizeof("
OPTIMIZED_START = "// Optimized for single track and no channel remap"
PREPARED_ASSIGNMENT = "ctx->audio_avframe->nb_samples = FFMAX(samples, ctx->audio_input_frame_size);"

# FFmpeg owns the actual planar stride. align=0 is the same argument passed
# to avcodec_fill_audio_frame in this *original* consumer; never guess stride
# from the partial number of FIFO samples.
REPAIRED_HELPER = """static uint8_t *interleaved_to_planar(int samples,
                                      int prepared_samples,
                                      int channels,
                                      uint8_t *audio,
                                      int bytes_per_sample,
                                      enum AVSampleFormat sample_fmt)
{
    int linesize = 0;
    int allocated_bytes = av_samples_get_buffer_size(&linesize,
                                                     channels,
                                                     prepared_samples,
                                                     sample_fmt,
                                                     0);
    if (samples < 0 || samples > prepared_samples || channels < 1
        || channels > MAX_AUDIO_STREAMS * 2 || bytes_per_sample < 1
        || bytes_per_sample != av_get_bytes_per_sample(sample_fmt)
        || !av_sample_fmt_is_planar(sample_fmt)
        || allocated_bytes <= 0 || allocated_bytes > AUDIO_ENCODE_BUFFER_SIZE
        || linesize <= 0
        || (int64_t) linesize * channels > AUDIO_ENCODE_BUFFER_SIZE)
        return NULL;

    uint8_t *buffer = mlt_pool_alloc(AUDIO_ENCODE_BUFFER_SIZE);
    if (!buffer)
        return NULL;
    memset(buffer, 0, AUDIO_ENCODE_BUFFER_SIZE);
    for (int c = 0; c < channels; c++) {
        uint8_t *p = buffer + (size_t) c * linesize;
        uint8_t *q = audio + (size_t) c * bytes_per_sample;
        for (int i = 0; i < samples; i++) {
            memcpy(p, q, bytes_per_sample);
            p += bytes_per_sample;
            q += channels * bytes_per_sample;
        }
    }
    return buffer;
}

"""


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def checked_original(path: Path) -> bytes:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("original source must be an absolute regular file")
    before = path.stat()
    if before.st_size != ORIGINAL_BYTES or before.st_size > MAX_READ:
        raise ValueError("original MLT source size differs from exact v7.22.0")
    raw = path.read_bytes()
    after = path.stat()
    if (len(raw) != before.st_size or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns or sha(raw) != ORIGINAL_SHA256):
        raise ValueError("MLT source content or generation differs from frozen original")
    return raw


def repair_exact_original(raw: bytes) -> tuple[bytes, bytes]:
    if len(raw) != ORIGINAL_BYTES or sha(raw) != ORIGINAL_SHA256:
        raise ValueError("repair refuses nonoriginal or already-modified MLT source")
    text = raw.decode("utf-8")
    start = text.find(SENTINEL_START)
    end = text.find(SENTINEL_END, start)
    if start < 0 or end < start or text.find(SENTINEL_START, start + 1) >= 0:
        raise ValueError("original planar conversion boundary differs")
    original_helper = text[start:end]
    if not all(marker in original_helper for marker in (
        "uint8_t *p = buffer;",
        "memset(buffer, 0, AUDIO_ENCODE_BUFFER_SIZE);",
        "for (c = 0; c < channels; c++)",
        "int i = samples + 1;",
        "p += bytes_per_sample;",
    )):
        raise ValueError("original packed planar helper differs")
    text = text[:start] + REPAIRED_HELPER + text[end:]
    optimize = text.find(OPTIMIZED_START)
    stop = text.find("} else {", optimize)
    if optimize < 0 or stop < optimize:
        raise ValueError("original single-track block not found")
    scoped = text[optimize:stop]
    if scoped.count(PREPARED_ASSIGNMENT) != 1:
        raise ValueError("original fixed frame assignment differs")
    for dtype in SAMPLE_TYPES:
        old = f"{OLD_PREFIX}{dtype}));"
        if scoped.count(old) != 1:
            raise ValueError(f"expected one original planar conversion for {dtype}")
        new = (
            "p = interleaved_to_planar(samples,\n"
            "                                          FFMAX(samples, ctx->audio_input_frame_size),\n"
            f"                                          ctx->channels, p, sizeof({dtype}),\n"
            "                                          codec->sample_fmt);"
        )
        scoped = scoped.replace(old, new)
    scoped = scoped.replace(
        PREPARED_ASSIGNMENT,
        "if (!p) {\n"
        '                mlt_log_error(ctx->properties, "planar encoder frame layout invalid\\n");\n'
        "                return -1;\n"
        "            }\n"
        "            " + PREPARED_ASSIGNMENT,
    )
    text = text[:optimize] + scoped + text[stop:]
    if text.count("p = interleaved_to_planar(samples,") != 4:
        raise ValueError("planar conversion calls were not conserved")
    if text.count(SENTINEL_START) != 1:
        raise ValueError("planar converter identity was not conserved")
    repaired = text.encode("utf-8")
    if repaired == raw:
        raise AssertionError("MLT source repair left the original bytes unchanged")
    return repaired, REPAIRED_HELPER.encode("utf-8")


def write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as dest:
        dest.write(data)
        dest.flush()
        import os
        os.fsync(dest.fileno())


def generate(original: Path, directory: Path) -> dict:
    if (not directory.is_absolute() or directory.is_symlink()
            or not directory.is_dir()):
        raise ValueError("repair destination must be a fresh existing private directory")
    outputs = [
        directory / "consumer_avformat.c",
        directory / "planar-helper-under-test.h",
        directory / "repair-receipt.json",
    ]
    if any(path.exists() or path.is_symlink() for path in outputs):
        raise FileExistsError("repair destination contains an earlier source output")
    source = checked_original(original)
    repaired, helper = repair_exact_original(source)
    receipt = {
        "schema": "motionwright.mlt.planar-source-repair/1",
        "original_upstream_tag": "v7.22.0",
        "original_sha256": sha(source),
        "repaired_source_sha256": sha(repaired),
        "exact_test_helper_sha256": sha(helper),
        "original_bytes": len(source),
        "repaired_bytes": len(repaired),
        "source_altered_in_place": False,
        "product_pin_modified": False,
        "provider_compiled": False,
        "provider_dispatched": False,
        "master_audio_tail_corrected": False,
        "native_master_accepted": False,
    }
    write_new(outputs[0], repaired)
    write_new(outputs[1], helper)
    write_new(outputs[2], (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())
    if checked_original(original) != source:
        raise ValueError("MLT original source changed while preparing repair")
    if (sha(outputs[0].read_bytes()) != receipt["repaired_source_sha256"]
            or sha(outputs[1].read_bytes()) != receipt["exact_test_helper_sha256"]):
        raise ValueError("MLT repaired source failed readback")
    return receipt


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--consumer", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(generate(args.consumer, args.output_dir), sort_keys=True))


if __name__ == "__main__":
    main()
