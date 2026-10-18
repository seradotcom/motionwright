# Windows uninstall integrity

The Windows NSIS template binds application payload cleanup to the bytes captured for that installer build. A separately pinned predecessor profile permits an effective update from one reviewed previous package. Current payloads can be retained or restored when missing. Changed or unrecognized bytes are preserved. These source contracts require verification with the compiled Windows package; they are not execution results.

## Template and build inputs

`apps/desktop/src-tauri/windows/installer.nsi` is based on the Tauri template at commit `30da1fd6e17de6107ecc850c95dfb16b5729f2dd`, upstream file `crates/tauri-bundler/src/bundle/windows/nsis/installer.nsi`, SHA256 `dabed59013b1d78b879a1a85bc7f2eed2993b33a9a90cdabe5946de3d3950597`. Its MIT notice is retained in the template. The associated bundler source selects the NSIS 3.11 distribution and renders a fresh NSIS output directory.

The two Tauri configuration fields needed to select this template are:

```json
{
  "bundle": {
    "windows": {
      "nsis": {
        "template": "windows/installer.nsi",
        "installerHooks": "windows/uninstall-ownership.nsh"
      }
    }
  }
}
```

The template requires Windows MakeNSIS and Python 3. Tauri resolves `installerHooks` to an absolute path. The guard include locates the existing `tooling/nsis_owned_files.py` relative to its reviewed source directory. Compilation calls that helper with `!execute`, which uses CreateProcess on Windows, and requires exit code zero. No shell command processor or installed application file supplies the helper.

Each main executable, resource, and external binary receives a separate capture and SHA256/size include in the new NSIS build directory. `File` consumes the capture named by that include. Resource and binary symbols use the numeric index of the same repeated object iteration; compilation refuses a missing index. The build gate must inspect the rendered template and include bindings with multiple resource and binary entries.

The helper preserves its existing contract: bounded reads and copies, regular files and ordinary parents, source/capture identity checks, exclusive capture creation, and exclusive publication of the completed include. It does not replace failed or pre-existing outputs. The include is not an editable installation manifest; its constants are compiled into the installer and uninstaller.

## Initial installation and source preparation

Before dependency installation or any deletion of installed payloads, the installer prepares the full set of new main, resource and external-binary bytes in its fresh NSIS plugin directory. Each staging name must be absent. Extraction uses `SetOverwrite off`, checks the error, and compares the complete staged file with the SHA256 and size from its build capture.

`WriteUninstaller` creates only the fixed new-self staging name there. Windows supplies the long spelling of this newly created plugin directory because its temporary path may use an 8.3 alias. This conversion does not weaken the target guard's rejection of aliases.

The installer-only `MWOWN_OBSERVE_NEW_SELF` mode reuses the existing guard worker. It admits only that current plugin directory and fixed staging filename, reads a nonempty regular file within the 2 GiB minus 1 bound, hashes it through the existing CNG code, rechecks its physical path, and requires successful cleanup. This observation can verify publication of the newly generated source; it is never the authority for deleting an installed self image.

The destination's immediate parent must exist and pass the guard. The installer may then create the destination directory. Every current and predecessor-owned path is classified before any payload is removed. An unrelated unlisted file is left in place. A listed existing file must match the current build or the admitted predecessor exactly; its name or length alone is insufficient.

For a missing or pinned-predecessor payload, the installer creates a new GUID-named private staging directory below the DESTINATION's immediate parent on the same volume. The GUID chooses a name; successful exclusive CreateDirectoryW is required and an existing directory is never adopted. It copies the already verified plugin capture with CopyFileExW and requires an explicit nonzero native result before checking the complete staged size and SHA256 or withdrawing an admitted predecessor. The copy uses COPY_FILE_FAIL_IF_EXISTS | COPY_FILE_COPY_SYMLINK (0x801), so an occupied leaf is refused even if its bytes match or it is a dangling destination symlink. An absence check, hash match or NSIS File SKIPPED result never substitutes for successful exclusive child creation. The canonical executable basename is never the extraction or staging-copy output. Interrupted or failed staging can leave partial bytes in that invocation's private directory. A later explicit invocation allocates a new directory, preserving all earlier stages without reading, adopting, overwriting or deleting them. This also avoids the previous fixed SHA-derived staging-name conflict.

