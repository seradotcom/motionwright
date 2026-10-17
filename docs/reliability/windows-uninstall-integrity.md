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

For a missing current payload, the installer repeats the absence check, extracts from the same captured build input with `SetOverwrite off`, checks the extraction error, and compares the resulting file with its captured SHA256 and size. A complete current match is retained without extraction. A late file with the exact admitted current bytes can satisfy the comparison without having been created by this invocation; the template does not issue a creation receipt.

The fresh self is checked again before publication. If a self image already matches that fresh source, it is retained. If it matches the independently pinned predecessor self, the installer withdraws it through the same-handle guard before publishing the new source. `Rename` is used without `/REBOOTOK`, with explicit destination absence, error and final hash checks. There is no overwrite, CopyFiles, Delete or retry fallback. The operation is required to fail when its destination exists.

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

A current payload is compared again immediately before its replacement. The new source must still match its prepared bytes. After withdrawal of an admitted previous file, the target must be absent, extraction must succeed and the installed bytes must match the current capture. Obsolete prior resources, external binaries and former main names also participate in the initial union preflight and later guarded removal. `OldMainBinaryName` from the registry supplies no extra paths.

The new installer never executes a previous application `UninstallString`, a previous uninstaller, an MSI uninstall command or a registry-selected application executable. Registration may identify the selected directory and may veto an unsupported directory, version or uninstaller spelling. It cannot authorize bytes. Existing Tauri semantic-version comparison and the configured downgrade policy are preserved. `/UPDATE` enters the same byte checks; it is not an ownership bypass.

The new uninstaller still refuses `/UPDATE`: update is performed by the new installer, which carries the independently admitted predecessor constants. Ordinary uninstall continues to preserve self and uses only its own payload baselines.

## Repair, repeat and interrupted work

An explicit new invocation of the same candidate can retain complete current files, replace exact predecessor files and restore missing current files. This supports a mixed state produced by stopping between complete file operations, provided the full union preflight still succeeds. Existing valid current self bytes and missing payloads can therefore be reconciled without launching the old uninstaller.

Each invocation reports its own outcome. A later successful invocation does not rewrite an earlier failure, cancellation or UNKNOWN. No uncertain operation is automatically retried. There is no multi-file rollback or installed-project schema migration in this template.

An interruption during target extraction can leave a truncated or otherwise unrecognized file. Such bytes are preserved and refused on the next invocation; `MISSING` is not inferred from an incomplete file. Automatic recovery of that state is a remaining engineering task. It requires an independently verified publication strategy or separately authorized disposition, not a filename-based cleanup. The clean-update and missing-file repair paths do not establish universal cancellation or incomplete-install recovery.

The supported update profile is a bounded regular-file layout. A file-to-directory conflict is refused by the parent checks. Old empty directories and shortcuts may remain. Project stores, exports and application data are outside the predecessor file inventory and must not be added to grant the installer cleanup authority.

## Uninstall

Before handling a running application or deleting any payload, the uninstaller checks every listed payload against the constants compiled from its build captures. Missing listed files are allowed. A mismatch, reparse point, alias, unavailable file, nonregular file, or other failed verification refuses the deletion phase and retains registration.

For each admitted payload, `MWOWN_DELETE` opens and verifies the same ordinary file handle used for its disposition request. It denies sharing for writers and deleters during that operation, hashes the bounded contents, and checks size and the physical path. Cleanup needed before disposition must succeed. There is no path-based Delete fallback.

A preservation decision stops further deletion and uses exit status 2. A close failure after an admitted disposition produces an uncertain outcome, stops further operations without retry, retains registration, and uses exit status 3. An earlier deletion may already have occurred; this is not a transaction or rollback mechanism. The native gate must distinguish these outcomes from a successful process exit.

Only the main executable, resources, and external binaries with the current installer's byte baselines participate in this cleanup. Foreign files and project/application data are retained. Resource directories are removed only by nonrecursive, nondeferred `RMDir` calls after the payload phase. There is no recursive application-data deletion option.

`uninstall.exe` and shortcuts are retained by ordinary uninstall. Its own complete image cannot carry a self-referential full-file digest, and this template has no baseline for later changes to generated shortcuts. The installation directory therefore remains. A shortcut target or file length does not substitute for byte authority. Retaining these files is a documented cleanup limitation, not proof of complete removal.

After a successful payload phase, the upstream file-association, protocol, and uninstall-registration operations remain. The byte guard is a file-payload policy; it does not introduce a separate ownership system for registry values. Dependency installation retains the Tauri WebView configuration and updater behavior, with fresh plugin-directory staging instead of deleting fixed names in the shared temporary directory.

## Boundaries of the guarantee

The guard closes CHECK and OBSERVE handles. A later `CreateDirectory`, `File`, `WriteUninstaller`, `Rename`, or process launch is a separate operation. Rechecking a path does not hold every ancestor against replacement through that later operation. The template does not claim an atomic installation, a held-handle verified launch, cancellation rollback, or complete protection against a hostile concurrent namespace change. Same-handle DELETE has a narrower, explicit byte/disposition binding.

A marked deletion is not itself independent evidence of namespace disappearance; another permitted reader can delay it. A native oracle must inspect the actual files after the process exits. Synchronous CNG/file reads are bounded in byte count but have no hard wall-clock deadline. A process timeout remains a separate outcome and cannot be converted to a preservation or cleanup pass.

An installation failure can leave newly extracted or partially extracted payloads, removed predecessors, retained staging files or newly created directories. The template does not delete unknown partial outputs to make a test pass. NSIS may clean its own plugin directory on exit, so an absent staging file after failure remains absent; it must not be reconstructed and presented as retained evidence.

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
| Truncated target | A partial target must remain a refusal with exact bytes retained. This negative is not a positive recovery claim; automatic handling remains a separate engineering task. |
| Self publication | Exercise a matching current self, a pinned predecessor self, modified self and an unrelated existing self. Inspect new staged and published bytes. A destination appearing at Rename must be preserved with an error and no overwrite/deletion fallback. |
| Path, source and access failures | Exercise regular-file parents, reparse parents/leaves, locked or unavailable files, missing ordinary leaves and a changed prepared source. Distinguish missingness from refusal and retain actual before/after observations. |
| Cleanup and timeout | A separately identified native guard harness must exercise a CNG cleanup failure after a mismatch and uncertain close after disposition. Preserve the original outcome, stop without retry and inspect the actual namespace after exit. A process timeout is not a verified preservation result. |
| Cancel/incomplete program closure | Establish the specific recoverable states with the actual package. The source-level clean update, safe refusal or a Python test cannot close all cancellation/incomplete-install requirements. |

No execution result is asserted by this document. Admission must use the actual package and retained observations for the candidate being evaluated.

## Primitive references

- [NSIS Rename](https://nsis.sourceforge.io/Reference/Rename): destination existence and error behavior without `/REBOOTOK`.
- [NSIS SetOverwrite](https://nsis.sourceforge.io/Reference/SetOverwrite): extraction behavior for existing files.
- [NSIS compile-time commands](https://nsis.sourceforge.io/Docs/Chapter5.html): Windows `!execute`, symbol substitution and directory predefines.
- [NSIS GetFullPathName](https://nsis.sourceforge.io/Reference/GetFullPathName): explicit long-path conversion is distinct from full-path resolution.
- [Windows MoveFileW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefilew): nonexisting destination contract.
- [Windows SetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle): disposition through an open handle.
