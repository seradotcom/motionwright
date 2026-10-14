# Motionwright v0.1 — read-only Broker provider lifecycle replay

**Scope:** Investigation of intermittent Motion Canvas provider generation
loss (#49, upstream Semwright #257) with the exact pinned Semwright source
04142a2ae16e53a58bb6cf43135786e396a4b720, version 1.0.0.
This is a CI-only observation, not an installed-owner recovery action.

## Why the original status is insufficient

A real ACK of driver.motion-canvas.render.start has occasionally been
followed by an original-job status Timeout with unknown read outcome, then
Unavailable / "Provider capability generation is inactive". The pinned
core/src/providers.rs ProviderLease.active() has several independent ways
to become false (lifetime cancellation, epoch invalidation, provider closed
token or changed identity), and the same unavailable message can therefore
represent different lifecycle failures. The error string alone cannot prove
a provider process exited, a crash/OOM occurred or a job completed.

The original Broker also has a session-authorized read-only event stream,
exposed as the pinned CLI's watch --after 0 command. Historical provider
events may include provider.connected, provider.disconnected,
provider.capabilities.changed, provider.generation.invalidated and
provider.removed, with native generation and catalog revision metadata.
These events can show an *observed* lifecycle transition, not its actual
root cause or a terminal media result. No client is permitted to infer
render success, ask for a new effect grant or retry the original mutation
based on this observation.

## Implementation

- tooling/native_provider_broker_events.py performs exactly one bounded
  read-only CLI watch subscription with the already provisioned owner
  socket/session. It uses the same pinned Semwright binary already built
  for the original native AV master E2E.
- The watcher captures at most 2.5 seconds of replay and admits only
  bounded strict UTF-8 JSON lines with increasing Broker sequence, exact
  source semwright-core, exact provider driver:motion-canvas, and one of
  five named lifecycle event kinds.
- It discards untrusted payloads, arbitrary attributes, original session
  tickets, owner/project paths, native job references, timestamps, stdout,
  stderr and all other provider/job events. Only kind, native generation
  and catalog revision survive. Malformed, oversized, duplicated or
  forged provenance returns UNVERIFIED rather than a filtered success.
- Only the baseline v0.1 Native AV CI workflow enables the diagnostic
  using MOTIONWRIGHT_V01_PROVIDER_EVENT_REPLAY=1. The shared Python harness
  deliberately does not run it for v0.5 fixtures and does not modify
  original output media, rendering or source.
- A bounded local private native-provider-broker-events.json is written
  **before deliberate daemon teardown** in the disposable CI runner.
  It is not included in the allowlisted public AV artifact. GitHub CI
  text logs only the fixed observation category and per-allowlisted-kind
  counts, never provider generations, payloads or native job selectors.
- This also removes the original AV owner-socket startup assertion path
  that embedded raw semwrightd.log output in GitHub exception text.
  Unexpected startup still fails closed without pasting private logs.

On a successful baseline AV run, exact-head GitHub CI checks the structured
receipt's source SHA, Semwright pin, read-only/no-retry/no-recovery claims
and allowlisted fields. A missing event, unavailable watch subscription
or observed provider.connected event does **not** prove lifecycle health.
On a failed run, this optional forensic work never replaces the
original native error or upgrades UNKNOWN to verified pixels.

## Evidence, tests and limitations

Adversarial Python fixtures validate exact same-provider event provenance,
typed event kinds, ordering, parser duplicates, untrusted payload exclusion,
unknown event streams, bounded size/deadline, CLI invocation identity,
read-only subscription, timeout and source-controlled CI opt-in. No local
native renderer or direct Broker call is necessary for synthetic tests.
A full pinned GH native AV test is required before integration.

An intermittent cause may not recur during the next run. Even if the
Broker reports provider.disconnected, the event does not prove whether
the provider exited due to memory pressure, an expected sandbox shutdown,
an event-buffer loss, a catalog refresh or an operator action. A clean
run is a baseline, not evidence that upstream #257 is fixed.

v0.1 remains BLOCKED_PENDING_EXTERNAL_EVIDENCE for independent
208-requirement review, 60 owner acceptance scenarios, signing, native
foreground MLT project.close, the glib advisory and actual provider
generation loss correction. None of these gates can be marked PASS by
this diagnostic.
