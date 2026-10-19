/* Real libavutil/libavcodec buffer API tests; the encoder is never opened.
 * Constructor samples are test inputs, not native production evidence.
 */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <libavutil/channel_layout.h>
#include <libavutil/error.h>
#include "audio_tap.h"

typedef struct fixture {
    AVCodecContext *codec;
    AVFrame *frame;
    float *packed;
    uint8_t *backing;
    size_t capacity;
    int consumed, prepared, fill_rc, linesize;
} fixture;
static unsigned int sends, receives, null_sends;
static float left_marker(int n) { return ((float) n + 1) / 4096.0f; }
static float right_marker(int n) { return -((float) n + 2049) / 4096.0f; }
static int send_stub(const AVFrame *frame) { ++sends; if (!frame) ++null_sends; return 0; }
static int receive_stub(void) { ++receives; return AVERROR(EAGAIN); }

static uint64_t field(const mw_audio_tap *tap, size_t offset, unsigned int index)
{
    assert(index < MW_TAP_WORDS && offset >= MW_TAP_HEADER_BYTES);
    assert(offset <= tap->used && tap->used - offset >= MW_TAP_RECORD_BYTES);
    return mw_tap_get_u64(tap->data + offset + 8u * index);
}
static const uint8_t *plane0(const mw_audio_tap *tap, size_t offset)
{ return tap->data + offset + MW_TAP_RECORD_BYTES; }
static const uint8_t *plane1(const mw_audio_tap *tap, size_t offset)
{ return plane0(tap, offset) + field(tap, offset, MW_PLANE0_BYTES); }
static void is_float(const uint8_t *p, float expected)
{ assert(memcmp(p, &expected, sizeof(expected)) == 0); }
static void is_zero(const uint8_t *p, size_t length)
{ for (size_t i = 0; i < length; ++i) assert(p[i] == 0); }

