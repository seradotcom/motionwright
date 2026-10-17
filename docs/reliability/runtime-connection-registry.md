# Runtime connection registry integrity

The desktop keeps an application-owned, project-scoped reference to a Semwright connection file. The reference is a locator, not an authorization grant. Existing ProductionConnection and resource checks admit a selected runtime. The registry never copies connection-file contents, Broker tokens, or native job evidence.

## Product defect and correction

Previously, construction checked only the final registry directory and retained its textual path. Subsequent reads, writes and clears checked the final JSON file but did not revalidate the directory. A directory or ancestor redirected after construction could therefore make these operations reach another tree containing a valid reference for the same project and generation. Losing the entire directory also allowed resolve to take the environment fallback and clear to report an absent record.

Construction now accepts trusted initial app-data aliases, rejects a final link or Windows reparse point, applies the existing private Unix directory permissions, and stores the canonical physical path. Every resolve, remember and clear validates that path before accessing a reference or taking a fallback: the directory must exist, be ordinary, have no group/other permissions on Unix, and still resolve to the stored path. Retargeting the initial app-data alias does not redirect an existing registry object.

| Condition | Result |
| --- | --- |
| Valid registry and saved reference | Existing schema, project/generation, size and file checks apply. |
| Valid registry and absent JSON | Existing environment fallback and idempotent clear remain available. |
| Missing or invalid registry directory | Error before fallback, staging or deletion; operations do not recreate or repair it. |
| Final directory link/reparse or redirected ancestor detected before an operation | Error; preserved original and alternate registry records remain untouched. |
| Unix directory gains group/other permissions | Error without changing those permissions or records. |
| Legitimate initial app-data alias | Normal round trip against its physical target; later alias retargeting does not move that object. |

The staged write, sync and rename behavior is unchanged, including preservation of the previous verified reference when Windows denies replacement. The wire schema, connection validation, project-generation scope and public IPC shape are unchanged.

## Regression evidence and execution

The initial regression-only commit `e04112d04ff6c8c8b8b0c90254250ffdace1f7a0` retains the previous product implementation. [Owner Runtime First-Use Audit 39006277202](https://github.com/seradotcom/motionwright/actions/runs/39006277202) compiled and executed five Windows cases: four controls passed and `missing_registry_root_rejects_all_operations` failed with read `Ok(None)`, a write staging error and clear `Ok(false)`. The normalized log SHA-256 is `fecd7a6b52843cc824dfa5b47c79e39bdd9403c4a6e260a3f8534a7b7b65b12a`.

The initial corrected source `8ba5b3deca916a76c8e52f69647dd38ef74c7a8c` passed those five Windows cases in [run 39007067295](https://github.com/seradotcom/motionwright/actions/runs/39007067295), log SHA-256 `c40504e27404bdc9ab0f6e431aa687edc782d7c7164c481c8b5cd0e98a596400`. Python preflights on Linux/macOS in those runs did not execute Unix Rust cases.

The expanded regression-only commit `112be29523a17acbc35edcc02eb8f109a61c52df` is based on `4a2d614cfb808f159fca2d68d56570bb1e39302e`. It retains the old production code and adds the same eight Windows cases used with the correction. The module contains 16 applicable Unix tests and 13 applicable Windows tests. Counts are expectations until an execution records every test ID and outcome.

Substitution fixtures use two physical trees with valid records for the same project/generation. Each read, write and clear case substitutes a root or ancestor after constructing the registry and compares both physical trees afterward, including record bytes and directory entries. Unix also verifies mode changes. Alias controls verify a legitimate initial alias and the original physical target after alias retargeting. Synthetic references exercise registry persistence; they do not establish an authenticated Broker session.

Run the desktop module with the normal build prerequisites:

```sh
cargo test --locked -p motionwright-desktop --bin motionwright-desktop runtime_setup::tests -- --nocapture --include-ignored --test-threads=1
```

Windows requires fresh runner-provisioned NTFS fixtures for each source execution. Set `MW_REGISTRY_JUNCTION_FIXTURE` to an absolute directory inside `RUNNER_TEMP`. Its eight case directories are `root-read`, `root-write`, `root-clear`, `ancestor-read`, `ancestor-write`, `ancestor-clear`, `alias-initial` and `alias-retarget`. Each starts with ordinary `original/app-data/owner-native-connections` and `decoy/app-data/owner-native-connections` directories. Prepare `prepared-junction` toward the decoy registry for root cases, toward `decoy` for ancestor cases, and toward `original/app-data` for alias cases. The retarget case also requires `replacement-junction` toward `decoy/app-data`.

Tests move these entries, verify reparse/directory attributes and canonical targets, then perform real registry operations. Missing fixtures fail explicitly when the tests are invoked. Their ignore annotation excludes external fixture requirements from ordinary local runs; the dedicated execution must include them and report zero ignored. Provisioning evidence must establish the actual NTFS junction type: a reparse attribute alone does not identify that subtype.

## Requirement scope and limits

This change supplies registry-path integrity evidence for the v0.1 installed runtime and security requirements: safe connection selection, explicit recovery errors and preservation of unrelated data. It does not alone close installed-owner acceptance, first launch, platform packaging or the complete authorization criteria.

These are pre-operation path checks, not atomic directory-handle confinement. They do not prevent a concurrent substitution after validation, identify a replacement ordinary directory at the same pathname, establish a full Unix ownership/ancestor policy, or authenticate a Broker principal. Unix checks reject any group/other permission bits rather than requiring an exact owner mode of 0700 during every operation. Current connection contents and native resource lifetimes continue to use their existing validation and close paths. Platform acceptance must cite the exact source SHA, actual fixtures and executed cases; omitted or ignored cases are not a platform pass.
