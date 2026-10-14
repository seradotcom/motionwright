# Motionwright v0.1 — live, bounded read-only Broker provider watch

Scope: issue #49 only. This is a disposable CI diagnostic for the original
Motion Canvas native-render → MLT H.264/AAC master lane. It does not affect
Studio, other render fixtures, v0.5 features or the Semwright Native SDK pin.

## Problem

The former observer ran **after** the original job finished or failed. If the
Broker daemon died during an acknowledged render, the historical session
replay in `finally` could not connect, losing evidence of an earlier
`provider.disconnected` or `provider.generation.invalidated` transition.
This was especially problematic when `render.status` first timed out and
later returned an inactive capability generation: that failure is real, but
its root cause is not known.

## New evidence

After the pinned CLI has established the original owner session, one
additional **read-only** `watch --after 0` CLI subscriber starts before
`render`. Its stdout is consumed into memory by one low-impact thread,
strictly capped at 256 KiB; overflow is marked `UNVERIFIED`. The observer
is stopped **before** intentional Broker teardown, including on failure.
A Broker crash can therefore leave separately observed and already captured
lifecycle events even when the existing final historical replay becomes
unavailable. This does not depend on repeating native mutations.

Only allowlisted Semwright-core Motion Canvas event
`{kind, generation, revision}` tuples appear in the private
`native-provider-live-broker-events.json`. No raw CLI output, original
job/session IDs, owner paths, tokens, environment, stdout/stderr from the
provider, or daemon logs are copied to the public artifact. CI prints
only the observation category, counts for the five closed-enum provider
transition kinds, and whether the subscriber exited prematurely. Those
safe counts survive a failed/removed Broker runner without exposing raw
session event history or pretending to prove root cause.
The original replay file remains separate and unmodified.

The live report always marks `history_exhaustive=false`,
`native_provider_causation_proven=false`,
`original_render_retry_permitted=false`, and
`terminal_native_result_verified=false`. An observed provider event
does **not** by itself identify a process kill/OOM, lifecycle epoch, or
the outcome of the original job. An event-free stream is not positive
proof of healthy provider lifetime.

## Verification and boundaries

Adversarial unit tests exercise positive/early-exit captured events,
wrong-provider filtering, bounded-overflow refusal, malformed/partial
frames, read/start failures, subscriber termination and no native
mutation. The exact-source GitHub Actions native AV lane runs these tests
and produces a new private live evidence report. Its public upload still
uses the previously established strict media-only publication path.

This is a diagnostic enhancement, not a demonstrated runtime repair.
Issues #49 (provider lifecycle), #91 (MLT foreground authorized close),
#89 (glib) and #126 (private acceptance and release authority) must remain
OPEN until their independent evidence is obtained.