static fixture make_fixture(int consumed, int prepared, enum AVSampleFormat fmt,
                             int compact, int align)
{
    fixture f = {0};
    assert(consumed > 0 && consumed <= prepared && prepared <= MW_TAP_MAX_SAMPLES);
    const AVCodec *aac = avcodec_find_encoder(AV_CODEC_ID_AAC);
    assert(aac);
    f.codec = avcodec_alloc_context3(aac);
    f.frame = av_frame_alloc();
    assert(f.codec && f.frame);
    f.codec->sample_fmt = fmt;
    f.codec->sample_rate = 48000;
    f.codec->frame_size = 1024; /* Constructor metadata; no avcodec_open2. */
    f.codec->time_base = (AVRational) {1, 48000};
    av_channel_layout_default(&f.codec->ch_layout, 2);
    f.frame->format = fmt;
    f.frame->sample_rate = 48000;
    f.frame->nb_samples = prepared;
    f.frame->pts = 52224;
    av_channel_layout_default(&f.frame->ch_layout, 2);
    int needed = av_samples_get_buffer_size(&f.linesize, 2, prepared, fmt, align);
    assert(needed > 0);
    f.capacity = (size_t) needed;
    if (f.capacity < (size_t) consumed * 8u) f.capacity = (size_t) consumed * 8u;
    f.backing = calloc(1, f.capacity);
    f.packed = calloc((size_t) consumed * 2u, sizeof(float));
    assert(f.backing && f.packed);
    f.consumed = consumed; f.prepared = prepared;
    for (int n = 0; n < consumed; ++n) {
        f.packed[2*n] = left_marker(n); f.packed[2*n+1] = right_marker(n);
        for (int c = 0; c < 2; ++c) {
            size_t offset = fmt == AV_SAMPLE_FMT_FLTP
                ? (size_t) c * (compact ? (size_t) consumed * 4u : (size_t) f.linesize) + (size_t) n * 4u
                : ((size_t) n * 2u + (size_t) c) * 4u;
            assert(offset + 4u <= f.capacity);
            memcpy(f.backing + offset, f.packed + 2*n+c, 4);
        }
    }
    f.fill_rc = avcodec_fill_audio_frame(f.frame, 2, fmt, f.backing, (int) f.capacity, align);
    assert(f.fill_rc >= 0);
    return f;
}
static void destroy(fixture *f)
{
    av_frame_free(&f->frame); avcodec_free_context(&f->codec);
    free(f->packed); free(f->backing); memset(f, 0, sizeof(*f));
}
static size_t observe_send(mw_audio_tap *tap, fixture *f, int consumed)
{
    unsigned int old_send = sends, old_receive = receives;
    int old_nb = f->frame->nb_samples;
    int64_t old_pts = f->frame->pts;
    uint8_t **old_planes = f->frame->extended_data;
    uint8_t *snapshot = malloc(f->capacity);
    assert(snapshot); memcpy(snapshot, f->backing, f->capacity);
    size_t offset = mw_tap_frame(tap, f->codec, f->frame, consumed,
                                f->fill_rc, f->backing, f->capacity);
    int ret = send_stub(consumed ? f->frame : NULL);
    mw_tap_send_result(tap, offset, ret);
    if (ret >= 0) ret = receive_stub();
    assert(ret == AVERROR(EAGAIN) && sends == old_send + 1 && receives == old_receive + 1);
    assert(f->frame->nb_samples == old_nb && f->frame->pts == old_pts);
    assert(f->frame->extended_data == old_planes && memcmp(snapshot, f->backing, f->capacity) == 0);
    free(snapshot); return offset;
}
static void test_compact(void)
{
    fixture f = make_fixture(576, 1024, AV_SAMPLE_FMT_FLTP, 1, 0);
    mw_audio_tap *tap = mw_tap_new_memory(); assert(tap);
    size_t a = mw_tap_packed(tap, 'A', f.packed, 576, 2, 48000, 4, 5, 1,
                            32, AV_NOPTS_VALUE, MW_TAP_UNKNOWN, MW_TAP_UNKNOWN);
    size_t fetched = mw_tap_packed(tap, 'F', f.packed, 576, 2, 48000, 4, 5, 1,
                                  -1, 52224, 576u*8u, 0);
    size_t b = observe_send(tap, &f, 576);
    assert(a && fetched && b && tap->errors == 0);
    assert(field(tap, a, MW_FIFO_CONSUMED_SAMPLES) == MW_TAP_UNKNOWN);
    assert(field(tap, fetched, MW_FIFO_CONSUMED_SAMPLES) == 576);
    assert(field(tap, fetched, MW_PREPARED_PTS) == 52224);
    for (unsigned int n = MW_TIME_BASE_NUM; n <= MW_CODEC_FRAME_SIZE; ++n) {
        assert(field(tap, a, n) == 0 && field(tap, fetched, n) == 0);
    }
    assert(field(tap, a, MW_PLANE0_OFFSET) == 0 && field(tap, a, MW_PLANE1_OFFSET) == 0);
    assert(field(tap, fetched, MW_PLANE0_OFFSET) == 0 && field(tap, fetched, MW_PLANE1_OFFSET) == 0);
    assert(field(tap, b, MW_FIFO_CONSUMED_SAMPLES) == 576);
    assert(field(tap, b, MW_PREPARED_NB_SAMPLES) == 1024);
    assert(field(tap, b, MW_PLANE1_OFFSET) == 4096);
    assert(field(tap, b, MW_TIME_BASE_NUM) == 1 && field(tap, b, MW_TIME_BASE_DEN) == 48000);
    assert(field(tap, b, MW_CODEC_FRAME_SIZE) == 1024);
    assert(field(tap, b, MW_CODEC_CAPABILITIES) == (uint64_t) (unsigned int) f.codec->codec->capabilities);
    assert(memcmp(plane0(tap,a), plane0(tap,fetched), 576u*8u) == 0);
    const uint8_t *l = plane0(tap,b), *r = plane1(tap,b);
    for (int n=0;n<576;++n) is_float(l+(size_t)n*4u,left_marker(n));
    for (int n=0;n<448;++n) is_float(l+(size_t)(576+n)*4u,right_marker(n));
    for (int n=0;n<128;++n) is_float(r+(size_t)n*4u,right_marker(448+n));
    is_zero(r+128u*4u,896u*4u);
    memset(f.backing,0x5a,f.capacity);
    is_float(l,left_marker(0)); is_float(r,right_marker(448));
    mw_tap_finalize_header(tap);
    assert(memcmp(tap->data,"MWATAP01",8)==0 && mw_tap_get_u64(tap->data+8)==1);
    assert(mw_tap_get_u64(tap->data+16)==22 && mw_tap_get_u64(tap->data+40)==0);
    free(tap); destroy(&f);
}
static void test_small(int align)
{
    fixture f=make_fixture(3,5,AV_SAMPLE_FMT_FLTP,1,align);
    mw_audio_tap *tap=mw_tap_new_memory(); assert(tap);
    f.frame->pts=700000;
    size_t b=observe_send(tap,&f,3); assert(b && tap->errors==0);
    assert(field(tap,b,MW_PREPARED_PTS)==700000 && field(tap,b,MW_PLANE0_BYTES)==20);
    const uint8_t *l=plane0(tap,b),*r=plane1(tap,b);
    is_float(l,left_marker(0));is_float(l+4,left_marker(1));is_float(l+8,left_marker(2));
    is_float(l+12,right_marker(0));is_float(l+16,right_marker(1));
    assert(field(tap,b,MW_PLANE1_OFFSET)==(align==0?128u:20u));
    if(align==0)is_zero(r,20);else{is_float(r,right_marker(2));is_zero(r+4,16);}
    free(tap);destroy(&f);
}
static void test_padded_and_drain(void)
{
    fixture f=make_fixture(576,1024,AV_SAMPLE_FMT_FLTP,0,0);
    mw_audio_tap *tap=mw_tap_new_memory(); assert(tap);
    size_t b=observe_send(tap,&f,576);
    for(int n=0;n<576;++n){is_float(plane0(tap,b)+(size_t)n*4u,left_marker(n));is_float(plane1(tap,b)+(size_t)n*4u,right_marker(n));}
    is_zero(plane0(tap,b)+576u*4u,448u*4u);is_zero(plane1(tap,b)+576u*4u,448u*4u);
    unsigned int before=null_sends;
    f.frame->pts=53248;
    size_t d=observe_send(tap,&f,0);
    assert(field(tap,d,MW_KIND)=='D' && field(tap,d,MW_FIFO_CONSUMED_SAMPLES)==0);
    assert(field(tap,d,MW_PREPARED_NB_SAMPLES)==1024 && field(tap,d,MW_PREPARED_PTS)==53248);
    assert(field(tap,d,MW_PLANE0_BYTES)==0 && field(tap,d,MW_PLANE1_BYTES)==0 && null_sends==before+1);
    free(tap);destroy(&f);
}
static void test_packed(void)
{
    fixture f=make_fixture(576,1024,AV_SAMPLE_FMT_FLT,0,0);
    mw_audio_tap *tap=mw_tap_new_memory();assert(tap);
    size_t b=observe_send(tap,&f,576);
    assert(b && tap->errors==0 && field(tap,b,MW_LAYOUT)==MW_PACKED);
    assert(field(tap,b,MW_PLANE0_BYTES)==8192 && field(tap,b,MW_PLANE1_BYTES)==0);
    assert(memcmp(plane0(tap,b),f.packed,576u*8u)==0);
    is_zero(plane0(tap,b)+576u*8u,448u*8u); free(tap);destroy(&f);
}
static void test_negatives(void)
{
    fixture f=make_fixture(576,1024,AV_SAMPLE_FMT_FLTP,0,0);
    mw_audio_tap *tap=mw_tap_new_memory();assert(tap);
    uint8_t **planes=f.frame->extended_data;f.frame->extended_data=NULL;
    size_t r=mw_tap_frame(tap,f.codec,f.frame,576,AVERROR(EINVAL),f.backing,f.capacity);
    f.frame->extended_data=planes;
    assert(r && tap->errors==1 && field(tap,r,MW_PLANE0_BYTES)==0);
    r=mw_tap_frame(tap,f.codec,f.frame,576,f.fill_rc,f.backing,f.capacity-1);
    assert(r && tap->errors==2 && field(tap,r,MW_PLANE1_BYTES)==0);
    r=mw_tap_packed(tap,'F',f.packed,576,2,48000,4,5,1,-1,52224,576u*8u,8);
    assert(r && tap->errors==3 && field(tap,r,MW_FIFO_CONSUMED_SAMPLES)==MW_TAP_UNKNOWN);
    r=mw_tap_frame(tap,f.codec,f.frame,1025,f.fill_rc,f.backing,f.capacity);
    assert(r && tap->errors==4 && field(tap,r,MW_PLANE0_BYTES)==0);
    uint64_t w[MW_TAP_WORDS]={0};
    assert(mw_tap_record(tap,w,f.backing,SIZE_MAX,NULL,0)==0 && tap->errors==5);
    tap->used=MW_TAP_CAPACITY-8;
    assert(mw_tap_record(tap,w,NULL,0,NULL,0)==0 && tap->errors==6);
    free(tap);destroy(&f);
}

