# Resource limits for local file imports

Local asset and measured-voice imports use a finite resource policy owned by
the native host. This implements the input-size, duration and decode-work
part of the v0.1 security boundary (CR-SEC-02). The numerical ceilings below
are product policy, not performance measurements or acceptance thresholds.

## Policy and covered operations

Every existing StudioService constructor selects ImportLimits::default().
A native caller can construct a stricter instance with
open_with_import_limits or from_store_with_import_limits. ImportLimits has
private fields, no deserialization implementation and no environment-variable
configuration. Its builders accept only positive values no larger than the
current value. A service has no policy setter; cloning it preserves its policy.
The policy is absent from import intents, request digests and WebView requests.

| Resource | Default ceiling | Enforcement purpose |
| --- | ---: | --- |
| Supplied source file | 268,435,456 bytes (256 MiB) | Bound capture and source verification for a known replay. |
| Measured audio duration | 3,600 seconds | Bound the selected audio track using exact rational comparisons. |
| Decoded samples, all channels | 345,600,000 | Bound cumulative measurement work independently of duration. |
| Demuxed packets, all tracks | 1,000,000 | Include packets that are discarded because their track is not selected. |
| Encoded bytes in one packet | 16,777,216 bytes (16 MiB) | Reject an oversized packet before passing it to the decoder. |
| Decoded samples in one packet | 1,048,576 | Bound each returned block and validate available codec declarations. |
| Cumulative audio source reads | 2,147,483,648 bytes (2 GiB) | Charge rereads after seeks as well as initial reads. |

These limits apply jointly. The sample ceiling corresponds to one hour at
48 kHz with two channels, but this does not promise that every one-hour file
is admissible. For example, its uncompressed 16-bit PCM container exceeds the
source-byte ceiling. Higher rates or channel counts can reach a sample limit
before the duration limit. Existing supported format checks remain 8,000 to
384,000 Hz, one to 32 channels, and stable format during measurement.

New declared SVG imports retain the existing 8 MiB ceiling, further reduced
when the host selects a smaller general source limit. Decoded SVG admission
still examines the exact staged bytes. A known historical replay only verifies
the supplied bytes against its receipt; it does not rerun current content or
SVG admission rules.

## Capture, replay and publication

The service checks project authority, import intent and request identity before
source I/O. New requests use the existing bounded private capture primitive.
That primitive validates and retains a regular source handle, checks its size
and counts actual streaming bytes. It may read one extra byte to detect growth,
but rejects excess before writing those bytes to its private capture. Failure
drops only that capture. It never removes an unrelated staged file or a
previously published CAS object.

A known replay uses the same finite source-byte policy through a bounded
retained-handle hash. This path performs no staging, content scanner, decode or
CAS repair. Its second receipt read still checks that the previously observed
result has not changed during source verification.

A host can reduce its policy after a file was committed. The normal replay
can then reject a new verification of an oversized supplied file without
invalidating the original receipt. recover_file_import remains an explicit
read of the historical committed result, independent of source files, CAS
contents and audio budgets. Neither absence nor a policy error authorizes
repeating an operation whose outcome is UNKNOWN.

For a new voice import, all measurement limits run against the private capture
before CAS publication and before the project/event/receipt transaction. A
budget rejection leaves the canonical project, events and receipts unchanged
and does not publish the rejected source. An empty staging directory may
remain; this is distinct from a leaked capture.

## Audio accounting

Measurement checks available selected-track and codec declarations before
constructing the decoder. Unknown declarations do not count as proof that a
file is small. Duration comparisons use integer arithmetic; a fraction of a
second above the limit is not rounded down.

The loop counts packets and their encoded bytes before filtering by track.
It checks actual decoded frames, channels, per-packet samples, cumulative
samples and duration before continuing. Counting samples includes every
channel, rather than treating stereo as mono.

The audio source reader charges all successful reads, including rereads after
a seek. One additional physical byte may be read to distinguish exact-budget
EOF from excess. Exhaustion is sticky: a seek cannot reset it and subsequent
reads cannot start a second budget. The caller checks the shared exhaustion
signal after probing and after each demux operation, including error and EOF
returns, so a demuxer cannot turn budget exhaustion into successful truncation.

Source-byte violations retain StorageError::BlobTooLarge with observed size
and limit. Audio budget violations use the existing domain-invalid error
contract with a named budget dimension. Operational I/O errors retain their
normal error path when no budget was exceeded.

## Practical limits

Checks execute between library calls. An individual probe, demux or decode
call can allocate or spend CPU before it returns; packet bytes are already
allocated by the demuxer when the consumer inspects them, and decoded samples
already exist when their count is checked. This policy does not provide
preemptive process isolation, a hard RAM cap or a wall-time deadline. An audio
read budget also cannot count work that performs no source read.

The covered entrypoints are import_asset_file and import_voice_file, plus
source verification for their known replays. This policy does not redefine
lower-level blob ingestion, bundle import, backup, waveform generation or
original-mix audition. Those operations retain their own contracts and
require their own applicable evidence.

## Regression evidence

The causal comparison uses the same real StudioService operations and small
fixtures on both revisions. Its baseline introduces the host-policy type and
constructors without connecting enforcement; a missing API or compilation
failure is not an expected RED result. The repair connects the same policy
to capture, known-source hashing and measured audio.

Controls cover exact byte and duration limits, one-byte or one-frame excess,
stereo sample accounting, packet and source-read budgets, and known replay
versus source-free recovery after reopening. A real container with multiple tracks must
establish the selected-track packet roster before crediting coverage of
discarded packets. Retained-handle growth is exercised deterministically
after admission, without a timed race or a FIFO baseline.

Evidence must name the executed source SHA, actual nonzero test roster and
original assertion results. Source formatting and policy checks alone do not
establish that these Rust regressions passed.
