/* Bounded, opt-in observer for the pinned MLT avformat consumer.
 * This code does not convert PCM, mutate AVFrames, or invoke a codec.
 * Output-path authority belongs to the existing launch/grant boundary.
 */
#ifndef MOTIONWRIGHT_MLT_AUDIO_TAP_H
#define MOTIONWRIGHT_MLT_AUDIO_TAP_H

#include <errno.h>
#include <fcntl.h>
#include <float.h>
#include <limits.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <libavcodec/avcodec.h>
#include <libavutil/frame.h>
#include <libavutil/samplefmt.h>

#define MW_TAP_HEADER_BYTES 64u
#define MW_TAP_WORDS 22u
#define MW_TAP_RECORD_BYTES (MW_TAP_WORDS * 8u)
#define MW_TAP_CAPACITY (2u * 1024u * 1024u)
#define MW_TAP_MAX_RECORDS 256u
#define MW_TAP_MAX_SAMPLES 8192
#define MW_TAP_UNKNOWN UINT64_MAX
#define MW_TAP_NOT_SEEN ((uint64_t) INT64_MIN)

enum mw_tap_word {
    MW_KIND, MW_SAMPLE_FORMAT, MW_ORIGIN_FORMAT, MW_NCH, MW_SAMPLE_RATE,
    MW_PREPARED_NB_SAMPLES, MW_FIFO_CONSUMED_SAMPLES, MW_PREPARED_PTS,
    MW_MLT_FRAME_POSITION, MW_LAYOUT, MW_PLANE0_BYTES, MW_PLANE1_BYTES,
    MW_PLANE0_OFFSET, MW_PLANE1_OFFSET, MW_FILL_RC, MW_SEND_RC,
    MW_FIFO_BYTES_BEFORE, MW_FIFO_BYTES_AFTER, MW_TIME_BASE_NUM,
    MW_TIME_BASE_DEN, MW_CODEC_CAPABILITIES, MW_CODEC_FRAME_SIZE, MW_WORD_COUNT
};
enum mw_tap_layout { MW_PACKED, MW_PLANAR, MW_METADATA_ONLY };

_Static_assert(MW_WORD_COUNT == MW_TAP_WORDS, "tap word count");
_Static_assert(CHAR_BIT == 8 && sizeof(float) == 4, "tap requires float32");
_Static_assert(FLT_RADIX == 2 && FLT_MANT_DIG == 24 && FLT_MAX_EXP == 128,
               "tap requires IEEE binary32");

typedef struct mw_audio_tap {
    int fd;
    size_t used;
    size_t last_record;
    uint64_t records;
    uint64_t errors;
    uint8_t data[MW_TAP_CAPACITY];
} mw_audio_tap;

static void mw_tap_error(mw_audio_tap *tap)
{
    if (tap && tap->errors != UINT64_MAX)
        ++tap->errors;
}

static void mw_tap_put_u64(uint8_t *dst, uint64_t value)
{
    for (unsigned int n = 0; n < 8; ++n)
        dst[n] = (uint8_t) (value >> (8u * n));
}

static uint64_t mw_tap_get_u64(const uint8_t *src)
{
    uint64_t value = 0;
    for (unsigned int n = 0; n < 8; ++n)
        value |= (uint64_t) src[n] << (8u * n);
    return value;
}

static mw_audio_tap *mw_tap_new_memory(void)
{
    mw_audio_tap *tap = calloc(1, sizeof(*tap));
    if (tap) {
        tap->fd = -1;
        tap->used = MW_TAP_HEADER_BYTES;
    }
    return tap;
}

static void mw_tap_finalize_header(mw_audio_tap *tap)
{
    const uint16_t endian_probe = 1;
    if (!tap)
        return;
    memcpy(tap->data, "MWATAP01", 8);
    mw_tap_put_u64(tap->data + 8, 1);
    mw_tap_put_u64(tap->data + 16, MW_TAP_WORDS);
    mw_tap_put_u64(tap->data + 24, tap->records);
    mw_tap_put_u64(tap->data + 32, (uint64_t) tap->used);
    mw_tap_put_u64(tap->data + 40, tap->errors);
    mw_tap_put_u64(tap->data + 48, *(const uint8_t *) &endian_probe ? 1 : 2);
    mw_tap_put_u64(tap->data + 56, sizeof(float));
}

