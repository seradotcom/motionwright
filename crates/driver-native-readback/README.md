# Native original-mix readback driver

This crate supplies a concrete Driver SDK 54d handler for inspecting an already
produced canonical original-mix WAV and its original MP4. It invokes the existing
`tooling/native_mlt_mix_readback.py::inspect_master` exactly once through a
Host-owned job. It neither creates a mix nor invokes MLT, Motion Canvas or an
alternative media runtime.

The implementation is a source proposal until its registration, runtime profile,
native integration and platform lane are admitted. Unit tests are not evidence
of an executed Driver Host, decoded product master or export approval.

## Entry and authority

The only command is `driver.native-readback.original_mix.inspect`, under provider
`driver:native-readback`. Its closed request carries backend-derived identities,
source/receipt digests, a bounded exact clock and the owner installation pins.
It has no media path, executable, argv, adjustable threshold, approval or native
job field. Unknown fields are rejected. Direct `Driver::execute` without the
existing SDK's private Host context is denied, even with a correct descriptor
digest and fabricated PASS JSON.

`OwnerReadbackInstallation::manifest` returns a proposed manifest; it does not
sign, install, authorize or start it. Existing owner policy must grant the driver
scope and these named roots:

| Logical root | Required owner binding | Manifest access |
| --- | --- | --- |
| `native-readback-media` | The original native output directory containing the exact WAV/MP4 | Read only, no execute |
| `native-readback-runtime` | A reviewed runtime closure required by the sealed tools | Read only, execute for native libraries |
| `native-readback-python-executable` | Exact Python executable, SHA-256 pinned | Host seals executable |
| `native-readback-ffmpeg-executable` | Exact ffmpeg executable, SHA-256 pinned | Host seals executable |
| `native-readback-ffprobe-executable` | Exact ffprobe executable, SHA-256 pinned | Host seals executable |

The Python tool can see only the two declared mounts and the ffmpeg/ffprobe
dependencies. There is no network, secret, system-config or write mount. A runtime
directory's presence and an executable digest do not establish Python stdlib/DLL
closure, bootstrap compatibility or isolation. The owner profile and the real
platform lane must establish those facts. No `PYTHONHOME`, shell or unsandboxed
fallback is added here. SDK54d runtime tools are supported through the existing
Linux/Windows Host implementations; a macOS native PASS is not claimed.

## Fixed execution

The Host resolves all path-bearing arguments with SDK `MountPath` or `ToolPath`.
Only mount roots are used, as required by SDK54d. Canonical native producer names
are derived from the backend identity, source-plan/WAV SHA and original mux
request ID; no nested or caller-selected path is admitted.

Python receives `-I -B -` and a fixed program on stdin. The program contains the
compiled adapter plus a base64 JSON packet. The packet embeds the sole existing
reader with SHA-256
`5c66976b10357c7e97294e62bccb6844fad4277344ccb7c948e421f2fc267abc`.
Rust verifies this exact source before starting a job, and the adapter verifies
it again before executing it. CRLF changes fail this pin; use the exact Git LF
source in the build and do not silently normalize a different reader.

Request validation, geometry, rational sample-clock checks and the fifteen-second
canonical-mixer budget precede tool/media work. The sole reader retains its
existing thresholds, full logical PCM comparison, AAC stream/profile checks,
frame/packet clock checks, declared padding rules and safe decoder supervision.
The adapter additionally binds the actual sealed tool bytes to the expected
owner pins and checks their final identity. It does not adjust, normalize, align,
pad, resample, repair or relax the comparison.

The program is bounded by the SDK's 64 KiB stdin limit. The Host job deadline is
180 seconds, the driver request deadline is 200 seconds, and the native production
transport needs an explicit 210-second command deadline. The current generic
45-second production deadline is insufficient. The existing SDK wait/cancel/reap
implementation owns supervision; this crate has no scheduler or retry loop.
Byte budgets do not claim bounds on every filesystem syscall or all pathname
races. Native timeouts still require the real Host's process-tree cleanup.

## Output and uncertainty

A successful result contains the exact request-binding SHA, reader/adapter/tool
pins, the acknowledged original Host job, a closed technical summary and explicit
scope. The summary preserves WAV/MP4/PCM digests, all compared sample counts,
packet/frame timing, declared suffix, alignment diagnostic scope, comparison
limits, window counts and the complete canonical reader-report digest/size.

The full window arrays are not sent through stdout: even a short genuine report
can exceed the SDK's 256 KiB output limit. Full-report serialization is capped at
16 MiB, and the returned summary at 32 KiB. Oversize data is rejected; a successful
comparison is never truncated into a different verdict. The result explicitly
sets `complete_reader_report_retained=false`. Retaining a complete report requires
a separately admitted evidence destination; a digest alone is not retained bytes.

Known reader rejection returns a fixed diagnostic. Original Host failures retain
their code, `outcome_known`, progress and native diagnostic. After a real start
acknowledgement, the actual job ID is preserved in an opaque bounded error
candidate. Without an acknowledgement, no job ID is fabricated. There is no
automatic second start, no replacement master and no conversion of UNKNOWN to a
known success or failure. Any later inspection must follow the owner's existing
reconciliation and permission flow.

This observation does not confer listening, rights, loudness/true-peak, pixel
fidelity, editable-source reopen or export approval. The native consumer must
validate the real original receipt and current canonical custody before it can
use the result. JSON possession is not authority.

## Focused tests

From the repository root, after the crate is an admitted workspace member:

```text
cargo test -p motionwright-native-readback-driver --lib -- --nocapture
cargo clippy -p motionwright-native-readback-driver --all-targets -- -D warnings
python -B -m unittest discover -s crates/driver-native-readback/tests -p test_worker.py -v
```

There are nine Rust boundary tests and thirteen Python adapter tests. Synthetic
success data tests only transport and authority boundaries. The Python suite also
runs one isolated Python stdin-program negative to prove a modified reader cannot
execute its source. It does not invoke ffmpeg, ffprobe, Host or a native producer.
The existing sole reader owns real AAC calibration; use that lane and real
canonical product WAV/master artifacts for physical validation.
