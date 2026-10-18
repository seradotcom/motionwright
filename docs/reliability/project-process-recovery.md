# Project persistence after process termination

This fixture checks committed project recovery after forced process termination. It creates a scene, two branches with different committed marker content, an imported audio asset, and two independent SQLite clients that submit different AddAsset changes at the same revision. Exactly one request must commit; the other must return a revision conflict.

The process keeps both service connections alive until the wrapper terminates its PID. Before reopening Motionwright, an independent readonly SQLite connection checks the document, eight events, eight receipts, branch state and content-addressed assets. A fresh fixture process then recovers the original import receipt and winning receipt through the readonly service API. The rejected request remains absent and is never resubmitted.

## Build and run

Run in a clean checkout with Rust, Python 3 and Git available. Set `EXPECTED_SOURCE_SHA` to the exact commit selected for this run. Build once and execute the resulting binary directly. Do not use `cargo run` as the supervised process.

### Linux and macOS runners

```bash
set -euo pipefail
source_root="$(pwd -P)"
actual_sha="$(git rev-parse HEAD)"
test "$actual_sha" = "$EXPECTED_SOURCE_SHA"
case_root="$RUNNER_TEMP/project-process-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
build_log="$RUNNER_TEMP/project-process-build-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT.log"
cargo build --locked -p motionwright-service --example project-process-recovery 2>&1 | tee "$build_log"
binary="$source_root/target/debug/examples/project-process-recovery"
binary_sha="$(python -c 'import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$binary")"
python tooling/project_process_recovery.py \
  --source-root "$source_root" \
  --expected-source-sha "$EXPECTED_SOURCE_SHA" \
  --actual-source-sha "$actual_sha" \
  --binary "$binary" \
  --expected-binary-sha256 "$binary_sha" \
  --case-root "$case_root" \
  --run-id "$GITHUB_RUN_ID" \
  --job-name "$GITHUB_JOB" \
  --runner-os "$RUNNER_OS" \
  --timeout-seconds 120
```

The binary path above assumes the default Cargo target directory. If the job sets `CARGO_TARGET_DIR`, use the actual example executable from that directory.

### Windows runners (PowerShell)

```powershell
$sourceRoot = (Get-Location).Path
$actualSha = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actualSha -ne $env:EXPECTED_SOURCE_SHA) { throw 'Source SHA mismatch' }
$caseRoot = Join-Path $env:RUNNER_TEMP "project-process-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT"
$buildLog = Join-Path $env:RUNNER_TEMP "project-process-build-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT.log"
cargo build --locked -p motionwright-service --example project-process-recovery 2>&1 | Tee-Object -FilePath $buildLog
if ($LASTEXITCODE -ne 0) { throw 'Fixture build failed' }
$binary = Join-Path $sourceRoot 'target\debug\examples\project-process-recovery.exe'
$binarySha = (Get-FileHash -Algorithm SHA256 -LiteralPath $binary).Hash.ToLowerInvariant()
$fixtureArguments = @(
  'tooling/project_process_recovery.py',
  '--source-root', $sourceRoot,
  '--expected-source-sha', $env:EXPECTED_SOURCE_SHA,
  '--actual-source-sha', $actualSha,
  '--binary', $binary,
  '--expected-binary-sha256', $binarySha,
  '--case-root', $caseRoot,
  '--run-id', $env:GITHUB_RUN_ID,
  '--job-name', $env:GITHUB_JOB,
  '--runner-os', $env:RUNNER_OS,
  '--timeout-seconds', '120'
)
python @fixtureArguments
if ($LASTEXITCODE -ne 0) { throw 'Process recovery fixture failed' }
```

The case directory must not already exist. A failed or interrupted case is retained for inspection; a later independent run uses a new case directory.

## Evidence and custody

Preserve the build command and complete build log together with the case artifacts. The wrapper checks the expected commit against `git rev-parse HEAD` in `--source-root`, requires a clean tracked checkout and computes the actual executable SHA-256. It repeats the source and binary observations after the case. These observations do not by themselves prove how the binary was compiled. The runner's build command/log and immutable checkout bind the binary to the selected source.

The wrapper resolves initial runner-owned directory aliases once, including macOS temporary-directory aliases. Inside the newly created case, it rejects observed symlinks, Windows reparse points and non-regular files. The binary itself must be an ordinary file.

The wrapper records the actual PID, termination method and exit code. Unix uses SIGKILL; Windows uses TerminateProcess through `Popen.kill`. The READY PID must equal the PID of the directly launched executable.

`evidence/result.json` indexes the process logs, immutable intent/checkpoint hashes, reconstructed source-byte checks and three SQLite snapshots: before termination, immediately after termination, and after readonly service recovery. The pre-termination SQLite reader is closed before the kill. The first post-termination database reader is Python, before any Store or StudioService opener.

All original WAV input files are removed before READY. Three previously published CAS blobs must retain their exact bytes. Only the original asset and winning candidate may be associated with the project. The losing candidate's preexisting blob is retained; its request must have no receipt or project association.

A passing case requires the complete logical SQLite snapshots to remain identical. The original import recovers revision 3 while the current document remains at revision 8. Receipt observation must not create a project, event or receipt.

## Scope

This is a process-termination test. It does not establish power-loss durability, actor authorization, user consent, external Host effect counts, replay retention policy, restored-backup replay behavior, installer signatures or platform signing.

The wrapper bounds process output and supervises pipes using threads and bounded queues. An external job timeout must still bound operating-system or filesystem stalls. Filesystem path checks do not provide a sandbox against an adversarial concurrent filesystem mutator.

A SQLite database file copied without its required journal state is not a project backup. This fixture uses the original isolated store and does not relabel an incomplete file copy as a valid backup.
