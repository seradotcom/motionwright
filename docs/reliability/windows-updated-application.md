# Updated Windows application observation

The updated-application oracle checks one visible edit and one read-only reopen of an existing project after an independently observed Windows upgrade recovery. It binds the installed executable to the compiled product, selects the saved project by UUID, observes the original save response, and compares retained SQLite state across two distinct application lifetimes.

The tool consumes original recovery evidence. It does not install, interrupt, recover, compile or repair the application. Its synthetic tests and PowerShell syntax checks are not an installed application run. Physical interruption, recovery, edit and reopen remain **NOT_RUN** until their respective original observations exist.

## Prerequisites and separate authorities

Use an isolated, disposable Windows CI environment with the application's normal profile and a visible interactive desktop. Do not redirect the Windows profile to manufacture first use.

The command requires three independently pinned source checkouts:

- The **product** checkout identifies the executable and installer pair in the compile receipt.
- The **gate** checkout supplies the canonical NSIS checkpoint, live-context and termination readers in tooling/windows_nsis_interruption.py.
- The **oracle** checkout supplies this observer, its UI controller and its existing custody helpers.

The gate helper is imported after its Git identity and bytes have been checked. The oracle calls its existing readers; it does not implement a second checkpoint protocol. The gate source must contain that helper and its declared source paths. A compilation-only receipt is insufficient to begin an application observation.

The evidence input must contain all of these originals:

| Reference | Required relationship |
| --- | --- |
| Compile summary | Both normal and opt-in variants were compiled from the exact product and gate; source observations before and after agree. |
| Normal and opt-in setups | Hashes match their compile records, differ from each other, and bind the same current main executable. |
| Current main executable | Bytes match the compile record, recovered installation and Windows version resources. |
| Predecessor profile | Independently pinned profile identifies the exact owned subset of the complete original installation and targets this current main/version. |
| Checkpoint, live context and interruption terminal | The canonical reader confirms their operation, original process identity, candidate bytes and known termination. |
| Recovery terminal | A separate original normal-setup process has a known successful terminal in the same product/run context. |
| Before and recovered snapshots | They bind the interrupted and recovery operations and retain the complete installed namespace and project database family. Original foreign files and the database family survive recovery byte for byte. |

The three operation UUIDs—interruption, recovery and this observation—must differ. A successful reader or compiler process does not supply a recovery terminal. A caller-authored PASS field, an installer exit inferred from a later file, or an unconfirmed termination cannot substitute for an original.

The predecessor include generator is reused to validate and retain the pinned profile with the producer's exact architecture identity, **x64**. This is an integrity check over previously reviewed ownership material. It does not grant ownership authority to an arbitrary inventory or execute a generated include.

Every owned entry must match its original before-snapshot bytes. All other original installation entries remain foreign and must occur unchanged in the recovered snapshot. Complete snapshots are retained; they are never filtered down to the owned roster. The profile's package, capture receipt and owned source files must remain available at their original relative paths beside the profile, so the canonical helper can revalidate and capture the entire input family.

## Historical input

The input JSON uses schema **motionwright.windows-updated-application-input/1** and exactly these fields:

| Field | Meaning |
| --- | --- |
| operation_id | New, canonical nonzero UUID for this observation. |
| run | Original repository, positive integer run_id, and positive integer run_attempt; all match the disposable Windows environment. |
| product_sha, gate_sha | Complete Git commit IDs, also supplied independently on the command line. |
| version | Expected product version in the compile and installed PE records. |
| refs | Original references listed below. |
| registry_keys | Explicit registry-key roster used by the original lifecycle snapshots. |
| install_root, database, executable | Absolute, plain paths from the recovered lifecycle. The database is outside the installation. |
| project_id, generation | Exact preserved project identity. Selection by title alone is insufficient. |
| revision, old_title | Original preserved revision and title. |
| new_title | Different title: 1–96 ASCII letters, digits, spaces, periods, underscores or hyphens; starts with a letter or digit and has no surrounding spaces. |

The refs object has exactly these members: compile_summary, normal_setup, opt_in_setup, current_main, predecessor_profile, checkpoint, interruption_context, interruption_terminal, recovery_terminal, before_snapshot, and recovered_snapshot.

Each reference has path, bytes and sha256. Paths are relative to the separately supplied evidence root. Existing lifecycle guards reject aliases, reparse points, missing originals and changed bytes. JSON with duplicate keys or nonfinite values is rejected.

The recovery terminal uses the existing **motionwright.windows-lifecycle-terminal/1** shape with phase **upgrade**. Its context binds the recovery operation UUID, original PID and creation filetime, normal-setup identity, source SHA and run. Its original_terminal must be the lifecycle helper's known exit-zero observation. Recovery uses the separately admitted normal variant, built from the same exact product source, target version and captured main payload as the opt-in variant, with a distinct setup digest. It is a new operation after admitted known termination and interrupted state; UNKNOWN does not authorize it. The before and recovered references use **motionwright.windows-lifecycle-snapshot/1**.