The freshly generated uninstaller is checked and then copied with the same explicit CopyFileExW(0x801) creation contract into another newly created private stage on the destination volume. This replaces the prior CopyFileW staging primitive. Payload staging likewise changes from a second NSIS File extraction to a Win32 copy of the already verified plugin file. Both staging copies require a successful BOOL result, followed by the unchanged staged-byte guard. Neither copy is final publication or a fallback for a failed held-handle rename. After verifying the new source and stage and classifying the old target, both self and application payloads use MWOWN_PUBLISH: a held source handle with SHA256 and size checks and a non-reparse immediate directory handle are kept open across SetFileInformationByHandle(FileRenameInfo). FILE_RENAME_INFO has ReplaceIfExists=FALSE, RootDirectory=NULL and a checked absolute filename under that held immediate parent. The same source handle is checked again after the rename. The guard scans every source-relative parent component, including the private staging directory, using the source path's length even when the target basename is shorter. No second path-based Rename, MoveFileEx, File or CopyFiles fallback is allowed. Outcome uncertainty stops with no retry. Only a confirmed publication with successful guard cleanup permits one nonrecursive removal of that invocation's now-empty stage directory; unexpected contents or a removal failure retain it.

New shortcuts are prepared in the plugin directory and moved without replacement. Existing links are preserved, regardless of their targets. The new link's application model ID is set before publication. The byte guard does not infer ownership of a retained shortcut or rewrite it to repair a renamed executable.

Payload output selection, extraction, installed-byte verification and registration errors stop completion. Registry groups are checked before later size or shortcut helpers can change the shared NSIS error flag. Successful completion is recorded only after the final installation actions. A requested main launch requires a new byte check, but its handle is not retained across process creation.

## Predecessor authority and effective update

The build can provide `MW_NSIS_PREDECESSOR_MANIFEST` and `MW_NSIS_PREDECESSOR_MANIFEST_SHA256` to the existing `tooling/nsis_owned_files.py`. Both must be present, or both absent. The helper reads the environment path directly; the template does not interpolate that path into a shell command. An absent profile grants no predecessor ownership.

The SHA256 pin comes from the build owner's reviewed input, independently of the manifest. The profile is bounded to 1 MiB, has a closed schema and rejects duplicate JSON keys. It identifies one predecessor source commit/tree and package, and is bound to the exact target application, bundle identifier, NSIS architecture, version and captured new-main SHA256.

The `motionwright.nsis-predecessor/1` fields are:

| Field | Required content |
| --- | --- |
| `identity` | Exact `application`, `bundle_id` and `arch` from the target template. Architecture spellings from other receipts require an explicit reviewed mapping. |
| `version`, `source_sha`, `source_tree` | Previous package version and retained Git identities. The helper validates their shape; the build input review establishes their provenance. |
| `target` | Exact target `version` and `main_sha256`. A profile for another candidate cannot authorize this build. |
| `package`, `capture_receipt` | Relative input path, SHA256 and strict integer byte count for each retained file. Both must be nonempty. |
| `files` | Complete reviewed predecessor inventory. Each entry has a canonical relative destination, relative retained source path, SHA256, strict integer byte count, and kind: `main`, `resource`, `external_binary` or `uninstaller`. |

There must be exactly one nonempty root main executable and one nonempty self at `uninstall.exe`. Duplicate or case-aliased destinations, wrong self roles, traversal, streams, device paths and unsupported path spellings are refused. The helper captures and checks the manifest, package, receipt and every listed predecessor source with its existing bounded capture routine. Capture and include names are exclusive; a failed or existing output is not replaced or reused.

The retained package-to-file relationship and completeness of the inventory must be reviewed before approving the independent manifest pin. Hashing a freely supplied receipt does not authenticate its contents. The helper deliberately does not invent this authority. It produces a generated lookup and enumeration include from the approved constants; it does not create an installed runtime manifest, updater, journal or second ownership engine.

The installer classifies the complete union of current and predecessor paths before removal:

| Observation | Action |
| --- | --- |
| Complete current size and SHA256 match | Retain the file as `NEW`; no extraction or deletion. |
| Current path is missing through ordinary parents | Classify `MISSING`; restore a listed current file from the captured new source. |
| Current bytes differ, then complete predecessor size and SHA256 match | Classify `PREDECESSOR`; permit the guarded replacement or retirement of that exact old file. |
| Path, access, identity, read or cleanup failure | Refuse; do not downgrade the result to a content comparison. |
| Bytes match neither admitted version | Preserve and refuse. |

