/* Source-only callsite gate. No codec is opened and no media is produced.
 * Compile with the exact generated planar-helper-under-test.h and the
 * authenticated encoder-submit-under-test.h in one private include directory.
 */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libavcodec/avcodec.h>
#include <libavutil/common.h>
#include <libavutil/samplefmt.h>

#define MAX_AUDIO_STREAMS (8)
#define AUDIO_ENCODE_BUFFER_SIZE (48000 * 2 * MAX_AUDIO_STREAMS)

static void *mlt_pool_alloc(size_t size) { return malloc(size); }
#include "planar-helper-under-test.h"

static int errors;
static int sends;
static int active_samples;
static int active_declared;
static int active_linesize;
static int64_t active_pts;

static void require(int ok, const char *reason)
{
    if (!ok) {
        fprintf(stderr, "MLT_LAST_FRAME_RED %s samples=%d declared=%d\n",
                reason, active_samples, active_declared);
        ++errors;
    }
}

static float source_sample(int channel, int index)
{
    return channel ? 0.625f - index / 4096.f : 0.125f + index / 4096.f;
}

static int observe_send(AVCodecContext *codec, const AVFrame *frame)
{
    ++sends;
    require(codec != NULL, "codec missing");
    if (!frame) {
        require(active_samples == 0, "positive tail sent as NULL");
        return 0;
    }
    require(active_samples > 0, "empty drain sent as data");
    require(frame->nb_samples == active_declared, "encoder-visible sample count");
    require(frame->pts == active_pts, "source PTS changed");
    require(frame->linesize[0] == active_linesize, "prepared stride changed");
    if (!frame->extended_data || !frame->extended_data[0] || !frame->extended_data[1]) {
        require(0, "prepared planes missing");
        return -1;
    }
    require(frame->extended_data[1] - frame->extended_data[0] == active_linesize,
            "second plane moved");
    for (int channel = 0; channel < 2; ++channel) {
        for (int index = 0; index < active_samples; ++index) {
            float expected = source_sample(channel, index);
            if (memcmp(frame->extended_data[channel] + (size_t) index * sizeof(float),
                       &expected, sizeof(expected)) != 0) {
                require(0, "source channel content changed");
                break;
            }
        }
    }
    return 0;
}

typedef struct {
    int audio_input_frame_size;
    int channels;
    uint8_t *audio_buf_1;
    AVFrame *audio_avframe;
    int64_t sample_count[1];
    void *properties;
} encode_ctx_t;

static void mlt_log_error(void *properties, const char *message)
{
    (void) properties;
    (void) message;
    require(0, "actual source rejected frame preparation");
}

/* Preserve the original consumer API, including its deprecated channels field. */
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
static int submit_actual_source(encode_ctx_t *ctx, AVCodecContext *codec, int samples)
{
    const int i = 0;
#define avcodec_send_frame observe_send
#include "encoder-submit-under-test.h"
#undef avcodec_send_frame
    if (p != ctx->audio_buf_1)
        free(p);
    return ret;
}

static void exercise(int samples, int capabilities, int64_t start)
{
    const int prepared = 1024;
    active_samples = samples;
    active_declared = samples > 0 && samples < prepared
        && (capabilities & AV_CODEC_CAP_SMALL_LAST_FRAME) ? samples : prepared;
    active_pts = start;
    int bytes = av_samples_get_buffer_size(&active_linesize, 2, prepared,
                                           AV_SAMPLE_FMT_FLTP, 0);
    require(bytes > 0, "FFmpeg reference layout unavailable");
    AVCodec descriptor = {0};
    descriptor.capabilities = capabilities;
    AVCodecContext codec = {0};
    codec.codec = &descriptor;
    codec.sample_fmt = AV_SAMPLE_FMT_FLTP;
    codec.channels = 2;
    encode_ctx_t ctx = {0};
    ctx.audio_input_frame_size = prepared;
    ctx.channels = 2;
    ctx.audio_buf_1 = calloc(1, AUDIO_ENCODE_BUFFER_SIZE);
    ctx.audio_avframe = av_frame_alloc();
    if (!ctx.audio_buf_1 || !ctx.audio_avframe)
        exit(2);
    ctx.audio_avframe->format = AV_SAMPLE_FMT_FLTP;
    ctx.sample_count[0] = start;
    for (int index = 0; index < samples; ++index) {
        for (int channel = 0; channel < 2; ++channel) {
            float value = source_sample(channel, index);
            memcpy(ctx.audio_buf_1 + ((size_t) index * 2 + channel) * sizeof(float),
                   &value, sizeof(value));
        }
    }
    int before = sends;
    require(submit_actual_source(&ctx, &codec, samples) == 0, "submission failed");
    require(sends == before + 1, "send count changed");
    /* samples=0 deliberately preserves the existing NULL-drain clock behavior. */
    require(ctx.sample_count[0] == start + active_declared, "submitted sample clock");
    av_frame_free(&ctx.audio_avframe);
    free(ctx.audio_buf_1);
}
#pragma GCC diagnostic pop

int main(void)
{
    const AVCodec *aac = avcodec_find_encoder(AV_CODEC_ID_AAC);
    require(aac && (aac->capabilities & AV_CODEC_CAP_SMALL_LAST_FRAME),
            "AAC lacks SMALL_LAST_FRAME capability");
    const int small = AV_CODEC_CAP_SMALL_LAST_FRAME;
    exercise(576, small, 52224);
    exercise(256, small, 287744);
    exercise(1, small, 4096);
    exercise(1023, small, 4096);
    exercise(1024, small, 4096);
    exercise(0, small, 52800);
    exercise(576, 0, 52224);
    exercise(256, AV_CODEC_CAP_VARIABLE_FRAME_SIZE, 287744);
    if (errors) {
        fprintf(stderr, "MLT_LAST_FRAME_FAIL cases=8 errors=%d\n", errors);
        return 1;
    }
    puts("MLT_LAST_FRAME_CALLSITE_PASS cases=8 source-only; codec-not-opened; native-master-NOT_RUN");
    return 0;
}
