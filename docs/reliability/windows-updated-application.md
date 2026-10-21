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
| Predecessor profile | Independently pinned profile identifies the original installed inventory and targets this current main/version. |
| Checkpoint, live context and interruption terminal | The canonical reader confirms their operation, original process identity, candidate bytes and known termination. |
| Recovery terminal | A separate original normal-setup process has a known successful terminal in the same product/run context. |
| Before and recovered snapshots | They bind the interrupted and recovery operations and preserve the complete original project database family. |

The three operation UUIDs—interruption, recovery and this observation—must differ. A successful reader or compiler process does not supply a recovery terminal. A caller-authored PASS field, an installer exit inferred from a later file, or an unconfirmed termination cannot substitute for an original.

The predecessor include generator is reused to validate and retain the pinned profile. This is an integrity check over previously reviewed ownership material. It does not grant ownership authority to an arbitrary inventory or execute a generated include.

## Closed input

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

The recovery terminal uses the existing **motionwright.windows-lifecycle-terminal/1** shape with phase **upgrade**. Its context binds the recovery operation UUID, original PID and creation filetime, normal-setup identity, source SHA and run. Its original_terminal must be the lifecycle helper's known exit-zero observation. The before and recovered references use **motionwright.windows-lifecycle-snapshot/1**.

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

The reader distinguishes storage schema from project-document schema. Before launch, supported storage schemas are 2 and 3 and project schemas are 1 and 2. After the updated application writes, storage schema 3 and project schema 2 are required.

The comparison requires:

- The same project UUID and generation, and exactly one additional revision.
- The requested title, preserved authored fields and collections, and the active branch's next revision.
- The domain's required invalidation of resolved reviews on the active branch; review text, anchors and unrelated review state remain intact.
- Exactly one appended rename event and one new original, non-replayed request receipt; earlier events and receipts remain unchanged.
- The same semantic document, events and receipts after the read-only second lifetime.

A legacy project migration may add fields while preserving pre-existing values; a current schema-2 document may not acquire unrelated fields. The reader has explicit database, JSON and row bounds. Larger fixtures require a separately reviewed extension rather than silent truncation.

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

Focused Python controls exercise malformed or foreign originals, unknown outcomes with matching SQLite state, complete database-family capture, WAL visibility, migration and rename semantics, receipt preservation and the no-replay boundary. Direct lifetime and orchestration controls inject controller timeouts, ambiguous process errors, output-read failures and finalization failures while using synthetic files and SQLite databases. OS process and UI boundaries are mocked; these controls do not execute an installed application. UI source contracts check the controller against the actual project library and Brief controls.

~~~text
python -B -m unittest discover -s tooling/tests -p test_windows_updated_application.py -v
python -B -m unittest discover -s tooling/tests -p test_windows_updated_application_ui_contract.py -v
~~~

PowerShell parser validation checks syntax without executing the controller. These controls can admit a reviewable oracle implementation; they cannot supply an installed application run, interruption, recovery or human approval.
