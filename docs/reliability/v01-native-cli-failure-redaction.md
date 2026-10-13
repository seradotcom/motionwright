# Motionwright v0.1 — canonical CI-only native JSON failure redaction

**Scope: v0.1 native integration and CI data security.** This does not implement new Semwright authority, issue native cleanup commands, repair Driver Host generation loss, validate real owner installation, or authorize release 0.1.0.

## Existing gap

The AV master harness was previously hardened under issue #49, but the independent baseline Motion Canvas, Motion Canvas-to-MLT lossless pipeline and Native SDK shared-host lifecycle scripts still formatted command argv and up to 4–8 KiB of raw stdout and stderr into CI exceptions. A failed pinned Semwright invocation may contain an opaque native job reference, session file path, connection or untrusted provider text. Those are not publishable diagnostics.

## Implemented v0.1 guard

The same closed diagnostic contract implemented in the existing native AV failure guard is now the single shared Python run_sanitized_json function used by:

- tooling/native_av_master_e2e.py — baseline Motion Canvas-to-MLT H.264/AAC acceptance;
- tooling/native_render_e2e.py — 60-frame actual Motion Canvas direct/mediated render;
- tooling/native_mlt_sequence_e2e.py — 33-frame native MLT timeline in PCM, video-only and final MP4 modes;
- tooling/native_host_shared_state.py — Native SDK shared-host source/connection integrity tests.

The CLI arguments, execution permissions, timeouts and successful JSON payloads are unchanged. If a child process fails, times out, cannot spawn, emits malformed JSON, or returns a top-level JSON value other than an object, the function fails closed and raises a bounded NativeCommandFailure with no raw argv, stderr, stdout, private media names, native job IDs, connection tokens or session paths.

The failure includes only constant diagnostic reason, allowlisted error code, source-outcome classification and limited status-event signals where a bounded typed envelope is available. An UNKNOWN render after an acknowledged mutation is not retried and is never treated as a successful native job or verified media output. The caller may retain separate source/receipt evidence in protected CI artifacts according to the original workflow, but exceptions do not print the original untrusted text.

This guards the v0.1 baseline workflows; additional experimental v0.5-specific Blender/Manim wrappers remain outside this bounded PR and require a separate future review rather than silent modifications to other agents' work.

## Verification

tooling/tests/test_native_cli_failure_redaction.py runs adversarial secret-shaped failures, malformed successes, timeouts, OS spawn failures, exact single-invocation/no-auto-retry accounting, and unchanged valid success against all four wrappers. Existing test_native_av_failure_guard.py remains green. These lightweight tests are run in the dedicated v0.1 GitHub Actions workflow and before the respective heavy Native SDK, Motion Canvas and MLT production jobs.

A successful negative redaction test is not evidence of any of the 60 private product acceptance cases, and does not clear issues #49, #91, #89 or #126.