The input file's SHA-256 is a separate command-line pin. This binds the reviewed assembled input; it does not replace independent provenance review of the originals it names.

## Invocation and custody

Run Python from the oracle checkout with tooling/windows_updated_application.py. Supply all of the following arguments:

~~~text
--product-repo PRODUCT_CHECKOUT
--gate-repo GATE_CHECKOUT
--product-source PRODUCT_COMMIT
--gate-source GATE_COMMIT
--oracle-source ORACLE_COMMIT
--input REVIEWED_INPUT_JSON
--input-sha256 REVIEWED_INPUT_SHA256
--evidence-root ORIGINAL_EVIDENCE_DIRECTORY
--output NEW_OBSERVATION_DIRECTORY
~~~

The output must be new, separate from the input evidence and outside all three checkouts and the live profile. Each invocation receives a new directory; existing attempts are never overwritten. The command does not dispatch CI or publish a release.

Git and source files must remain unchanged. Expected line-ending conversion in a checkout is recorded by the canonical source observer rather than silently treated as identical bytes.

## Visible operation

The first application lifetime starts the exact installed executable once. The Python parent retains the original process handle and independently reads its creation time and executable path. The controller is bound to that PID, creation time and visible Motionwright window.

The controller opens the project library and finds the complete saved UUID, following the existing paginated list within a fixed bound. After selection, it reopens the library and requires the same UUID, title, revision and **Current** marker. This marker is derived from the project accepted by the application coordinator.

In Brief, the controller requires the current title and revision. Existing local input, an unconfirmed submission, a recovery warning or a conflict prevents an edit. The expected future original-response marker must not already be present.

The controller then delivers the safe title once and invokes **Save title** once. Before that invocation, it changes the observation state to UNKNOWN. Subsequent polling only observes. ACK requires the source-defined message that the original save reply and saved revision agree, together with the new title and expected next revision. A returned UI Invoke, matching database values or a reload does not establish ACK.

The first process must close gracefully with a known zero terminal. Forced cleanup only uses the retained original process and cannot produce a successful observation. If Save is unconfirmed, there is no second lifetime or replay, even when a retained database copy contains the requested title.

Only a fully observed first ACK, known graceful terminal and complete custody permit a second process. It has a distinct PID/creation-time identity, selects the same UUID, and reads the updated title and revision without typing or invoking Save. Its terminal must also be a graceful known success.

## SQLite and semantic checks

The installer must preserve the before and recovered database family byte for byte, including explicit absence or retained presence of WAL and SHM. The live recovered family is checked against those originals before launch.

After each known graceful terminal, the oracle captures a stable complete family into exclusive original files. SQLite opens a separate inspection copy with mode=ro and query_only=ON. It does not use immutable=1, which could ignore an authoritative WAL. Opening or recovering an inspection copy cannot modify the retained originals.

The reader distinguishes storage schema from project-document schema. With historical input/1, supported storage schemas before launch are 2 and 3 and project schemas are 1 and 2; after Save, storage schema 3 and project schema 2 are required. Explicit input/2 selects source-bound storage 4 or 5 as described below. The default remains historical.

The comparison requires:

- The same project UUID and generation, and exactly one additional revision.
- The requested title, preserved authored fields and collections, and the active branch's next revision.
- The domain's required invalidation of resolved reviews on the active branch; review text, anchors and unrelated review state remain intact.
- Exactly one appended rename event and one new original, non-replayed request receipt; earlier events and receipts remain unchanged.
- The same semantic document, events and receipts after the read-only second lifetime.

A legacy project migration may add fields while preserving pre-existing values; a current schema-2 document may not acquire unrelated fields. The reader has explicit database, JSON and row bounds. Larger fixtures require a separately reviewed extension rather than silent truncation.


## Explicit storage 4 and 5 input

The historical input/1 contract remains unchanged: it admits storage 2 or 3 before launch and requires storage 3 after Save. An integer override, the first-use UI, a document version or an observed database version cannot select Storage4.

The successor **motionwright.windows-updated-application-input/2** keeps every input/1 field and adds exactly **storage_cohorts**, with **before** and **after** records in the **motionwright.storage-source-cohort/1** format. Its refs also require **before_storage_source** and **before_storage_observation**. These are the retained original storage declaration and its canonical source observation, with their own byte counts and digests.

Each cohort binds the source commit and tree, the Git and physical checkout identities of crates/storage/src/lib.rs, the recorded line-ending representation, the expected storage version, project-document version and receipt family. The historical declaration is interpreted from its bounded original bytes, agrees with its original source observation, and must match the predecessor profile's source commit and tree. Storage2 additionally retains the existing exact automatic-project predecessor source/binary admission. Storage 3, 4 and 5 predecessors require their own reviewed profile and original source evidence.

The after cohort must explicitly select storage 4 or 5. It must match both the product observation in the original compile receipt and the independently observed current product checkout before application launch. The product source closure also includes crates/storage/src/apply_receipt_binding.rs. The oracle source closure includes tooling/storage_schema_contract.py and its tests. Missing source originals, unsupported declarations, changed source identities and cross-cohort substitutions are refused.

Both the before and recovered lifecycle snapshots use **motionwright.windows-lifecycle-snapshot/2** and retain the **before** cohort. Installing or recovering an executable does not migrate the saved database. Only the subsequent application operation may produce the after cohort. The producer must propagate these contracts through capture, readback and input assembly; changing a pinned oracle commit alone is insufficient.

| Observation | Storage | Project document | Receipt representation |
| --- | --- | --- | --- |
| Admitted historical predecessor | Explicit 2 | Historical 1 or 2 | Four historical fields; no reserved binding columns |
| Admitted predecessor | Explicit 3 | 2 | Four historical fields; no reserved binding columns |
| Admitted current predecessor | Explicit 4 or 5 | 2 | Six fields, including existing binding marker and payload |
| After the new original Save | Explicit 4 or 5 | 2 | Preserved old rows and exactly one new V1 Save receipt |
| Read-only reopen | Same admitted 4 or 5 | Same saved document | Exact same events and receipt rows |

Explicit semantic observations use **motionwright.updated-application-sqlite/2** and include their admitted cohort. The complete oracle receipt uses **motionwright.windows-updated-application/2**. Historical input/1 retains its original receipt and semantic representation.

### Storage 5 layout and admission

Storage 5 keeps the six-field receipt representation. The read-only inspection copy must also contain the canonical CAS table definitions, operation indexes and one valid store identity; missing, replaced or foreign CAS objects fail before a project snapshot is admitted. Inspection never repairs these objects, rewrites a retained original or treats an integer user_version as source authority. A future schema 6 is not supported by this observer.

Both modern physical selections remain closed until their exact product and oracle source pairs are reviewed and frozen. The producer requires its internal compiled-storage admission for storage 4 and 5; a dictionary, caller-supplied cohort or unpinned oracle commit cannot substitute for that admission. Supporting a database representation does not authorize an installer or application run.

Before an intentional installer interruption, the producer closes its exclusive termination-intent file and reads the bounded original bytes back, comparing the exact serialization, size and digest before the one permitted TerminateProcess request. Changed or unreadable intent bytes authorize no termination. This readback establishes the observed file contents; it does not independently prove power-loss durability, global deadline supervision or closure of every descendant process.

### Receipt preservation and the new Save

The four historical selected fields are request_id, request_sha256, raw result_json and created_at. A migration from storage 2 or 3 to the admitted storage 4 or 5 preserves each byte of these text values and appends only integer **0** and SQL **NULL**. It does not manufacture evidence for old generic receipts or typed project creation. A 4→4, 4→5 or 5→5 observation preserves all six fields of every existing row, including both V0 and V1 records. Request identities must remain unique.

The modern reader validates the complete source-selected SQLite layout and actual marker/payload types. Marker 0 requires SQL NULL; marker 1 requires bounded text. A missing column, a forged default, an unsupported marker, a non-text payload or an oversized payload cannot silently become a legacy row. Binding payloads are limited to 4,096 UTF-8 bytes before they are materialized.

The distinct new generic Save must have marker 1 and a V1 binding with exactly twelve string fields: format, project_id, generation, resource, request_id, request_sha256, base_revision, base_branch, result_revision, result_branch, result_sha256 and binding_sha256. Duplicate and unknown JSON keys are rejected. UUIDs are canonical and nonzero; revision strings use canonical decimal spelling within SQLite's signed 64-bit range. Request identities retain the storage writer's bounded text contract.

The independent observer hashes the exact raw result_json bytes before decoding that result. It checks the published ten-string checksum tuple and its NUL-terminated motionwright.apply-binding/1 domain, the canonical request digest for the selected project and rename Change, and the original request/base/result authority, generations, revisions and branches. Earlier V1 rows receive representation, raw-result and checksum checks and must remain unchanged; this does not claim a fresh execution or a full revalidation of every historical Change.

A verified new V1 receipt describes a local saved observation. It cannot replace the original UI Save response. Even complete matching SQLite evidence after UNKNOWN does not authorize reopen, replay, a new mutation or acceptance. The existing original-handle process, controller, terminal and custody rules still govern both application lifetimes.

## Results and limitations

A complete runtime observation reports **SOURCE_BOUND_UPDATED_APP_OBSERVATIONS**. It identifies technical observations for the exact supplied sources and originals. It does not claim human, visual or release acceptance.

