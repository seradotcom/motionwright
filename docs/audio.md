# Audio evidence and editorial workflow

Motionwright treats the source audio file as authority. A voice take enters the project through the desktop/service boundary, not through an agent-authored mutation.

## Measured import

A voice import is content-addressed first. Motionwright verifies the immutable blob SHA-256 and decodes that exact stored file to obtain:

- decoded audio frame count;
- sample rate;
- channel count;
- exact rational duration derived from decoded frames / sample rate.

The asset and measured VoiceTrack are committed atomically against the expected project generation/revision. Importing another take appends another immutable asset/track and changes the active selection; it does not replace prior bytes or prior takes.

Supported decode formats are determined by the pinned Symphonia dependency configured with its full format/codec feature set. Invalid, undecodable, changing-layout or empty streams fail closed.

## Transcript and cues

Transcript intervals use rational time and stable IDs. A manual edit records manual alignment evidence. Measured alignment may be stored only when its engine/source digest/confidence is supplied by a qualified alignment path. Unknown remains a first-class state.

Cues also use stable IDs, rational timestamps and explicit evidence. A transcript segment cannot be removed while a cue references it.

## Mix and metering

Voice/music gains and optional mastering targets are versioned intent. They are not measured output evidence.

Source loudness and true peak fields stay UNKNOWN (null) until a qualified, pinned measurement implementation populates them. Motionwright never substitutes sample peak, metadata or a target value for a true-peak measurement.

## Lazy waveform proxy

Waveform display is derived lazily from the immutable, digest-verified voice source. Motionwright decodes the source as a stream and writes a compact binary proxy one window at a time instead of retaining the whole decoded audio or the whole waveform in memory.

The current proxy contract is versioned as `sample-peak-max-abs-v1`:

- one peak represents 2,048 decoded frames across all channels;
- each peak is the maximum absolute sample in that window, quantized to an unsigned 16-bit value;
- the proxy header binds source SHA-256, sample rate, channel count, total decoded frames and peak count;
- page reads are bounded to at most 2,048 peaks; the Studio currently asks for 192 at a time;
- a cache fingerprint includes the proxy algorithm version, source digest and frames-per-peak;
- the proxy uses the verified derived-cache layer and is regenerated on a cache miss.

A sample-peak proxy is a navigation visualization. It is **not** LUFS, inter-sample true peak, mastering compliance or output evidence. Those measurements remain UNKNOWN unless a qualified measurement path supplies them.

The desktop command performs proxy generation through a blocking worker rather than the Tauri/UI thread. Browser demo mode returns no waveform page and never synthesizes source bytes.

## Native SDK authority

The Semwright Native SDK can select an existing measured take and edit transcript segments, cues and mix intent through CAS operations. It cannot mint VoiceTrack measurement evidence or bypass byte-bound import.