/* Optional private constructor files let Python independently decode C bytes.
 * They are deliberately marked as constructors by the caller's test receipt.
 */
static void emit_constructor(const char *directory, int compact)
{
    char path[4096];
    int n=snprintf(path,sizeof(path),"%s/%s.trace",directory,compact?"compact":"padded");
    assert(n>0 && (size_t)n<sizeof(path));
    mw_audio_tap *tap=mw_tap_open(path);assert(tap);
    struct stat st;assert(fstat(tap->fd,&st)==0 && (st.st_mode&077)==0);
    assert(mw_tap_open(path)==NULL); /* Existing capture cannot be overwritten. */
    fixture f=make_fixture(576,1024,AV_SAMPLE_FMT_FLTP,compact,0);
    f.frame->pts=0;
    assert(mw_tap_packed(tap,'A',f.packed,576,2,48000,4,5,1,0,AV_NOPTS_VALUE,MW_TAP_UNKNOWN,MW_TAP_UNKNOWN));
    assert(mw_tap_packed(tap,'F',f.packed,576,2,48000,4,5,1,-1,0,576u*8u,0));
    assert(observe_send(tap,&f,576));
    f.frame->pts=1024;assert(observe_send(tap,&f,0));
    assert(mw_tap_finish(&tap)==0 && tap==NULL);
    assert(mw_tap_finish(&tap)==0);
    destroy(&f);
}
int main(int argc,char **argv)
{
    assert(argc==1 || argc==2);
    test_compact();test_small(0);test_small(1);test_padded_and_drain();test_packed();test_negatives();
    if(argc==2){emit_constructor(argv[1],0);emit_constructor(argv[1],1);}
    puts("audio tap constructors: PASS; codec is not opened; native production NOT_RUN");
    return 0;
}