An incomplete observation retains the original application outcome separately:

| Outcome | Meaning |
| --- | --- |
| NOT_STARTED | The controller was not dispatched. The application process may already have started. |
| NOT_SENT | A validated original controller record establishes that no Save was invoked. |
| UNKNOWN | The original save result was not established. Matching later state does not convert it to ACK or authorize replay. |
| ACK with incomplete custody or terminal | The known original application result remains ACK, but the observation is incomplete and cannot proceed to reopen. |
| OBSERVED | The second lifetime read the selected project without invoking Save. |

A complete, validated original ACK remains ACK after a controller timeout, nonzero exit, or later custody failure. A timeout never establishes controller completion, even if an exit code is also present. Once controller dispatch is possible, an unobserved result remains UNKNOWN; an opaque process error does not prove that dispatch or Save never occurred.

Late process-observation, log-retention and receipt-publication errors preserve the known application result and record incomplete custody. An incomplete terminal or custody observation cannot proceed to a second application lifetime. Partial logs, intents and observations remain in the exclusive output. Failed final receipt publication returns a nonzero result and a concise custody failure.

The tool does not prove power-loss recovery, broad installer compatibility, visual quality, every project migration, or human acceptance. It does not reconstruct absent historical evidence. A new source composition requires evidence associated with that composition; prior-source results remain attributed to their original source.

## Synthetic verification

Focused Python controls exercise malformed or foreign originals, unknown outcomes with matching SQLite state, complete database-family capture, WAL visibility, migration and rename semantics, receipt preservation and the no-replay boundary. Direct lifetime and orchestration controls inject controller timeouts, ambiguous process errors, output-read failures and finalization failures while using synthetic files and SQLite databases. OS process and UI boundaries are mocked; these controls do not execute an installed application. Producer-compatibility controls run the original predecessor include generator and retained-file/snapshot helpers against complete synthetic profiles and inventories, including preserved foreign files, missing or modified entries, invalid pins and path aliases. The orchestration control reaches a simulated application boundary only after the real profile helper admits the input; its prior checkpoint and PE observations remain synthetic. UI source contracts check the controller against the actual project library and Brief controls.

~~~text
python -B -m unittest discover -s tooling/tests -p test_windows_updated_application.py -v
python -B -m unittest discover -s tooling/tests -p test_windows_updated_application_ui_contract.py -v
~~~

PowerShell parser validation checks syntax without executing the controller. These controls can admit a reviewable oracle implementation; they cannot supply an installed application run, interruption, recovery or human approval.


## Explicit schema 6 source successor

Schema 6 is a separate source-selected storage family. It keeps project-document
schema 2 and the six-field receipt representation used by schemas 4 and 5. A
schema number, a schema-5 pin or a synthetic typed consumer cannot authorize a
schema-6 installed run. Its physical product/oracle pins remain unset until A1
composes and reviews the complete source.

The canonical read-only layout validator requires the exact two budget tables,
their four automatic indexes, six budget triggers and all 27 registered-writer
fences across the nine existing writer tables. The six CAS writer fences are
part of those 27; they are not aliases for the six budget triggers. Quoted SQL
values and operators remain significant: changing 'dispatching' case or changing
IS NOT 6 to <> 6 cannot pass normalization. Historical families reject reserved
schema-6 objects. None of these inspection functions repairs or migrates a
database.

The existing source-cohort /1 record still binds storage/src/lib.rs. Schema 6
also requires the separately retained root_operation_budget.rs observation and
original bytes from the same source/tree, with exact Git or admitted LF-to-CRLF
encoding. The reviewed module is 27,365 Git bytes with SHA-256
a09bf76a6c80fdc08cf0304c9127b5b67b0c3f5b8af0dba1f658486a79cec3b6.
The compiler's source closure retains that module independently; an observation
digest without its matching original is insufficient.

Producer, installed cold-start reader and updated-application oracle pass the
explicit cohort through their actual SQLite readers. The oracle preserves old
receipts from schemas 2–6 while checking the new save binding and unchanged
reopen state. The cold-start reader checks schema-6 layout before allowing its
single native Create attempt. An uncertain Create remains non-replayable.

The isolated source checkpoint has 46 canonical schema-contract methods and 14
new consumer methods. The selected six-module Python regression has 345 PASS,
zero failures/errors/skips; three previously failing A6 workflow integration
controls remain explicitly excluded with their original failures retained.
These results cover Python, actual isolated SQLite files and mocked process
boundaries. They do not execute Rust migration, an installer, native GUI, an
actual writer with SQLITE_INNOCUOUS or native process termination. The original
consumer RED and the corrected explicit-module-load RED are retained separately.
Whole-process supervision, external blocked-I/O deadline H and actual compiler/
plugin/source admission remain separate prerequisites for physical execution.
