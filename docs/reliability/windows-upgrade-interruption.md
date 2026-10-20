# Windows upgrade interruption and recovery contract

## Requirement and scope

This contract supports the v0.1 installer and recovery obligation tracked in
#126, with project preservation and coherent restore under CR-PROJECT-01,
CR-PROJECT-02, CR-PROJECT-06 and ACC-003/004. It covers a deliberately interrupted
NSIS process on an isolated Windows runner. It does not establish physical
power-loss durability, platform signing, a published upgrade or human acceptance.

The original source-bound 0.1.0 to 0.1.1 fixture cycle on source 480b4ad9 passed
install, upgrade and uninstall once each. Its interruption state remains NOT_RUN.
A new checkpoint source, compiled setup and original execution require their own
identities and evidence; this contract does not relabel the earlier cycle.

## Existing recovery path

The installer stages complete candidate bytes in an exclusive GUID directory in
the destination parent. It verifies those bytes before withdrawing a predecessor.
Only a content hash and size from the admitted predecessor profile authorize
withdrawal. Publication rechecks the held stage handle and performs a no-replace
rename. Project databases are not installer payloads.

The classifier already admits three states for a listed payload: exact candidate
bytes, exact pinned predecessor bytes, or an absent target. A new invocation can
therefore retain candidate files, replace only predecessors and fill missing
files. A modified owned file vetoes the upgrade before payload withdrawal.
Uninstall preserves content mismatches; it must still remove positive, intact
owned controls. Neither operation adopts or recursively cleans older stages.

## Checkpoint activation and reservation

The source contains an optional MWINST_CI_INTERRUPTION compile-time block.
Normal release builds must leave this define absent. A6 owns the reviewed
fixture build overlay, records the define, rendered template, setup digest,
version/profile pair and source/tree/lock identities. An environment variable
alone cannot enable a normal build.

For the single interrupted invocation, set MOTIONWRIGHT_NSIS_INTERRUPT_ID to a
new braced GUID. The runner admits a private TEMP root and supplies
GITHUB_ACTIONS=true. The environment check prevents accidental activation; it
is not authentication or consent.

EarlyChecks arms the hook after full destination preflight and before any
predecessor payload withdrawal. It validates the GUID and requires the previous
main executable to match its pinned profile. It captures its actual PID and
process-creation FILETIME, then reserves the following leaf with CREATE_NEW,
share mode zero and a noninheritable handle:

    TEMP/motionwright-nsis-{GUID}.checkpoint

An existing leaf is a refusal. The GUID is never reused, the receipt is never
overwritten, and the source does not delete it. CREATE_NEW does not establish
ancestor confinement; A6 must independently admit the runner's TEMP directory.
A failure before the checkpoint may leave an empty reserved file. Preserve it
as incomplete evidence.

## Exact interruption point and receipt

The hook is restricted to MW_MAIN. It runs after:

1. The complete candidate main payload has been staged and verified.
2. The pinned previous main executable has been withdrawn.
3. REQUIRE_ABSENT has confirmed the canonical target is absent.

It runs before MWOWN_PUBLISH and before the later resource, uninstaller,
association and registry publication steps. The hook rechecks the stage.

The installer writes one fixed-order line in UTF-16LE, without a BOM or trailing
NUL, ending with CRLF:

    motionwright.nsis-checkpoint/1|id={GUID}|pid=DECIMAL|creation_filetime=DECIMAL64|phase=main-withdrawn-before-publication|stage=mw-stage-{GUID}/payload.bin|candidate_sha256=HEX64|candidate_size=DECIMAL

The actual stage field uses a Windows backslash. The two GUIDs are independent:
the interruption operation owns the receipt, while the stage allocation owns
its directory. The reader must require exact field names/order, the literal
phase, canonical GUIDs, positive bounded integers, one admitted relative stage
and the expected candidate digest/size. FILETIME must remain an exact 64-bit
integer or decimal string, never a floating-point number.

