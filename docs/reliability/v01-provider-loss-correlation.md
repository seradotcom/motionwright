# Motionwright v0.1 — passive native provider-loss correlation

**Diagnostic scope only.** This does not fix Semwright Driver Host generation loss, provide a native provider restart policy, reconstruct an interrupted render, grant a mutation, or establish installed-owner acceptance.

## Observed failure motivating this work

The original failed attempt of [native AV master run 38301150251](https://github.com/seradotcom/motionwright/actions/runs/38301150251) used the exact Motionwright source and pinned Semwright Native SDK. It acknowledged the Motion Canvas render start, observed an asynchronous status Timeout, and later received Unavailable with an inactive provider generation. The native render outcome remained UNKNOWN and no replacement render.start was attempted.

The failed job's retained private lifecycle artifact recorded 37 passive bounded samples. The daemon was visible in all samples and did not return an exit status before intentional cleanup. The sampled bwrap process count increased during rendering and later decreased; a valid cgroup OOM-kill delta was not available. This supports **correlation**, not the conclusion that a particular sandbox died or that the decrease caused the capability-generation loss.

## Implemented technical report

The existing native AV CI workflow now writes a **redacted numeric-only** report, native-provider-loss-correlation.json, when its lifecycle sampler stops and before it intentionally tears down the Broker. The derivation is bound to the trusted source receipt sampler and its independently constructed status-reconciliation summary.

- Source schema and authority are mandatory; sample count and monotonically increasing time must agree, with at most 720 samples and 64 processes per sample.
- Process roles are accepted only from a closed vocabulary. Individual PIDs/PPIDs, process paths, sockets, arguments, owner/session data, private media, opaque job refs and full process snapshots are excluded.
- The report counts bwrap and named-provider process-role decreases **only between two consecutive complete process-tree samples** with the daemon present. A truncated tree or missing daemon makes that sample pair incomparable; it must not be reported as a child process exit. The report explicitly counts complete samples, comparable pairs and incomparable pairs. When the final sample is incomplete, `bwrap_process_count_final` is `null`, not a fabricated zero. Process count changes, daemon gaps and numeric OOM-kill deltas remain observations **without attributing cause**.
- The correlated outcome is UNKNOWN only for the typed original Timeout-then-Unavailable pattern. An unrelated completed native result receipt never becomes UNKNOWN solely because a sandbox descendant count changed.
- The report explicitly states provider_generation_exit_cause_established=false, specific_provider_process_exit_verified=false, native_job_terminal_result_verified_by_this_report=false, automatic_native_mutation_retry=false, installed_owner_acceptance=NOT_RUN and v01_release_ready=false.

Failures to prepare this optional diagnostic cannot turn a failed native render into success or mask its original error. The Native SDK stays pinned; no new desktop/provider backend is created.

## Verification and blockers

Ten adversarial unit tests reject malformed roles, oversized process samples, bad source authority, forged status classifications, bad ordering and untrusted private-looking metadata. A small dedicated GitHub Actions workflow validates them independently of the heavy pinned AV E2E, which also checks the new report's source hashes and the explicit no-root-cause claims.

A future investigator still needs a source- and generation-bound native Driver Host process/IPC sequence around the failure, plus a reproducible explanation of generation deactivation. The disappearance of child roles is **not** a proven crash mechanism. Issue #49 and the independent v0.1.0 release gate #126 remain open.
