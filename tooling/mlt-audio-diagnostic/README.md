# MLT audio input diagnostic

This diagnostic observes the audio handed to one MLT AAC consumer. It does not
repair MLT, render a master, authorize a native operation, or accept a release.

The supported investigation uses the Ubuntu MLT 7.22.0-1build6 source and FFmpeg
7:6.1.1-3ubuntu5. The original consumer has SHA-256
c1c8850ff049a8775bb3d24c64be9b9365659773ce019902585c056f5be2f910.
The instrumenter refuses any other source, including an already instrumented
copy. The Ubuntu source archive is
7cf4d9573a061d76902c6e12a2f09f5f461e7c697635233df82a63a3fe4d6da6;
the Debian packaging archive is
2a6be20c5450464559bba5ff627295707e1f4921552ffc180c65441e587b7abf.

Source records:
- https://github.com/mltframework/mlt/blob/v7.22.0/src/modules/avformat/consumer_avformat.c
- https://archive.ubuntu.com/ubuntu/pool/universe/m/mlt/mlt_7.22.0-1build6.dsc

## What is observed

The instrument copies bytes at three points in the same avformat consumer:

- **A:** after the original MLT audio read and any speed-related mute, immediately
  before the original FIFO append. Records the MLT frame position.
- **F:** immediately after the original FIFO fetch. Records the used-byte counts
  before and after that fetch and the existing sample counter before its increment.
- **B:** after the original avcodec_fill_audio_frame, before the original
  avcodec_send_frame. Copies the complete declared planes from the actual
  extended_data pointers, then records the result of that original send.
- **D:** the original NULL send used to drain the codec. Auxiliary AVFrame
  nb_samples and PTS are recorded, but D contains zero submitted PCM samples.

The original conversion helper, align=0, frame size, sample counters, send/receive
calls, muxing, and draining remain unchanged. Remapped or multiple-stream audio
is outside this diagnostic. It produces an invalid trace rather than new audio
behavior. Capture errors do not alter the original native operation result.

For a partial FIFO block, the source helper can compact planar channels using
the consumed length while FFmpeg locates planes using the larger declared frame
length. The reader compares the observed planes as they are. It never crops and
repackages the buffer to reconstruct the desired channels.

The labels left/right in the byte report refer to channel indices zero/one.
This trace version does not serialize speaker masks. Physical speaker assignment
must be checked against the actual provider context and the final decoded
media; two channels alone do not establish that assignment.

## Authority, privacy, and operation identity

The instrumented provider must be built, identified, and admitted through the
existing native authority boundary. Its exact output path must be included in
an authentic grant before setting MOTIONWRIGHT_MLT_AUDIO_TAP_PATH. This repository
utility supplies no grant, driver replacement, native-operation recovery API, or
alternative runtime. Preserve the existing SDK pin.

Use a fresh, private diagnostic workspace and a distinct authorized operation.
Never replay an UNKNOWN operation. Inspect its existing state and preserve its
evidence through the responsible coordinator.

The consumer reserves the exact absolute Linux output path with O_EXCL,
O_NOFOLLOW, O_CLOEXEC, and mode 0600. It does not truncate an existing capture,
change the process umask, or choose a fallback path. Ancestor/path confinement
belongs to the existing grant and provider sandbox, not this header. Keep the
parent directory private and stable for the whole operation.

PCM is held in a bounded 2 MiB allocation during processing and dumped once
logically at cleanup. Partial write and EINTR handling advance the same dump;
they do not restart a native job. Native stdout/stderr and the terminal operation
receipt must be preserved. A missing trace, nonzero header error count, invalid
setup, incomplete write, or failed native operation cannot become evidence of
successful physical capture. In particular, the receiving gate must require
MLT_AUDIO_TAP_STATE requested=1 setup=1 finish=0 and validate the complete trace.
A file alone does not establish successful descriptor close or native completion.

Do not upload raw traces or decoded/source PCM as public workflow artifacts.
Use a private authorized artifact channel. If the lane only offers public
artifacts, establish an approved private or encrypted transport first.

## Binary contract

All integer words use little endian u64. Signed values retain two's-complement
representation. UNKNOWN counters use UINT64_MAX; an unobserved signed result
uses INT64_MIN. PCM is IEEE binary32 in the recorded machine's byte order;
the reader admits only little endian float32.

The 64-byte file header is <8s7Q: MWATAP01, version 1, record word count 22,
record count, total bytes, capture error count, endian marker 1, float width 4.
There are at most 256 records and 2 MiB total. No trailing bytes are admitted.

Each record has 22 words, then exactly plane0_bytes and plane1_bytes:

