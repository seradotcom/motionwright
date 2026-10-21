# Windows NSIS verification

The Windows NSIS workflow separates source controls, compilation and physical
observations. A successful source or compiler job does not establish installation,
interruption recovery, an original Save acknowledgement, or human acceptance.

## Source roles and scopes

The gate checkout owns the compiler, shared effect producer and readonly
checkpoint/lifecycle readers. An independently pinned product checkout supplies
the application and installer template. A third exact checkout supplies the
updated-application observer. Source inventories are verified against Git blobs
and actual checkout bytes; the older product is not required to contain new gate
files.

The default scope is **g0**. Automatic branch events only execute source controls.
**compile** is available through an explicit workflow dispatch and builds one
application, then normal and opt-in NSIS variants without running either setup.
It does not admit a predecessor profile. **physical** is a separate explicit
dispatch scope: it admits the fixed reviewed predecessor and two synthetic
resource files during the same compilation, then uses that single artifact in
two fresh Windows jobs. Unknown scopes fail source admission.

The updated-application observer is pinned to
`016cd88c4ef20ada70fcc446361c40e4a0b81ca1`. Its original controls remain independent:
56 runtime controls and 12 UI contract controls. The producer does not replace or
copy its Save/reopen runtime.

## Shared producer and compilation custody

`tooling/windows_installer_producer.py` is also used by the package workflow.
Its legacy commands retain the original three installer operations:
previous installation, current upgrade, current uninstall. Their receipt schema
and readonly lifecycle validator remain unchanged. Those historical operations
do not claim the new default-token, mixed-uninstall or interruption cases.

The predecessor is selected by fixed source/tree, Actions artifact, archive and
member identities. Metadata availability is checked at execution; expired or
changed bytes are not accepted. The historical artifact's failed modified-owned
case remains a failed historical observation. It only supplies reviewed binary
and ownership provenance. No historical project is reused as the new fixture.

The complete profile family is retained beside its manifest. The existing
ownership helper admits its x64 identity, target version/main, setup, independent
uninstaller, receipt and owned payloads. Both variants must contain its canonical
include and original captures. The two compiled setups have different digests
and share the same retained application, profile and resource overlay.

The main application is built once and retained before either bundling command.
Compiler command intents, original terminals, streams, rendered instructions,
payload captures and complete variant inventories are retained. Resource capture
bytes and rendered directory instructions are checked. Retention does not rewrite
paths embedded in original compiler outputs.

Compilation retains the existing 256 MiB per-payload, 512 MiB total diagnostic
and 4,096-file limits. Exceeding a limit leaves an incomplete observation and
preserved originals, never an increased limit or synthetic success.

## Fresh Windows cases

Physical jobs are serial and each receives a new hosted Windows runner. They do
not rebuild the application. Only this run's compile artifact and exact source
checkouts are accepted; projects and preservation controls are created in the job.
There is no arbitrary local-input argument or route for existing private native
sessions or user projects.

Before installer effects, the producer observes the actual current token:
elevation state, integrity level, user SID and interactive session. It verifies
native Known Folder paths against the environment and checks namespace,
registration and project-store freshness without deleting existing content.
An elevated, unknown or noninteractive token fails preflight. The workflow does
not disable UAC, manufacture a user profile or infer elevation from `currentUser`.
Actual child token and image observations must match the retained original handle.
Token availability remains an execution prerequisite, not an assumed success.

### Current default install and mixed uninstall

The current normal setup is launched once with `/S` and no `/D`. Its actual
default destination, current application bytes, version and registration are
observed. The producer then changes one synthetic owned resource, leaves another
owned resource intact and creates a foreign sentinel. One current uninstaller
invocation must preserve the modified owned and foreign bytes while removing
the intact owned payloads. Registration and the full project-store family are
checked separately.

### Interruption, normal recovery and the updated application

The exact predecessor is installed once in a fresh destination. The existing
cold-start observer creates and reopens a real synthetic project under the real
runner profile. The producer retains UUID, generation, revision, title, complete
DB/WAL/SHM and registry snapshots, then adds foreign and unrelated-stage controls.

One opt-in setup receives a fresh operation GUID and a new checkpoint directory
with a protected user/SYSTEM DACL. The checkpoint's bounded original bytes,
GUID, image, PID and full creation FILETIME must match the still-live original
process handle. The existing checkpoint readers remain the only checkpoint
parser and structural terminal validator.

One termination attempt is made on that same handle. A missing, partial,
expired or mismatched checkpoint, ambiguous wait, or checkpoint exit code 4
does not authorize recovery. After known termination, the interrupted snapshot
must show the old main withdrawn, the exact current main in the named stage,
and unchanged other owned files, foreign files, registry and DB family.

Only after that admission is a new operation allowed to invoke the **normal**
setup once. It has a different digest from the opt-in setup, the same source,
version, application and profile, and no interruption environment variable.
Known zero exit is necessary but not sufficient: complete current payloads,
registration, original database family, foreign content and original stage
must also be admitted.

The resulting closed input contains eleven original references and an independent
SHA-256 pin. The unchanged observer runs once, selects the exact project, requires
its original Save acknowledgement, then permits the separate readonly reopen only
after complete first-lifetime custody. A matching SQLite snapshot is never a
substitute for that acknowledgement.

## Evidence and failure semantics

Every installer invocation begins with an exclusive intent and an in-memory
UNKNOWN result before spawning. Opaque launch errors and timeouts cannot be
reported as proven no-spawn. A known original terminal remains known if later
snapshot or publication custody fails, but no following effect is authorized.
There is no installer replay, new operation key hiding an uncertain attempt,
cleanup to manufacture freshness, or automatic recovery after UNKNOWN.

The compile artifact is retained once. Each physical artifact retains only its
new synthetic observations, checkpoints, intents, terminals and observer output.
To read physical references offline, reconstruct its `originals/compile` directory
from that same run's compile artifact; original receipts and paths are unchanged.
Failure artifacts are diagnostics, not admission. Human acceptance and stable
release authorization remain separate decisions.