Only `hash_mismatch` and `size_mismatch` after a clean CHECK permit comparison with the previous profile. A common CNG cleanup failure has an explicit preserved result and cleanup-failure reason. The guard attempts each release once. Such a failure cannot retain a mismatch reason and accidentally enable the predecessor comparison.

`MWINST_DeletePrevious` is the only installer-side DELETE caller. Its SHA256 and size come exclusively from the compiled predecessor lookup. It uses the same existing file handle for bounded hashing and disposition. A refused operation stops with status 2; an uncertain close after a disposition stops with status 3, without automatic retry. The new self's observed digest is never supplied to this deletion caller.

A current payload and its same-volume stage are each compared against the reviewed capture before any predecessor withdrawal. After a verified old file is removed, publication uses the same source handle that supplied the new hash, without direct canonical extraction, and a new installed-byte check. Obsolete resources, binaries and prior main names remain subject to the same independently admitted previous-package disposition. Registry OldMainBinaryName never adds authority.

The new installer never executes a previous application `UninstallString`, a previous uninstaller, an MSI uninstall command or a registry-selected application executable. Registration may identify the selected directory and may veto an unsupported directory, version or uninstaller spelling. It cannot authorize bytes. Existing Tauri semantic-version comparison and the configured downgrade policy are preserved. `/UPDATE` enters the same byte checks; it is not an ownership bypass.

The new uninstaller still refuses `/UPDATE`: update is performed by the new installer, which carries the independently admitted predecessor constants. Ordinary uninstall continues to preserve self and uses only its own payload baselines.

## Repair, repeat and interrupted work

An explicit new invocation of the same candidate can retain complete current files, replace exact predecessor files and restore missing current files. This supports a mixed state produced by stopping between complete file operations, provided the full union preflight still succeeds. Existing valid current self bytes and missing payloads can therefore be reconciled without launching the old uninstaller.

Each invocation reports its own outcome. A later successful invocation does not rewrite an earlier failure, cancellation or UNKNOWN. No uncertain operation is automatically retried. There is no multi-file rollback or installed-project schema migration in this template.

An interruption during stage copying may leave incomplete stage bytes, not a canonical partial payload. A later explicit invocation preserves those earlier bytes and uses a new exclusive private stage, including when a legacy SHA-derived staging name is present. It never adopts a stage from another invocation, even if its bytes happen to match the capture. Pre-existing truncated canonical files from earlier installers or unknown publishers remain refused. The interval between predecessor withdrawal and atomic new-file publication can still leave the canonical file missing, requiring a new explicit invocation and retained original failure/UNKNOWN. This naming change does not authorize replay of an uncertain operation: each invocation still requires its own existing entry path and union preflight, with no automatic retry. Neither cancellation rollback, global install atomicity nor power-loss durability is established by this source change.

The supported update profile is a bounded regular-file layout. A file-to-directory conflict is refused by the parent checks. Old empty directories and shortcuts may remain. Project stores, exports and application data are outside the predecessor file inventory and must not be added to grant the installer cleanup authority.

## Uninstall

Before handling a running application or deleting any payload, the uninstaller checks every listed payload against the constants compiled from its build captures. Missing listed files are allowed. A mismatch, reparse point, alias, unavailable file, nonregular file, or other failed verification refuses the deletion phase and retains registration.

For each admitted payload, `MWOWN_DELETE` opens and verifies the same ordinary file handle used for its disposition request. It denies sharing for writers and deleters during that operation, hashes the bounded contents, and checks size and the physical path. Cleanup needed before disposition must succeed. There is no path-based Delete fallback.

A preservation decision stops further deletion and uses exit status 2. A close failure after an admitted disposition produces an uncertain outcome, stops further operations without retry, retains registration, and uses exit status 3. An earlier deletion may already have occurred; this is not a transaction or rollback mechanism. The native gate must distinguish these outcomes from a successful process exit.

Only the main executable, resources, and external binaries with the current installer's byte baselines participate in this cleanup. Foreign files and project/application data are retained. Resource directories are removed only by nonrecursive, nondeferred `RMDir` calls after the payload phase. There is no recursive application-data deletion option.

`uninstall.exe` and shortcuts are retained by ordinary uninstall. Its own complete image cannot carry a self-referential full-file digest, and this template has no baseline for later changes to generated shortcuts. The installation directory therefore remains. A shortcut target or file length does not substitute for byte authority. Retaining these files is a documented cleanup limitation, not proof of complete removal.

After a successful payload phase, the upstream file-association, protocol, and uninstall-registration operations remain. The byte guard is a file-payload policy; it does not introduce a separate ownership system for registry values. Dependency installation retains the Tauri WebView configuration and updater behavior, with fresh plugin-directory staging instead of deleting fixed names in the shared temporary directory.

## Boundaries of the guarantee

The guard closes CHECK and OBSERVE handles, while the new PUBLISH mode verifies one staged source handle, retains the immediate parent handle against namespace replacement, calls FileRenameInfo with that same source handle, and verifies the final handle identity before closing it. Each stage extraction, prior-file withdrawal, publication, registration and launch are still distinct operations. This is NOT a transaction for the whole installed package and does not prove a held-handle verified launch, cancellation rollback or power-loss safety.

A marked deletion is not itself independent evidence of namespace disappearance; another permitted reader can delay it. A native oracle must inspect the actual files after the process exits. Synchronous CNG/file reads are bounded in byte count but have no hard wall-clock deadline. A process timeout remains a separate outcome and cannot be converted to a preservation or cleanup pass.

An installation failure may leave removed predecessors, missing canonical paths, untrusted staged files, created directories or older registration. The new source path avoids direct extraction of partially written new canonical files. The installer never deletes unknown stages to obtain green evidence. NSIS cleans only its plugin directory; destination-volume staged files must be inspected and preserved independently.

## Required verification

The existing seven capture test methods remain. Nine additional synthetic methods exercise predecessor pins, exact target binding, physical package/receipt/file mutations, duplicate or aliased ownership, unsafe paths, missing self, exclusive outputs and the real helper CLI. The positive helper fixture has two resources and two external binaries. It is deliberately not an executable package or an acceptance receipt.

Python tests exercise build tooling. They do not execute NSIS, prove its System-plugin ABI, validate rendered object iterations or demonstrate installation/update/uninstall behavior. The following Windows checks require the actual compiled package, known source and tool identities, bounded process observation, and before/after byte evidence. Outcomes remain pending until that evidence exists.

| Check | Required observation |
| --- | --- |
| Compile and binding | Render the full template with the selected Tauri/NSIS versions. Verify every include/capture pair and `File` source, including two resources and two external binaries with distinct indexes. Retain package, source, tools, approved predecessor manifest and rendered-source hashes. |
| First install and unchanged uninstall | Install into an admitted fresh directory, independently verify payload and self bytes, run that original generated uninstaller and observe unchanged listed payload removal. Observe retained self, shortcuts, foreign files and Project/export data. |
| Same-size changed main/resource/binary | Change one byte without changing length; retain both hashes and never launch modified executable bytes. Uninstall must refuse before the deletion phase, with every observed file preserved and an explicit nonzero outcome. |
| Clean previous-package update | Begin with an independently admitted previous package and complete inventory. Use the new installer and its pinned predecessor profile. Verify every resulting current payload/self and obsolete-path disposition. Confirm no previous uninstaller or registry-selected executable ran. |
| Modified predecessor, including obsolete files and old self | Alter one listed previous file. The union preflight must refuse before any target payload removal. A modified obsolete resource or old uninstaller must not be omitted from this check. |
| Repeat and missing-file repair | Run the same candidate again with complete current files, and separately with a current file missing. Verify retained bytes or exact restoration; include a rename of the previous main in the approved fixture and preserve foreign/Project/export data. |
| Interrupted between complete operations | Retain an exact mixed predecessor/current/missing state and the first operation's original outcome. A fresh explicit invocation may recover only through the same union classification. Observe final bytes independently. |
| Retained partial stage and truncated target | Seed a known partial legacy stage next to an admitted predecessor or missing canonical payload, then run one fresh candidate invocation. Verify recovery through a new private stage while preserving the earlier stage byte-for-byte. Cover main, nested resources, external binaries and self; an earlier partial canonical image must still be refused. Seeded-state recovery does not prove a real interruption or power-loss behavior. |
| Interrupted stage | Interrupt a real installer during same-volume staging: the old canonical stays complete and the stage must never be published. Retain that attempt as a separate outcome and do not automatically replay an UNKNOWN. Independently inspect the exact original old/new/missing and stage states after process exit. |
| Concurrent stage occupant | Inject an independently created equal-byte file, different-byte file, existing-target symlink and dangling-target symlink after the final absence check and before staging. Both payload and self must receive a zero CopyFileExW BOOL and refuse before predecessor withdrawal; preserve the occupant and any link target. Record native error, file identity, bytes and link metadata. Equal bytes alone never authorize adoption. |
| Self publication | Verify generated plugin self and destination-volume stage, retained current and admitted previous self, modified or unrelated self. A target appearing before held-handle FileRenameInfo must be preserved without overwrite or a path-based retry. |
| Path, source and access failures | Exercise regular-file parents, reparse parents/leaves, locked or unavailable files, missing ordinary leaves and a changed prepared source. For PUBLISH, include a long source-parent component with a shorter target basename, both a non-directory parent and an NTFS reparse parent. Inspect the returned result/reason and prove no file disposition occurred. Distinguish missingness from refusal and retain actual before/after observations. |
| Cleanup and timeout | A separately identified native guard harness must exercise a CNG cleanup failure after a mismatch and uncertain close after disposition. Preserve the original outcome, stop without retry and inspect the actual namespace after exit. A process timeout is not a verified preservation result. |
| Cancel/incomplete program closure | Establish the specific recoverable states with the actual package. The source-level clean update, safe refusal or a Python test cannot close all cancellation/incomplete-install requirements. |

