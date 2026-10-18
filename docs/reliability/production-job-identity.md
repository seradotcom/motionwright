# Production-job projection identity

Motionwright reconstructs visible job state from the append-only production receipt stream. Native SDK and Broker remain the only effect authority; a job projection never authorizes dispatch, cancellation, cleanup or retry.

## Identity

Recognized native render producers are Motion Canvas, MLT Video and Manim Community. Jobs are keyed by provider, project generation, project revision and native job reference. A colliding job reference from another provider, generation or revision cannot merge progress, cancellation, or terminal results.

For Motion Canvas and Manim, a completed render-start receipt must establish the reference within the exact project generation/revision. A status or result without that original start remains observable as outcome unknown, not successful. Duplicate acknowledged starts under the same key are ambiguous. Manim starts require matching SDK provider provenance and an explicit provider-generation value.

Status, cancel and result observations must return the exact requested job reference. When the original start recorded provider generation, later completed observations must retain it. Foreign, missing or ambiguous observations remain visible but cannot promote their artifact, progress, cancellation or result. A known successful terminal result is not replaced by a late running status.

MLT retains its historical observation policy; this change isolates its key without promoting legacy MLT receipts to new upstream authority.

## Receipt arrival order

The unique acknowledged start anchors the original request and creation metadata even when a status, cancellation or result receipt was persisted first. Until that start is present, the observation remains outcome unknown and cannot establish successful lineage.

A delayed start acknowledgement is admission evidence, not a fresh status observation. Its initial queued/running state and progress do not replace state or progress already observed for the original job. A valid terminal acknowledgement can establish a terminal result through the existing terminal-state rule. A known terminal state remains stable.

An uncertain start with no intervening job observation can still be reconciled by its own later known acknowledgement. These rules preserve the original receipt rows and do not dispatch or retry the effect.

## Recovery

Projection is read-only. It never starts a render, renews a grant, replays UNKNOWN or infers user authorization. The bounded latest-window Studio API and watermark-checked complete Native SDK snapshot remain separate. Historical observations retain their old version scope after project edits/imports. A failed_known read does not prove an earlier mutation was cancelled.

## Regression

Run these source-bound tests in an authorized isolated runner:

    cargo test --locked -p motionwright-service --test production_jobs_identity
    cargo test --locked -p motionwright-service --lib jobs::tests
    cargo test --locked -p motionwright-storage -p motionwright-service --all-targets

The integration suite writes original dispatch/completion pairs through StudioService into real SQLite and checks reopen, provider collisions, foreign returned references, provider-generation drift, and delayed original acknowledgement after active or terminal observations. The existing in-module lifecycle tests cover cancellation requests, provider loss and late observations.

This suite does not execute a real Manim renderer, prove video frames, authenticate a user, or reproduce the historical generation-loss cause of issue #49. Physical native and installed runtime validation remain separate gates.
