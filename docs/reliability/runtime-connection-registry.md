# Runtime connection registry integrity

The desktop keeps an application-owned, project-scoped reference to a Semwright connection file. The reference is a locator, not an authorization grant: the existing ProductionConnection and resource checks remain responsible for admitting a selected runtime. The registry never copies connection-file contents, Broker tokens, or native job evidence.

## Product defect and correction

Previously, construction checked only the final registry directory and then retained its textual path. Subsequent reads, writes and clears checked the final JSON file, but did not revalidate the directory. A directory or ancestor redirected after construction could therefore make these operations reach another tree with a valid-looking reference. Losing the entire directory also allowed resolve to take the environment fallback and clear to report an absent record.

Construction now accepts trusted initial app-data aliases, rejects a final link or Windows reparse point, applies the existing private Unix directory permissions, and stores the canonical physical path. Every resolve, remember and clear validates that stored path before accessing a reference or taking a fallback: the directory must exist, be ordinary, have no group/other permissions on Unix, and still resolve to the stored path. A retargeted initial app-data alias does not redirect an existing registry object.

| Condition | Result |
| --- | --- |
| Valid registry and saved reference | Existing schema, project/generation, size and file checks apply. |
| Valid registry and absent JSON | Existing environment fallback and idempotent clear remain available. |
| Missing or invalid registry directory | Explicit error before fallback, staging or deletion; operations do not recreate or repair it. |
| Final directory link/reparse or redirected ancestor detected before an operation | Error; preserved original and alternate registry records remain untouched. |
| Unix directory gains group/other permissions | Error without changing those permissions or records. |
| Legitimate initial app-data alias | Normal round trip against its physical target; later alias retargeting does not move that object. |

The existing staged write, sync and rename behavior is unchanged, including preservation of the previous verified reference when Windows denies replacement. The wire schema, connection validation, project-generation scope and public IPC shape are unchanged.

## Regression and integration evidence

The regression-only commit `e04112d04ff6c8c8b8b0c90254250ffdace1f7a0` contains the new tests with the previous product implementation. [Owner Runtime First-Use Audit 39006277202](https://github.com/seradotcom/motionwright/actions/runs/39006277202) compiled and executed the Windows tests: four existing controls passed and `missing_registry_root_rejects_all_operations` failed with read `Ok(None)`, write staging error and clear `Ok(false)`. This is an executed behavior failure, not a compilation failure. The normalized full log SHA-256 is `fecd7a6b52843cc824dfa5b47c79e39bdd9403c4a6e260a3f8534a7b7b65b12a`.

The twelve added tests cover a missing root; Unix root and ancestor substitution separately for read, write and clear; Unix permission changes separately for those operations; a legitimate initial alias control; and retargeting that initial alias. Substitution fixtures use two physical trees with valid records for the same project/generation and snapshot both trees, including file bytes, entries and permissions. The references are synthetic locators; these tests do not claim an authenticated Broker session.

Run the actual desktop test module with its normal build prerequisites:

```sh
cargo test --locked -p motionwright-desktop --bin motionwright-desktop runtime_setup::tests -- --nocapture
```

The existing first-use workflow executes the Rust module on Windows. Its Python preflights on Linux/macOS do not execute the Unix Rust regressions. Platform evidence must identify the exact tested source SHA. Linux/macOS Rust execution and a real Windows junction fixture are required to verify their respective scenarios; an omitted test is not a platform pass.

## Requirement scope and limits

This change supplies registry-path integrity evidence for the v0.1 installed runtime and security requirements: safe connection selection, explicit recovery errors and preservation of unrelated data. It does not by itself close installed-owner acceptance, first launch, platform packaging or the complete authorization criteria.

These are pre-operation path checks, not atomic directory-handle confinement. They do not prevent a concurrent substitution after validation, identify a replacement ordinary directory at the same pathname, establish a full Unix ownership/ancestor policy, or authenticate a Broker principal. Windows reparse points are rejected as non-ordinary final registry directories; an actual junction regression is required before claiming that platform behavior was exercised. Unix checks reject any group/other permission bits rather than requiring an exact owner mode of 0700 during every operation. Current connection contents and native resource lifetimes continue to use the existing separate validation and close paths.
