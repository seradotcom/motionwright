# Durable local change receipts

A local change receipt records a committed SQLite transaction. Reading that receipt requires the same project resource and generation as the currently stored project. Its retained result describes the original transaction; asset availability is checked separately.

## Transaction ordering

`Store::apply`, also used by `StudioService::apply`, performs these steps inside one immediate SQLite transaction:

1. Load and validate the current project document.
2. Check the requested resource and project generation.
3. Look up the request key and compare the change digest. A matching receipt returns its original `ApplyOutcome` with `replayed=true`; different input returns `RequestReuse`.
4. For a new request, check the expected project revision.
5. Inspect and verify the source bytes required by the new change.
6. Commit the changed document, journal event and durable receipt together.

The request ID still receives its bounded syntax check before the transaction. Source checks retain the existing content digest, regular-file and path validation rules. The schema, serialized change digest and public API remain unchanged.

## Repeating an acknowledged local change

A client can lose the response to a successful transaction. Repeating its request key, change and original stamp retrieves the committed result even after another edit advances the project revision. The resource and generation must still match.

For example, a request issued at revision 0 commits revision 1. A later request commits revision 2. Repeating the first request returns its revision-1 result and leaves the stored project at revision 2. It does not append another event or replace the current head.

A request key cannot override a different resource or generation. These requests return `GenerationConflict` before the stored result or a source-file error is exposed. A new request with an old revision returns `Conflict`; a matching request already committed in the same resource and generation remains repeatable.

These checks protect the integrity of the application's CAS scope. Clients must still enter through their trusted session and authorization boundary; possession of a request key is not authentication or a Broker effect grant.

## Missing source bytes after a commit

A source asset may become unavailable after its change was committed. The existing receipt remains readable without reopening or recreating that asset. Blob reads and asset-integrity checks continue to report the missing source, and subsequent new changes still require valid bytes.

This distinction lets a client determine that its original transaction completed while separately repairing an unavailable asset. Replaying a receipt does not claim that an asset was recovered, authorize a new native effect or permit repetition of an operation whose external outcome remains unknown.

An absent or invalid current project document prevents receipt replay: the service must establish the current resource and generation before returning a retained result.

## Regression coverage

`crates/service/tests/cas_replay_authority.rs` exercises the public service against real SQLite stores and independent reopenings:

- Existing receipts reject a foreign project resource or generation without changing the project or journal.
- A valid replay with an old revision returns the original result while preserving a newer project head.
- Reusing a key with changed input still returns `RequestReuse`.
- A committed asset receipt remains readable after its stored blob is removed; the file remains absent and a real blob read still returns `BlobMissing`.
- New asset changes reject foreign scope or a stale revision before inspecting a missing blob. An authorized change at the current revision still fails on the missing source.

Run the focused regression with:

```console
cargo test -p motionwright-service --test cas_replay_authority
```

The existing Project Portability workflow also runs all storage and service targets, formatting, source policy and strict Clippy checks. Native SDK and desktop consumers retain the same API and error variants.
