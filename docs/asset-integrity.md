# Local asset integrity and honest dependency provenance

Motionwright's **Dependencies** workspace previously displayed an asset beside a scene selected by the asset's list position, even though the project domain did **not** contain that source-to-scene dependency edge. That was not Project Graph evidence. The workspace now shows only consumers supported by the application-owned schema: an explicit VoiceTrack's `asset_id` references a specific asset, displayed as a measured voice consumer. Assets without a known direct reference are labeled **Not established (Graph needed)** rather than randomly associating them with scenes.

The new **Check local assets** command audits *content-addressed source bytes only*. This is deliberately distinct from an authorized Project Graph CURRENT/STALE/UNKNOWN verdict, licensing, source provenance, native renderer output or creative acceptance.

## Read-only trust boundary

The user explicitly chooses **Check local assets** in the desktop app. The Tauri `asset_integrity_page` command requires the exact project ID, generation and revision plus an integer offset and a 1–16-asset page size (the Studio uses 8). No paths, hashes, filenames, driver commands, media URLs, effect grants or arbitrary filesystem read capabilities can be provided by the WebView.

Motionwright's existing versioned `StudioService` and `Store` resolve each asset's saved `content_sha256` to the internal application-owned blob-root/shard/file convention. Checks reject symlinked or non-directory ancestors and non-regular/symlinked final entries. An existing regular file is hashed in **128 KiB chunks** and compared with its recorded SHA-256. Inspection budgets cap each source at **256 MiB** and total unique source hashing per page at **256 MiB**. A shared digest referenced by multiple assets is hashed once per page.

This operation performs no project mutation, import, missing-file repair, output rendering or network access. Large sources that exceed the bounded work budget are marked **deferred_by_budget**, not verified. It never buffers entire imported videos or audio into the WebView. The command uses a blocking worker and checks the creative generation/revision again after the scan. A new creative revision invalidates earlier page results and requires a fresh inspection.

## Result terminology

| Local status | Meaning |
|---|---|
| `verified` | The local file's observed bytes matched the exact referenced SHA-256 |
| `missing` | The digest-bound local file or shard directory was absent |
| `corrupt` | The local byte count or recomputed SHA-256 disagreed |
| `unsafe` | The path involved a symlink/non-regular entry or was replaced during inspection |
| `unreadable` | The operating system refused or interrupted the read |
| `not_content_addressed` | The project has an asset reference but no saved content digest; it cannot be claimed as imported |
| `deferred_by_budget` | The file exists but its size exceeded the per-asset or remaining page hashing budget |

Even a page whose **all references were examined** may contain unverified entries. The complete label means the project asset list has been traversed; it never means all underlying bytes passed verification. SHA-256 results describe a snapshot at inspection time, not a permanent guarantee that local files cannot change.

The existing browser demo has no authority to inspect the owner's filesystem. Its Check local assets control is disabled, and all assets remain **Not checked**. It does not fabricate valid hashes or inferred dependencies.

## Agent read-only Native SDK observation

The pinned Semwright Native SDK now exposes `driver.motionwright.observe` with the application-owned scope `asset-integrity`. Authorized agents can obtain the same per-asset status and byte count returned by the desktop inspector, without choosing paths, passing source hashes, reading media bytes, installing runtime tools or issuing any mutating effect grant. Results include only `asset_id`, `status` and `size_bytes`, not the local owner path, filename, media payload or recorded SHA-256. The caller can correlate an `asset_id` with the versioned project it was already authorized to observe.

Native responses are bounded to **16 assets per page**, even when the SDK query asks for a larger result. Each continuation uses a canonical `assets:v1:<page-limit>:<offset>` cursor associated with the exact SDK resource/generation/revision and scope. Changing the page size, switching observation scope, using a malformed/terminal offset or advancing the creative revision invalidates continuation. File hashing runs in a bounded blocking worker and the project version is checked again before returning the page. `complete: true` means all asset references have been visited, **not** that each blob verified: missing, corrupt and deferred entries remain explicitly recorded.

A filesystem actor who alters source bytes without changing the creative revision can change a later page's integrity result; the cursor therefore represents a stable **asset list**, not a frozen external filesystem snapshot. For delivery decisions, rerun the check close to export time. No Graph CURRENT/STALE verdict or source rights are inferred.

## Evidence and remaining work

- Rust storage tests ingest **real content-addressed files** through the existing import boundary, commit their references to SQLite, verify duplicate-digest handling and pagination, then detect missing/modified files without altering creative revisions. Symlink substitution is refused. A stale revision or malformed cursor fails closed.
- Chromium synthetic bridge tests exercise user opt-in, paged results, error statuses, exact source stamp, absence of raw paths/digests in the request, voice-track references, and reset after a new creative revision. Synthetic responses are UI contract tests, not evidence of real imported bytes.
- All heavy Rust/Tauri/browser/platform checks run in GitHub CI at the exact PR head. This feature doesn't change the pinned Semwright Native SDK or the native Driver Host/Broker authority.

**Not claimed:** Project Graph admission; inferred asset-to-scene edges; source rights; safety of third-party decoders; full media auditing above budgets; independent installed-device or creative acceptance. Actual Graph admission must come from the canonical Semwright authority as a separate signed/verified operation.
