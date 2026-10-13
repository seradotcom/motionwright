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
- The correlated outcome is UNKNOWN only when the original *acknowledged native render job* has a correctly staged `Timeout` (`outcome_unknown`, `outcome_known=false`) followed by `Unavailable` (`failed_known`, `outcome_known=true`) for **that same opaque job**. A Timeout from job A and Unavailable from job B never establish a generation-loss sequence. The diagnostic retains native job identifiers only in memory and exports a non-identifying boolean, `same_acknowledged_job_status_chain_verified`. Unknown/malformed timestamps and nonmatching status stages do not establish that sequence. An unrelated completed native result receipt never becomes evidence that an UNKNOWN original job succeeded.
- The report explicitly states provider_generation_exit_cause_established=false, specific_provider_process_exit_verified=false, native_job_terminal_result_verified_by_this_report=false, automatic_native_mutation_retry=false, installed_owner_acceptance=NOT_RUN and v01_release_ready=false.

Failures to prepare this optional diagnostic cannot turn a failed native render into success or mask its original error. The Native SDK stays pinned; no new desktop/provider backend is created.

## Verification and blockers

The dedicated GitHub Actions workflow validates both the lifecycle and correlation suites independently of the heavy pinned AV E2E. New adversarial cases cover interleaved jobs, unacknowledged starts, inconsistent stage/outcome fields, a foreign result identity inside an otherwise matching receipt, missing same-job proof and confidential values in error payloads. Positive synthetic matching-job cases and real existing native AV integration must still pass; a synthetic green check is not a reproduction of the original intermittent upstream fault.

A future investigator still needs a source- and generation-bound native Driver Host process/IPC sequence around the failure, plus a reproducible explanation of generation deactivation. The disappearance of child roles is **not** a proven crash mechanism. Issue #49 and the independent v0.1.0 release gate #126 remain open.