static void mw_tap_init_words(uint64_t w[MW_TAP_WORDS], unsigned int kind,
                              const AVCodecContext *codec)
{
    for (unsigned int n = 0; n < MW_TAP_WORDS; ++n)
        w[n] = MW_TAP_UNKNOWN;
    w[MW_KIND] = kind;
    w[MW_SAMPLE_FORMAT] = (uint64_t) (int64_t) AV_SAMPLE_FMT_NONE;
    w[MW_PREPARED_PTS] = (uint64_t) (int64_t) AV_NOPTS_VALUE;
    w[MW_MLT_FRAME_POSITION] = (uint64_t) (int64_t) -1;
    w[MW_LAYOUT] = MW_METADATA_ONLY;
    w[MW_PLANE0_BYTES] = w[MW_PLANE1_BYTES] = 0;
    w[MW_PLANE0_OFFSET] = w[MW_PLANE1_OFFSET] = 0;
    w[MW_FILL_RC] = w[MW_SEND_RC] = MW_TAP_NOT_SEEN;
    w[MW_TIME_BASE_NUM] = w[MW_TIME_BASE_DEN] = 0;
    w[MW_CODEC_CAPABILITIES] = w[MW_CODEC_FRAME_SIZE] = 0;
    if (codec) {
        w[MW_TIME_BASE_NUM] = (uint64_t) (int64_t) codec->time_base.num;
        w[MW_TIME_BASE_DEN] = (uint64_t) (int64_t) codec->time_base.den;
        w[MW_CODEC_FRAME_SIZE] = (uint64_t) (int64_t) codec->frame_size;
        w[MW_CODEC_CAPABILITIES] = codec->codec
            ? (uint64_t) (unsigned int) codec->codec->capabilities : MW_TAP_UNKNOWN;
    }
}

static size_t mw_tap_record(mw_audio_tap *tap, const uint64_t w[MW_TAP_WORDS],
                            const void *p0, size_t n0, const void *p1, size_t n1)
{
    if (!tap)
        return 0;
    if (!w || tap->used < MW_TAP_HEADER_BYTES || tap->used > MW_TAP_CAPACITY
        || tap->records >= MW_TAP_MAX_RECORDS || (n0 && !p0) || (n1 && !p1)) {
        mw_tap_error(tap);
        return 0;
    }
    size_t remaining = MW_TAP_CAPACITY - tap->used;
    if (remaining < MW_TAP_RECORD_BYTES || n0 > remaining - MW_TAP_RECORD_BYTES
        || n1 > remaining - MW_TAP_RECORD_BYTES - n0) {
        mw_tap_error(tap);
        return 0;
    }
    size_t offset = tap->used;
    for (unsigned int n = 0; n < MW_TAP_WORDS; ++n)
        mw_tap_put_u64(tap->data + offset + 8u * n,
                      n == MW_PLANE0_BYTES ? n0 : n == MW_PLANE1_BYTES ? n1 : w[n]);
    if (n0)
        memcpy(tap->data + offset + MW_TAP_RECORD_BYTES, p0, n0);
    if (n1)
        memcpy(tap->data + offset + MW_TAP_RECORD_BYTES + n0, p1, n1);
    tap->last_record = offset;
    tap->used += MW_TAP_RECORD_BYTES + n0 + n1;
    ++tap->records;
    return offset;
}

