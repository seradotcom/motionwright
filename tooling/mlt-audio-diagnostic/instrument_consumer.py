"""Instrument the exact MLT 7.22.0 consumer without changing codec control flow.

This constructs source files only. It never launches MLT or grants authority.
The caller must build and admit the resulting provider through the existing
native runtime boundary before using its opt-in private output.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

CONSUMER_SHA256 = "c1c8850ff049a8775bb3d24c64be9b9365659773ce019902585c056f5be2f910"

def replace_once(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise ValueError("source insertion anchor is not unique")
    return source.replace(before, after, 1)

def instrument(source: bytes) -> bytes:
    if hashlib.sha256(source).hexdigest() != CONSUMER_SHA256:
        raise ValueError("MLT consumer source SHA256 is not admitted")
    text = source.decode("utf-8")
    text = replace_once(text, '#include "common.h"\n',
                        '#include "common.h"\n#include "audio_tap.h"\n')
    text = replace_once(text, "    AVFrame *audio_avframe;\n} encode_ctx_t;",
                        "    AVFrame *audio_avframe;\n"
                        "    mw_audio_tap *mw_tap;\n"
                        "    int mw_tap_requested;\n"
                        "    int mw_tap_setup_ok;\n"
                        "    int mw_tap_finish_rc;\n} encode_ctx_t;")
    before_fetch = """    if (samples > 0) {
        sample_fifo_fetch(ctx->fifo, ctx->audio_buf_1, samples * ctx->sample_bytes * ctx->channels);
"""
    after_fetch = """    if (samples > 0) {
        uint64_t mw_fifo_before = ctx->mw_tap
            ? (uint64_t) sample_fifo_used(ctx->fifo) : MW_TAP_UNKNOWN;
        sample_fifo_fetch(ctx->fifo, ctx->audio_buf_1, samples * ctx->sample_bytes * ctx->channels);
        if (ctx->mw_tap) {
            mlt_audio_format mw_origin = get_mlt_audio_format(ctx->acodec_ctx[0]->sample_fmt);
            mw_tap_packed(ctx->mw_tap, 'F', ctx->audio_buf_1, samples,
                          ctx->channels, ctx->frequency, ctx->sample_bytes,
                          mw_origin, mw_origin == mlt_audio_f32le,
                          -1, ctx->sample_count[0], mw_fifo_before,
                          (uint64_t) sample_fifo_used(ctx->fifo));
        }
"""
    text = replace_once(text, before_fetch, after_fetch)
    before_fill = """            avcodec_fill_audio_frame(ctx->audio_avframe,
                                     codec->channels,
                                     codec->sample_fmt,
                                     (const uint8_t *) p,
                                     AUDIO_ENCODE_BUFFER_SIZE,
                                     0);
            int ret = avcodec_send_frame(codec, samples ? ctx->audio_avframe : NULL);
"""
    after_fill = """            int mw_fill_rc = avcodec_fill_audio_frame(ctx->audio_avframe,
                                     codec->channels,
                                     codec->sample_fmt,
                                     (const uint8_t *) p,
                                     AUDIO_ENCODE_BUFFER_SIZE,
                                     0);
            size_t mw_record = mw_tap_frame(ctx->mw_tap, codec, ctx->audio_avframe,
                                            samples, mw_fill_rc, p, AUDIO_ENCODE_BUFFER_SIZE);
            int ret = avcodec_send_frame(codec, samples ? ctx->audio_avframe : NULL);
            mw_tap_send_result(ctx->mw_tap, mw_record, ret);
"""
    text = replace_once(text, before_fill, after_fill)
    text = replace_once(text, """        } else {
            // Extract the audio channels according to channel mapping
""", """        } else {
            mw_tap_error(ctx->mw_tap); // This diagnostic does not admit remapping.
            // Extract the audio channels according to channel mapping
""")
    setup = """    // Diagnostic setup is optional and never changes the native result.
    // The authenticated launcher must grant this exact private path beforehand.
    {
        int mw_saved_errno = errno;
        const char *mw_path = getenv("MOTIONWRIGHT_MLT_AUDIO_TAP_PATH");
        enc_ctx->mw_tap_requested = mw_path != NULL;
        if (mw_path) {
            AVCodecContext *mw_codec = enc_ctx->acodec_ctx[0];
            if (enc_ctx->audio_codec_id == AV_CODEC_ID_AAC && mw_codec
                && enc_ctx->audio_avframe && !enc_ctx->audio_st[1]
                && mw_codec->sample_rate == 48000 && mw_codec->ch_layout.nb_channels == 2
                && (mw_codec->sample_fmt == AV_SAMPLE_FMT_FLTP
                    || mw_codec->sample_fmt == AV_SAMPLE_FMT_FLT)) {
                enc_ctx->mw_tap = mw_tap_open(mw_path);
                enc_ctx->mw_tap_setup_ok = enc_ctx->mw_tap != NULL;
            } else {
                fputs("MLT_AUDIO_TAP_INVALID unsupported_consumer_profile\\n", stderr);
            }
        }
        errno = mw_saved_errno;
    }

"""
    text = replace_once(text, "    // Get the starting time (can ignore the times above)\n",
                        setup + "    // Get the starting time (can ignore the times above)\n")
    text = replace_once(text, """                    // Append the samples
                    sample_fifo_append(enc_ctx->fifo,
""", """                    mw_tap_packed(enc_ctx->mw_tap, 'A', pcm, samples,
                                  enc_ctx->channels, enc_ctx->frequency, enc_ctx->sample_bytes,
                                  aud_fmt, aud_fmt == mlt_audio_f32le,
                                  mlt_frame_get_position(frame), AV_NOPTS_VALUE,
                                  MW_TAP_UNKNOWN, MW_TAP_UNKNOWN);
                    // Append the samples
                    sample_fifo_append(enc_ctx->fifo,
""")
    text = replace_once(text, "on_fatal_error:\n\n", """on_fatal_error:

    {
        int mw_saved_errno = errno;
        enc_ctx->mw_tap_finish_rc = mw_tap_finish(&enc_ctx->mw_tap);
        fprintf(stderr, "MLT_AUDIO_TAP_STATE requested=%d setup=%d finish=%d\\n",
                enc_ctx->mw_tap_requested, enc_ctx->mw_tap_setup_ok, enc_ctx->mw_tap_finish_rc);
        errno = mw_saved_errno;
    }

""")
    # The instrument preserves all original calls, arguments, and codec/helper code.
    for token in ("avcodec_send_frame(", "avcodec_receive_packet(",
                  "sample_fifo_fetch(", "sample_fifo_append(", "avcodec_fill_audio_frame("):
        if text.count(token) != source.decode("utf-8").count(token):
            raise ValueError("instrumentation changed native call count")
    helper_start = "static uint8_t *interleaved_to_planar("
    if text.count(helper_start) != 1:
        raise ValueError("conversion definition was not uniquely preserved")
    if helper_start in text:
        original_helper = source.decode("utf-8").split(helper_start, 1)[1].split("\n}\n", 1)[0]
        current_helper = text.split(helper_start, 1)[1].split("\n}\n", 1)[0]
        if original_helper != current_helper:
            raise ValueError("instrumentation changed the conversion under diagnosis")
    return text.encode("utf-8")

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consumer", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if not args.consumer.is_file() or args.consumer.is_symlink():
            raise ValueError("consumer must be a regular source file")
        source = args.consumer.read_bytes()
        header = Path(__file__).with_name("audio_tap.h").read_bytes()
        result = instrument(source)
        args.output_dir.mkdir(parents=False, exist_ok=False)
        for name, data in (("consumer_avformat.c", result), ("audio_tap.h", header)):
            with (args.output_dir / name).open("xb") as handle:
                handle.write(data)
        report = {
            "schema": "motionwright.mlt-instrumented-source/1",
            "input_consumer_sha256": hashlib.sha256(source).hexdigest(),
            "output_consumer_sha256": hashlib.sha256(result).hexdigest(),
            "observer_header_sha256": hashlib.sha256(header).hexdigest(),
            "instrumenter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "codec_calls_preserved": True,
            "conversion_preserved": True,
            "source_generation_only": True,
            "provider_admission": "NOT_ESTABLISHED",
            "native_dispatch": False,
            "release_authorized": False,
        }
        with (args.output_dir / "instrumented-source.json").open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, ValueError, UnicodeError) as exc:
        print(json.dumps({"source_generation_complete": False, "error": str(exc)}))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