| Word | Field |
| --- | --- |
| 0 | Kind A, F, B, or D |
| 1 | Payload AV sample format |
| 2 | Original MLT format for A/F; AVFrame format for B/D |
| 3 | Channel count |
| 4 | Sample rate |
| 5 | Observed or prepared samples per channel |
| 6 | FIFO consumed samples: UNKNOWN for A, observed count for F/B, zero for D |
| 7 | Prepared PTS, signed; INT64_MIN for A |
| 8 | MLT frame position, signed; -1 outside A |
| 9 | Layout: packed 0, planar 1, no PCM 2 |
| 10–11 | Copied byte lengths of the two planes |
| 12–13 | Actual plane offsets from the backing buffer, without addresses |
| 14–15 | Original fill and send results, signed |
| 16–17 | FIFO used bytes before/after F; UNKNOWN elsewhere |
| 18–19 | Codec time base numerator/denominator |
| 20 | Codec capabilities |
| 21 | Codec frame size |

A/F use packed float32 and record words 18–21 as zero. B copies both full declared
planar buffers, including padding, or the full packed buffer. It does not infer
the second plane from the amount consumed. With consumed=3 and prepared=5,
align=0 produces a 128-byte planar stride; align=1 produces 20 bytes. The actual
consumer retains align=0. With consumed=576 and prepared=1024, the stride is
4096 bytes.

## Selective validation

First run the Python contract suite in the isolated source directory:

    python -m unittest -v test_verify_trace

The test named test_C_constructor_traces_when_explicitly_supplied is skipped
unless MOTIONWRIGHT_TAP_CONSTRUCTOR_DIR identifies fresh C constructor output.
A skipped C test is not a compile or native PASS.

On the selected Linux lane, build the C constructor with the actual FFmpeg
development packages from the admitted dependency closure. MW_DIAG_SOURCE and
MW_DIAG_BUILD must already name the source directory and a private build
directory inside the assigned workspace. Set temporary/cache paths there too.

    cc -std=c11 -D_POSIX_C_SOURCE=200809L -O1 -g \
      -Wall -Wextra -Wpedantic \
      -Werror=implicit-function-declaration \
      -Werror=incompatible-pointer-types -Werror=return-type \
      -Werror=deprecated-declarations \
      -fsanitize=address,undefined -fno-sanitize-recover=all \
      -fno-omit-frame-pointer -I"$MW_DIAG_SOURCE" \
      "$MW_DIAG_SOURCE/test_audio_tap.c" \
      $(pkg-config --cflags --libs libavcodec libavutil) \
      -o "$MW_DIAG_BUILD/test_audio_tap"

Run it once with a fresh private constructor directory as its sole argument.
Record package versions and loaded-library identities. Set ASAN_OPTIONS to
detect_leaks=1:halt_on_error=1 and UBSAN_OPTIONS to
halt_on_error=1:print_stacktrace=1. It creates padded.trace and compact.trace
with synthetic marker samples and tests that an existing output is refused.
These are constructor traces, not native production captures.

Then set MOTIONWRIGHT_TAP_CONSTRUCTOR_DIR to that directory and rerun only the
Python suite. It independently constructs the corresponding WAV, decodes the
C-emitted records, expects PASS for padded planes and FAIL for compact planes.

Generate provider source into a fresh output directory:

    python instrument_consumer.py \
      --consumer "$MW_MLT_SOURCE/src/modules/avformat/consumer_avformat.c" \
      --output-dir "$MW_DIAG_BUILD/instrumented-source"

The output contains the instrumented consumer, header, and source-hash receipt.
Apply those exact source files to the isolated provider build and compile the
actual consumer translation unit and provider. A passing C constructor does
not establish that the complete MLT translation unit builds. Preserve the
unmodified and instrumented source hashes, build options, compiler/linker and
loaded-library closure. Admission of the resulting provider is a separate gate.

## Physical diagnostic

Only after provider admission, run one fresh authorized AAC job with the exact
source WAV and video. The original reproducer uses 33 video frames at 30 fps,
48 kHz stereo PCM, real_time=0, threads=2, libx264, and the AAC consumer. The
observer must not replace it with a PCM consumer or change its codec settings.

Read the trace with externally authenticated source/capture hashes:

    python verify_trace.py \
      --wav "$MW_SOURCE_WAV" --wav-sha256 "$MW_SOURCE_WAV_SHA256" \
      --trace "$MW_NATIVE_TRACE" --trace-sha256 "$MW_NATIVE_TRACE_SHA256" \
      --native-video-frames 33

The CLI requires the native 30 fps contract: A positions 0..N-1, 1600 samples
per video frame, 1024-sample AAC frames, matching source duration, stable codec
metadata, sequential F/B PTS, one final D, and complete FIFO consumption.
The Python API can inspect constructor traces without those native-profile
assertions and explicitly reports native_frame_contract=NOT_CHECKED.

The reader compares the entire WAV float conversion to A, entire A to F, and
each F channel to the valid prefix of its corresponding B plane. Padding is
measured separately. It reports consumed samples and total prepared samples
separately. Exit 0 means those diagnostic assertions pass, exit 1 means content
differs, and exit 2 means invalid or incomplete diagnostic input.

Even exit 0 does not accept AAC priming/padding, final duration, synchronization,
decoded master content, editability, authority, or release. Independently decode
the entire final master and compare its in-program audio and all video frames.
Record tail samples outside the original source separately from samples lost
inside it. Preserve all failing captures and receipts without relabeling them.
