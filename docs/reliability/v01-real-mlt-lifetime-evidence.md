# v0.1 — real MLT provider project lifetime reconciliation in native CI

**Scope:** Verify the previously implemented local MLT lifetime inventory against a *real* source-bound Broker/Driver Host production, not just synthetic SQLite receipts. This does not implement project.close, foreground destructive approval, runtime leak prevention, or installed-owner release acceptance.

## Real test path

The existing 33-scene / two-segment native production test is already pinned to the exact Semwright SDK, owner Broker, provider descriptor/manifest, driver executable hashes and 32+1 decoded frame clock. This change reopens that test's actual Motionwright SQLite database after native MLT timeline assembly, reconstructs every persisted production receipt under an exact generation/revision and fixed receipt watermark, and requires the MLT inventory to observe between 1 and 4 canonical completed project.create operations under the current project revision.

The inspected inventory is tied to real receipt bytes: a fresh file, native-mlt-lifetime-inventory.json, contains only the non-sensitive aggregate. Its SHA-256 is emitted by the Rust producer, checked again by the independent Python harness and checked once more in GitHub Actions alongside real artifact SHA-256. The test runs in all three CI modes: PCM lossless intermediate, video-only lossless intermediate and final H.264/AAC master with measured synthetic CI voice.

The runner rejects zero native creates, unbounded project counts, an unresolved or malformed creation request, any close request, a mismatched project/generation/revision, a digest mismatch, a partial source receipt snapshot or false claims of live provider state, owner consent, completed native cleanup and permission to close projects automatically.

## Negative interpretation

A valid source receipt proves **a creation was acknowledged**, not that the project still exists in memory. A successful FFV1 or final MP4 render does not prove successful native cleanup. The inventory intentionally retains native_provider_liveness_verified=false, foreground_cleanup_consent_obtained=false, verified_native_project_cleanup=false and automatic_destructive_cleanup_allowed=false.

Failure in this new check fails the real native MLT CI job and preserves CI artifacts for debugging. This is production code conformance evidence, **not** the owner-authenticated foreground closure required by [issue #91](https://github.com/seradotcom/motionwright/issues/91). Do not mark any of the 208 v0.1 requirements independently verified or any of the 60 private acceptance cases PASS on this basis.

The next actual closure gate requires interactive Semwright Policy/Broker authorization bound to the exact native project handle and provider generation, followed by verified one-time completion/denial/unknown receipts and independent Linux installed-owner acceptance. No standalone alternate approval mechanism is added.