No execution result is asserted by this document. Admission must use the actual package and retained observations for the candidate being evaluated.

## Primitive references

- [Windows CoCreateGuid](https://learn.microsoft.com/en-us/windows/win32/api/combaseapi/nf-combaseapi-cocreateguid): generates the private stage name; the GUID does not itself grant ownership.
- [Windows CreateDirectoryW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createdirectoryw): the new stage directory must be created successfully, with no adoption or collision retry.
- [Windows CopyFileExW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-copyfileexw): explicit BOOL result, FAIL_IF_EXISTS and COPY_SYMLINK for both stage copies; destination symlinks are refused with both flags.
- [Windows FILE_RENAME_INFO](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_rename_info): held source handle, checked absolute target path and ReplaceIfExists false.
- [NSIS SetOverwrite](https://nsis.sourceforge.io/Reference/SetOverwrite): extraction behavior for existing files.
- [NSIS compile-time commands](https://nsis.sourceforge.io/Docs/Chapter5.html): Windows `!execute`, symbol substitution and directory predefines.
- [NSIS GetFullPathName](https://nsis.sourceforge.io/Reference/GetFullPathName): explicit long-path conversion is distinct from full-path resolution.
- [NSIS System plug-in](https://nsis.sourceforge.io/Docs/System/System.html): Unicode structure allocation, pointer-size layout and target buffer passed to the real Win32 API.
- [Windows SetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle): disposition through an open handle.


## Disposable-runner known failure classification

A silent NSIS installer reports only its native exit code by default. That cannot distinguish a failed preflight from an interrupted staged write or a rejected same-handle publication. The original clean-update acceptance run 39075887666 ended with known exit code 2; its preserved artifact contained source and project hashes but not the specific `MWOWN_LastReason`. This is a known *unresolved* installed defect, not permission to replay its original installer attempt.

For a **new build on a disposable Windows runner only**, the installer may be compiled with `/DMWINST_CI_DIAGNOSTICS`. That optional compile-time flag emits a fixed-name file `$TEMP\motionwright-nsis-failure-v1.log` when `MWINST_FAIL` executes. The record contains only the closed stage identifier, the guard's static reason and a numeric Windows error captured from a failed native operation, with no absolute paths, tokens or machine identity. A6 must retain the original result, output files, stage bytes and both registry views before collecting the diagnostic. Installed production packages compiled without the flag perform no diagnostic file I/O.

The test-only error record is not evidence of file publication or rollback: actual FileRenameInfo success, uncertain outcome, handle close and prior-file removal remain separate. The source now refuses a non-32-bit NSIS System plug-in at compile time because its FILE_RENAME_INFO structure uses the reviewed 32-bit header layout. A compiled 32-bit helper and a new complete installer/update execution are still required for acceptance. Do not change `PUBLISH_UNCERTAIN` into a known-failed disposition based on the presence or absence of this diagnostic file.
