# Atomic native source imports

`StudioService::import_native_capsule_source_file` captures an authored local file and binds its native capsule in one canonical project mutation. It uses the existing SQLite transaction, event journal, durable request receipt, content-addressed storage and project revision checks.

## Transaction boundary

The service accepts the current project generation and revision, a stable request ID, an input file and a `NativeCapsuleSourceSpec`. The spec contains the source name/media type and capsule metadata. The service derives the imported asset ID from the project, generation and request ID, captures immutable source bytes and builds one `Change::ImportNativeCapsuleSource`.

The asset and capsule enter the project together. An invalid scene, locked target, duplicate identity or SQLite write failure must leave the canonical document, revision, event journal and request receipts unchanged. The source bytes are staged before the transaction. The existing conservative CAS source-retention policy also applies to a published blob when a later database commit fails; a retained blob is not evidence of a committed project mutation.

Replacing a capsule source preserves the capsule identity and retains its previous immutable asset. Source editing does not carry previous fidelity evidence into the replacement. All fidelity dimensions start as unavailable, property claims are empty and the source save supplies no provider grant or native execution receipt.

## Original request recovery

A known identical request returns its original durable result. A changed source, capsule metadata or import kind cannot reuse that request ID. The regular import method verifies the supplied bytes for a known request.

`recover_file_import` with `FileImportIntent::NativeCapsuleSource` is a read-only alternative when the original response or source file is unavailable. It verifies the stored event/result binding and returns the historical result without importing, repairing CAS or running a native application. A later canonical revision does not rewrite that original result. A missing receipt remains an observation; it does not authorize replay of an operation whose outcome is unknown.

Generation and resource checks precede source access. Importing a project under a new generation therefore does not inherit the old generation's request authority.

## Admission and compatibility

This is a local source persistence API. It reuses the existing file size, source capture and asset-admission checks. The caller must still obtain actual project-edit authority at the application's ingress. Native recipe validation, renderer execution, output inspection and fidelity admission retain their existing authorities and contracts.

Desktop's existing asset/voice commands and their recovery handler reject this new intent explicitly. Its dedicated, qualified source-save ingress remains an integration responsibility; adding this persistence API does not activate a UI command.

Existing asset and measured-voice imports keep their request formats. The project document shape is unchanged. The new typed change is recorded in the journal; consumers must understand that change before opening or replaying histories that contain it.

## Focused regression checks

```sh
cargo test --locked -p motionwright-domain --test native_capsule_source_atomicity
cargo test --locked -p motionwright-storage --test native_capsule_source_atomicity
cargo test --locked -p motionwright-service --test native_capsule_source_import
cargo test --locked -p motionwright-service --test file_import_idempotency
```

The service tests exercise real temporary SQLite databases, injected event/receipt write failures, database reopen, original-result recovery, request-kind isolation and replacement-source fidelity reset. The storage tests verify actual CAS bytes, scope precedence, stale writers and receipt tampering. These checks do not establish installed-platform acceptance or native-renderer fidelity.

The source-import fidelity defaults (including renderer identifiers and loss text) are persisted receipt contract values. Future changes must preserve their historical interpretation through an explicit version; changing their wording alone must not invalidate existing receipts.