/* A observes input before append. F observes the original FIFO fetch. */
static size_t mw_tap_packed(mw_audio_tap *tap, unsigned int kind, const void *pcm,
                            int nsamples, int nch, int rate, int sample_bytes,
                            int origin_format, int is_f32_packed,
                            int64_t frame_position, int64_t pts,
                            uint64_t fifo_before, uint64_t fifo_after)
{
    if (!tap)
        return 0;
    uint64_t w[MW_TAP_WORDS];
    mw_tap_init_words(w, kind, NULL);
    w[MW_SAMPLE_FORMAT] = AV_SAMPLE_FMT_FLT;
    w[MW_ORIGIN_FORMAT] = (uint64_t) (int64_t) origin_format;
    w[MW_NCH] = (uint64_t) (int64_t) nch;
    w[MW_SAMPLE_RATE] = (uint64_t) (int64_t) rate;
    w[MW_PREPARED_NB_SAMPLES] = (uint64_t) (int64_t) nsamples;
    w[MW_PREPARED_PTS] = (uint64_t) pts;
    w[MW_MLT_FRAME_POSITION] = (uint64_t) frame_position;
    w[MW_FIFO_BYTES_BEFORE] = fifo_before;
    w[MW_FIFO_BYTES_AFTER] = fifo_after;
    if ((kind != 'A' && kind != 'F') || !pcm || !is_f32_packed
        || nsamples <= 0 || nsamples > MW_TAP_MAX_SAMPLES
        || nch != 2 || rate != 48000 || sample_bytes != 4) {
        mw_tap_error(tap);
        return mw_tap_record(tap, w, NULL, 0, NULL, 0);
    }
    size_t length = (size_t) nsamples * 2u * sizeof(float);
    if (kind == 'F') {
        if (fifo_before == MW_TAP_UNKNOWN || fifo_after == MW_TAP_UNKNOWN
            || fifo_before < fifo_after || fifo_before - fifo_after != length) {
            mw_tap_error(tap);
            return mw_tap_record(tap, w, NULL, 0, NULL, 0);
        }
        w[MW_FIFO_CONSUMED_SAMPLES] = (uint64_t) nsamples;
    }
    w[MW_LAYOUT] = MW_PACKED;
    return mw_tap_record(tap, w, pcm, length, NULL, 0);
}

/* Integer pointer comparison is restricted to the admitted Linux target. */
static int mw_tap_region(const void *backing, size_t capacity, const void *region,
                         size_t length, uint64_t *offset_out)
{
    if (!backing || !region || !offset_out)
        return 0;
    uintptr_t base = (uintptr_t) backing, address = (uintptr_t) region;
    if (address < base || address - base > (uintptr_t) capacity
        || length > capacity - (size_t) (address - base))
        return 0;
    *offset_out = (uint64_t) (address - base);
    return 1;
}

/* Copy each actual extended_data plane, including the declared padding. */
static size_t mw_tap_frame(mw_audio_tap *tap, const AVCodecContext *codec,
                           const AVFrame *frame, int consumed, int fill_rc,
                           const void *backing, size_t capacity)
{
    if (!tap)
        return 0;
    uint64_t w[MW_TAP_WORDS];
    int nch = codec ? codec->ch_layout.nb_channels : 0;
    int rate = codec ? codec->sample_rate : 0;
    int fmt = codec ? (int) codec->sample_fmt : AV_SAMPLE_FMT_NONE;
    int prepared = frame ? frame->nb_samples : 0;
    mw_tap_init_words(w, consumed == 0 ? 'D' : 'B', codec);
    w[MW_SAMPLE_FORMAT] = (uint64_t) (int64_t) fmt;
    w[MW_ORIGIN_FORMAT] = (uint64_t) (int64_t) (frame ? frame->format : -1);
    w[MW_NCH] = (uint64_t) (int64_t) nch;
    w[MW_SAMPLE_RATE] = (uint64_t) (int64_t) rate;
    w[MW_PREPARED_NB_SAMPLES] = (uint64_t) (int64_t) prepared;
    w[MW_FIFO_CONSUMED_SAMPLES] = (uint64_t) (int64_t) consumed;
    w[MW_PREPARED_PTS] = (uint64_t) (frame ? frame->pts : AV_NOPTS_VALUE);
    w[MW_FILL_RC] = (uint64_t) (int64_t) fill_rc;
    if (consumed == 0)
        return mw_tap_record(tap, w, NULL, 0, NULL, 0);
    if (fill_rc < 0 || !codec || !frame || nch != 2 || rate != 48000
        || (fmt != AV_SAMPLE_FMT_FLT && fmt != AV_SAMPLE_FMT_FLTP)
        || frame->format != fmt || prepared <= 0 || prepared > MW_TAP_MAX_SAMPLES
        || consumed < 0 || consumed > prepared || !frame->extended_data) {
        mw_tap_error(tap);
        return mw_tap_record(tap, w, NULL, 0, NULL, 0);
    }
    size_t n0 = (size_t) prepared * sizeof(float), n1 = 0;
    const uint8_t *p0 = frame->extended_data[0], *p1 = NULL;
    if (fmt == AV_SAMPLE_FMT_FLTP) {
        p1 = frame->extended_data[1];
        n1 = n0;
    } else {
        n0 *= 2u;
    }
    if (!mw_tap_region(backing, capacity, p0, n0, &w[MW_PLANE0_OFFSET])
        || (n1 && !mw_tap_region(backing, capacity, p1, n1, &w[MW_PLANE1_OFFSET]))) {
        mw_tap_error(tap);
        return mw_tap_record(tap, w, NULL, 0, NULL, 0);
    }
    w[MW_LAYOUT] = fmt == AV_SAMPLE_FMT_FLTP ? MW_PLANAR : MW_PACKED;
    return mw_tap_record(tap, w, p0, n0, p1, n1);
}

