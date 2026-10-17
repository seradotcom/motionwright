# Durable file import and read-only recovery

File imports are project mutations and must preserve one request identity from user intent through storage. The existing service wrappers regenerated asset and voice-track UUIDs on every call. Consequently, identical source bytes and metadata produced a different persisted Change on replay. The wrappers also read a source before checking the supplied project scope and revision.

## Required behavior and implementation

| Requirement | Product behavior | Real regression |
| --- | --- | --- |
| Idempotent local import | Stable, purpose-separated IDs for new imports; original persisted IDs for existing receipts | Asset and measured-WAV replay after database reopen returns the original result |
| Resource and generation authority | Current project identity and generation precede receipt lookup and source access | Asset and voice reject wrong resource, generation, missing project and stale new-request revision before missing-file IO |
| Historical result recovery | Explicit typed receipt lookup is read-only and needs no original file or CAS read | Asset and voice results remain readable after later edits, source deletion, CAS deletion and reopen |
| Exact payload binding | Purpose, name, MIME, voice label, event, request digest and recorded asset/track must agree | Changed bytes or metadata reject reuse; corrupt persisted hash, event or result is reported rather than treated as absent |
| Atomic project mutation | Existing Store::apply transaction commits project, event and receipt once | Two independent SQLite connections importing the same bytes/key converge on one event and one result |
| Rejected content hygiene | Validate the staged capture before CAS publication; clean only its private staging name | Active SVG and undecodable voice leave no published blob or staging file; altered same-size capture is rejected |

The reader uses one deferred SQLite transaction. It checks the current resource and generation, the original outcome, exactly one matching journal event, and the canonical request digest. The asset and any measured voice track are compared with that historical outcome. Later edits do not turn the original outcome into the current Project.

The existing import signatures remain available. New imports capture the source once into the existing CAS staging area, release the service mutex for content validation or audio decoding, and verify the capture digest again before publication. Publication uses create-new hard linking and verifies an existing destination, including a concurrent winner. No SQLite writer transaction spans copying or decoding. The staging guard never deletes a published CAS blob.

New application UUIDs use a versioned SHA-256 namespace over project, generation, request ID, import purpose and role, encoded as UUID version 8. They are not presented as UUIDv5 or time-ordered UUIDs. Existing receipts with older randomly generated IDs remain authoritative.

## Caller contract

`StudioService::recover_file_import(project_id, expected, request_id, intent)` returns `Result<Option<ApplyOutcome>>`. The typed intent is exported as `FileImportIntent`:

```json
{"kind":"asset","name":"source.wav","media_type":"audio/wav"}
{"kind":"voice","name":"source.wav","media_type":"audio/wav","label":"Narration"}
```

Persist the request ID and the exact metadata before invoking the mutation. Recovering a present receipt returns `replayed: true` and its original revision and IDs. Refresh the authoritative Project separately; applying the historical result as the latest document would discard later edits. An absent receipt is only an observation and does not authorize repeating an UNKNOWN mutation.

A normal file-import replay still verifies the caller's source bytes. A missing source remains an IO error on that API; use the explicit read-only recovery API to inspect the committed result. Neither path repairs missing CAS content. Project integrity and explicit source recovery remain separate operations.

## Reproduction and verification

The tests-only commit `3387aed77bfb7749097eabc0093cd98b277c4dd7` reproduced both identical-input failures as `RequestReuse` and all eight scope/revision cases as source `Io(NotFound)`. Two control tests passed. See [Project Portability run 39010483764](https://github.com/seradotcom/motionwright/actions/runs/39010483764).

Run the production-backed suites with:

```text
cargo test -p motionwright-storage -p motionwright-service --all-targets
cargo clippy -p motionwright-storage -p motionwright-service --all-targets -- -D warnings
```

The five original baseline cases are retained. Additional tests exercise receipt recovery, corrupt persistent records, real concurrent connections, capture mutation, published-blob retention and content rejection. They use temporary SQLite databases, real PCM WAV bytes and the actual CAS. A start barrier coordinates the concurrency test but does not force a particular internal SQLite interleaving.

## Limits

This change does not supply Broker consent, a native-operation execution grant, installed desktop acceptance or a new retry policy. Desktop intent persistence and transport integration require their own consumer tests. Receipt verification checks its recorded import binding and current generation; it is not a cryptographic authentication of the entire historical database.

Filesystem checks are path-based and do not claim complete confinement against adversarial concurrent ancestor replacement. Ordinary error cleanup removes the private capture; a process crash can leave a staging file. A later domain or concurrent revision rejection can leave an unreferenced content-addressed blob, which must not be removed opportunistically because another project may already reference it.