The writer checks the WriteFile result and exact byte count, flushes the file
and checks the close result. Sharing remains disabled until that close. A6
retains and hashes the closed original bytes before parsing them. File flush
alone makes no claim about durable stage, registry or SQLite state after power
loss.

Every armed path stops. After publishing the receipt, the installer pauses for
30 seconds. If the runner does not terminate it, it exits with code 4 using
Quit, with no continuation to payload publication or installer callbacks.
Code 4 is a failed/expired fixture rendezvous, not successful interruption.

## Runner admission and termination

A6 retains its original process handle and checks the receipt's PID and creation
FILETIME against that same live process. It also binds the executable bytes,
original launch operation GUID, setup/profile inputs, run/job/attempt and
private directory to the admitted request. A child or elevated process with a
different identity must not be killed under the original handle assumption.

Only a complete matching receipt and a still-live matching handle permit one
TerminateProcess call for that process. Wait on that same handle and retain its
observed terminal status. A successful TerminateProcess return alone is not a
terminal observation. No process-name lookup, broad process kill or second
launch substitutes for this custody.

A missing/partial receipt, changed identity, expired pause, failed wait or
ambiguous effect leaves this scenario unverified. UNKNOWN never authorizes
another installer invocation or a new key hiding the previous attempt.

## Expected interrupted state

Retain a complete original snapshot before starting the upgrade and another
after the known observed termination. At this checkpoint:

- The previous canonical main executable is absent.
- The named stage contains the complete candidate bytes with the recorded hash.
- Previous resource/sidecar payloads, uninstaller and registration are unchanged.
- The project identity, revision and complete DB/WAL/SHM family are unchanged.
- Unowned files and any earlier unrelated stage directories are unchanged.

Open SQLite only through a copied complete family for independent inspection.
Do not checkpoint or mutate the retained original database to simplify the
comparison. Record both logical Project/journal equality and byte identities.

## Permitted recovery operation

After the termination and interrupted state have both been admitted, a
separately identified recovery operation may invoke the same setup bytes once,
with the interruption variable absent. It references the completed interrupted
operation and its original receipt. This is an explicit repair of a known state;
it is not a retry of UNKNOWN.

The existing preflight and ownership guard remain authoritative. A changed or
unowned payload, redirected path or unsupported version must refuse recovery.
No new overwrite, source adoption, broad cleanup or weakened hash check is
permitted. Existing complete candidate files can be retained; missing files are
restaged and published through the normal path. The interrupted stage remains
untouched and available for inspection.

After a successful recovery, verify every candidate-owned payload and the
registered version, the original project family and the unowned sentinels.
Also retain a separate modified-owned-resource refusal/preservation case and
an intact-owned-file removal control. Do not change those expected results to
match whatever the installer produced.

## Updated application acceptance

The recovered current application must open the same project, perform one
visible supported edit, close, reopen and expose the expected identity,
generation and revision with the exact changed field. A3 provides the current
supported UI action and A6 owns the installed-window runner. Opening the old
application before upgrade or querying only SQLite does not execute this case.

The existing future-schema rejection and transactional migration tests remain
separately source-bound. An installer preservation result does not prove a
migration or permit a future database to be downgraded.

## Evidence state

At this draft: source implementation proposed; NSIS compilation and physical
interruption/recovery execution NOT_RUN. Required original evidence includes
setup and predecessor identities, source and rendered overlay, intent,
checkpoint bytes, process identity/terminal, complete before/interrupted/
recovered snapshots, updated-application edit/reopen observations and all
original failures. A5 decides formal clause sufficiency independently.

Primary API references:

- https://nsis.sourceforge.io/Docs/System/System.html
- https://nsis.sourceforge.io/Docs/Chapter4.html
- https://learn.microsoft.com/en-us/windows/win32/api/combaseapi/nf-combaseapi-clsidfromstring
- https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes
- https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
- https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-writefile
- https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers
- https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess
