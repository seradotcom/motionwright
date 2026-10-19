/*
 * Real libavcodec/libavutil source-level test for a partial planar MLT AAC frame.
 * Build with the exact generated repair-trial header in the include path:
 *
 * cc -std=c11 -O1 -g -Wall -Wextra -Werror \
 *    -fsanitize=address,undefined -fno-sanitize-recover=all \
 *    -I"$MW_DIAG_BUILD/repair-trial" \
 *    test_planar_padding.c \
 *    $(pkg-config --cflags --libs libavcodec libavutil) \
 *    -o "$MW_DIAG_BUILD/test_planar_padding"
 *
 * This binary tests layout only, not an authorized MLT effect or AAC master.
 * The production helper is included verbatim from the generated source repair.
 */
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libavcodec/avcodec.h>
#include <libavutil/samplefmt.h>

#define MAX_AUDIO_STREAMS (8)
#define AUDIO_ENCODE_BUFFER_SIZE (48000 * 2 * MAX_AUDIO_STREAMS)

static void *mlt_pool_alloc(size_t size)
{
    return malloc(size);
}
#include "planar-helper-under-test.h"

static int errors = 0;

static void failure(const char *what, enum AVSampleFormat format, int samples,
                    int prepared, int channel, int index)
{
    fprintf(stderr,
            "MLT_PLANAR_RED %s format=%d consumed=%d prepared=%d channel=%d frame=%d\n",
            what, (int) format, samples, prepared, channel, index);
    ++errors;
}

static void source_sample(enum AVSampleFormat format,
                          int channel, int index, uint8_t *dst)
{
    switch (format) {
    case AV_SAMPLE_FMT_FLTP: {
        float sample = channel ? 0.625f - index / 4096.f
                               : 0.125f + index / 4096.f;
        memcpy(dst, &sample, sizeof(sample));
        break;
    }
    case AV_SAMPLE_FMT_S16P: {
        int16_t sample = channel ? (int16_t) (-22000 + index)
                                 : (int16_t) (10000 + index);
        memcpy(dst, &sample, sizeof(sample));
        break;
    }
    case AV_SAMPLE_FMT_S32P: {
        int32_t sample = channel ? -200000000 + index * 512
                                 : 100000000 + index * 512;
        memcpy(dst, &sample, sizeof(sample));
        break;
    }
    case AV_SAMPLE_FMT_U8P: {
        uint8_t sample = (uint8_t) ((channel ? 200 : 40) + index % 22);
        memcpy(dst, &sample, sizeof(sample));
        break;
    }
    default:
        fprintf(stderr, "MLT_PLANAR_UNSUPPORTED_FORMAT %d\n", (int) format);
        exit(2);
    }
}

static void exercise(enum AVSampleFormat format, int consumed,
                     int prepared, int expect_legacy_bad)
{
    const int channels = 2;
    const int bps = av_get_bytes_per_sample(format);
    int ffmpeg_linesize = 0;
    int frame_len = av_samples_get_buffer_size(
        &ffmpeg_linesize, channels, prepared, format, 0);
    if (bps <= 0 || frame_len <= 0 || ffmpeg_linesize <= 0
        || frame_len > AUDIO_ENCODE_BUFFER_SIZE) {
        failure("invalid actual FFmpeg planar layout", format,
                consumed, prepared, -1, -1);
        return;
    }

    uint8_t *interleaved = calloc((size_t) consumed * channels, (size_t) bps);
    uint8_t *old_compacted = calloc(1, AUDIO_ENCODE_BUFFER_SIZE);
    if (!interleaved || !old_compacted) {
        fprintf(stderr, "MLT_PLANAR_ALLOCATION_FAILED\n");
        exit(2);
    }
    for (int i = 0; i < consumed; ++i) {
        for (int c = 0; c < channels; ++c) {
            uint8_t *at = interleaved + ((size_t) i * channels + c) * bps;
            source_sample(format, c, i, at);
            // Historical compact packing: channel 1 begins after consumed,
            // which differs from FFmpeg's padded linesize on the AAC tail.
            memcpy(old_compacted + ((size_t) c * consumed + i) * bps, at, bps);
        }
    }

    uint8_t *repaired = interleaved_to_planar(
        consumed, prepared, channels, interleaved, bps, format);
    if (!repaired) {
        failure("original-source repair unexpectedly refused admitted frame",
                format, consumed, prepared, -1, -1);
        free(interleaved);
        free(old_compacted);
        return;
    }
    AVFrame *good = av_frame_alloc();
    AVFrame *historical = av_frame_alloc();
    if (!good || !historical) {
        fprintf(stderr, "MLT_PLANAR_AVFRAME_ALLOCATION_FAILED\n");
        exit(2);
    }
    // Derive silence from actual FFmpeg 6.1 libavutil, not from the repair
    // implementation or an assumed all-zero PCM encoding. U8P uses 0x80.
    uint8_t *silence = malloc(AUDIO_ENCODE_BUFFER_SIZE);
    if (!silence) {
        fprintf(stderr, "MLT_PLANAR_REFERENCE_ALLOCATION_FAILED\n");
        exit(2);
    }
    memset(silence, 0xa5, AUDIO_ENCODE_BUFFER_SIZE);
    uint8_t *silence_planes[2] = {silence, silence + ffmpeg_linesize};
    int silence_rc = av_samples_set_silence(
        silence_planes, 0, prepared, channels, format);
    if (silence_rc < 0) {
        failure("original FFmpeg silence oracle rejected the declared frame",
                format, consumed, prepared, -1, -1);
        free(silence);
        av_frame_free(&good);
        av_frame_free(&historical);
        free(repaired);
        free(interleaved);
        free(old_compacted);
        return;
    }
    good->nb_samples = prepared;
    historical->nb_samples = prepared;
    int good_ret = avcodec_fill_audio_frame(
        good, channels, format, repaired, AUDIO_ENCODE_BUFFER_SIZE, 0);
    int original_ret = avcodec_fill_audio_frame(
        historical, channels, format, old_compacted,
        AUDIO_ENCODE_BUFFER_SIZE, 0);
    if (good_ret < 0 || original_ret < 0 || good->linesize[0] != ffmpeg_linesize) {
        failure("avcodec_fill_audio_frame actual stride diverged",
                format, consumed, prepared, -1, -1);
    } else {
        int control_disagree = 0;
        for (int c = 0; c < channels; ++c) {
            for (int i = 0; i < prepared; ++i) {
                uint8_t expected[8] = {0};
                if (i < consumed) {
                    source_sample(format, c, i, expected);
                } else {
                    memcpy(expected,
                           silence_planes[c] + (size_t) i * bps, (size_t) bps);
                }
                if (memcmp(good->extended_data[c] + (size_t) i * bps,
                           expected, (size_t) bps) != 0) {
                    failure("repaired plane diverged from source or FFmpeg silence",
                            format, consumed, prepared, c, i);
                    break;
                }
                if (c == 1 && i < consumed
                    && memcmp(historical->extended_data[c] + (size_t) i * bps,
                              expected, (size_t) bps) != 0) {
                    control_disagree = 1;
                }
            }
        }
        // Source-independent U8P negative control: the original compact
        // buffer initializes unconsumed bytes to zero, but FFmpeg's signed
        // offset for unsigned 8-bit silence requires 0x80 instead.
        if (format == AV_SAMPLE_FMT_U8P && consumed < prepared) {
            if (silence_planes[1][consumed] != 0x80
                || historical->extended_data[1][consumed] == 0x80) {
                failure("legacy all-zero U8P silence unexpectedly valid",
                        format, consumed, prepared, 1, consumed);
            }
        }
        if (expect_legacy_bad && !control_disagree) {
            failure("legacy compact channel unexpectedly equal on partial frame",
                    format, consumed, prepared, 1, 0);
        } else if (!expect_legacy_bad && control_disagree) {
            failure("full frame should not alter legacy channel placement",
                    format, consumed, prepared, 1, 0);
        }
    }

    av_frame_free(&good);
    av_frame_free(&historical);
    free(silence);
    free(repaired);
    free(interleaved);
    free(old_compacted);
}

int main(void)
{
    enum AVSampleFormat formats[] = {
        AV_SAMPLE_FMT_FLTP, AV_SAMPLE_FMT_S16P,
        AV_SAMPLE_FMT_S32P, AV_SAMPLE_FMT_U8P,
    };
    for (size_t i = 0; i < sizeof(formats) / sizeof(formats[0]); ++i) {
        exercise(formats[i], 576, 1024, 1);
        exercise(formats[i], 1, 1024, 1);
        exercise(formats[i], 1024, 1024, 0);
    }
    if (errors) {
        fprintf(stderr, "MLT_PLANAR_FAIL checks=12 errors=%d\n", errors);
        return 1;
    }
    puts("MLT_PLANAR_LAYOUT_PASS checks=12 source-layout-only, no native master");
    return 0;
}