static void mw_tap_send_result(mw_audio_tap *tap, size_t offset, int original_rc)
{
    if (!tap || !offset)
        return;
    if (offset != tap->last_record || offset < MW_TAP_HEADER_BYTES
        || offset > tap->used || tap->used - offset < MW_TAP_RECORD_BYTES) {
        mw_tap_error(tap);
        return;
    }
    uint64_t kind = mw_tap_get_u64(tap->data + offset + 8u * MW_KIND);
    if ((kind != 'B' && kind != 'D')
        || mw_tap_get_u64(tap->data + offset + 8u * MW_SEND_RC) != MW_TAP_NOT_SEEN) {
        mw_tap_error(tap);
        return;
    }
    mw_tap_put_u64(tap->data + offset + 8u * MW_SEND_RC, (uint64_t) (int64_t) original_rc);
}

/* Caller must have an authentic grant covering this exact private output path.
 * O_EXCL reserves it once. No fallback path, truncation, chmod, or retry.
 * Linux is intentional: provider admission must identify this instrumented build.
 */
static mw_audio_tap *mw_tap_open(const char *path)
{
    mw_audio_tap *tap = mw_tap_new_memory();
    if (!tap) {
        fputs("MLT_AUDIO_TAP_INVALID allocation\n", stderr);
        return NULL;
    }
    if (!path || path[0] != '/') {
        fputs("MLT_AUDIO_TAP_INVALID absolute_output_required\n", stderr);
        free(tap);
        return NULL;
    }
    tap->fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (tap->fd < 0) {
        fputs("MLT_AUDIO_TAP_INVALID exclusive_open\n", stderr);
        free(tap);
        return NULL;
    }
    return tap;
}

/* Called once after original processing/flush. Capture failure is separate
 * from the consumer's native result; a missing/incomplete trace is invalid.
 */
static int mw_tap_finish(mw_audio_tap **owner)
{
    if (!owner || !*owner)
        return 0;
    mw_audio_tap *tap = *owner;
    *owner = NULL;
    mw_tap_finalize_header(tap);
    int failed = tap->errors != 0 || tap->fd < 0;
    if (tap->fd >= 0) {
        size_t done = 0;
        while (done < tap->used) {
            ssize_t written = write(tap->fd, tap->data + done, tap->used - done);
            if (written < 0 && errno == EINTR)
                continue;
            if (written <= 0) {
                failed = 1;
                break;
            }
            done += (size_t) written;
        }
        if (fsync(tap->fd) != 0)
            failed = 1;
        if (close(tap->fd) != 0)
            failed = 1;
    }
    free(tap);
    fputs(failed ? "MLT_AUDIO_TAP_INVALID capture_or_write\n"
                 : "MLT_AUDIO_TAP_WRITTEN verify_required\n", stderr);
    return failed ? -1 : 0;
}
#endif
