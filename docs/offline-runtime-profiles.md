# Install an offline runtime profile

Motionwright Desktop can inspect and install an owner-prepared local runtime package from **Integrations → Offline runtime profiles**. The inspection shows the files, their size, available destination space and the exact destination before an explicit installation. The original operation survives closing the application, and rollback preserves the installed files in a disabled folder.

## Supported package layouts

| Profile | Runtime version | Platform | Declared Node version |
| --- | --- | --- | --- |
| Motion Canvas | 3.17.2 | Linux x86_64 | 22.22.0 |
| Motion Canvas | 3.17.2 | macOS Apple Silicon (arm64) | 22.22.0 |
| HyperFrames | 0.8.181 | Linux x86_64 | 22.22.0 |

The supported entry and lock identities follow the current Semwright source pin `c2c40c74e5714a3e680415d0dddcf27743f0f0fa`. Motion Canvas uses the exact pinned `render.mjs`, `package.json` and `package-lock.json`. HyperFrames uses the existing reviewed runner and its closed runtime lock, including the original distribution metadata, Node digest and dependency roster.

The application inspects bytes and layout. It does not execute a version probe during installation, establish executable provenance from a declared version, start a provider or grant a Native render. A separately authorized Semwright Host still validates its runtime, mounts, resource, executable identities and effects. The corresponding installed operating system and provider checks remain necessary. Browser system libraries and fontconfig remain separate operating system dependencies; an npm lock and this file copy do not supply that system closure.

On other operating systems or architectures, this offline installer reports that no profile is admitted and offers local authoring or an existing supported owner connection. This does not imply that the Desktop application itself is restricted to Linux, or that a Linux native runtime is usable on another platform.

On macOS Apple Silicon, the Motion Canvas package must contain native thin ARM64 Mach-O Node and Firefox executables. Firefox uses the contained `firefox/Nightly.app/Contents/MacOS/firefox` layout. Intel, ARM64e, universal binaries and HyperFrames packages are not admitted by this initial Mac copy profile. The copier reads extended ACLs and attributes through retained file descriptors and refuses metadata it cannot preserve. It never removes quarantine, provenance, resource forks or other extended attributes, and never changes an ACL to make a package pass. A security metadata read failure also stops the copy. The original package remains available for its owner to manage through the normal operating system controls.

## Prepare a package from an existing runtime

Use a separate, owner-controlled package folder containing:

- `tools/node`: the actual Node executable to use with this runtime.
- `runtime/`: the complete already prepared runtime and its dependencies, including its browser and original lock. Motion Canvas's contained Firefox executable must be below `runtime/node_modules/playwright-core/.local-browsers/`. HyperFrames's `runtime/runtime-lock.json` must already describe the exact closure, including the browser, FFmpeg and ffprobe.

Use only files you are authorized to install. This workflow does not obtain redistribution rights or accept third-party license terms on your behalf. The package folder must contain regular files and owner-controlled directories, without symbolic links, hardlinked files, private credential files, unlisted files or empty directories. Preserve the original source separately when preparing the package.

From the Motionwright source checkout on the matching supported host, create its description explicitly:

```bash
python3 -B tooling/runtime_profile_bundle.py \
  --profile motion_canvas \
  --folder /absolute/owner/runtime-package
```

Use `--profile hyperframes` on Linux for that supported profile. The utility reads the existing files and creates `runtime-profile.json` exclusively. It refuses an existing description. It does not download packages, run npm, execute the runtime, change a dependency lock, configure a Host or open a session.

The description includes each file's relative path, byte length, SHA-256 and intended executable mode. The Desktop independently checks the description and original files again; the description alone is not installation or execution authority. Current limits are 30,000 payload files, 8,192 directories, 512 MiB per payload file and 2 GiB of total payload, with an 8 MiB description limit. Required destination space also accounts for filesystem overhead and a reserve.

## Inspect and install in Desktop

1. Open a saved project and choose **Integrations → Offline runtime profiles**.
2. Choose **Choose offline runtime package**. The operating system folder picker selects the local source. The webview does not supply a source or destination path.
3. Review the profile, file count, size and destination. Inspections last 15 minutes. Changing the saved project invalidates an earlier installation decision. Choosing another package clears the previous visible review; inspecting the new selection retires this project's older unsent preparation, including after an invalid selection. Other projects' preparations and durable installations are preserved.
4. Choose **Install reviewed local copy**. This requests one exact local permission, records the original operation in SQLite, creates a new private destination and copies the declared files without replacement. Each file is checked, and the completed copy is read back before it is reported as verified.
5. Expand **Paths for the separately authorized provider** to copy the verified Node, entry and lock paths or the lock digest. Configure those paths through the existing owner-controlled provider setup. Existing [connection selection](owner-native-runtime-onboarding.md) remains a separate step.

For a saved HyperFrames composition, choose **Read installed HyperFrames profiles** in the native PNG rendering panel, then **Use selected HyperFrames profile**. The panel verifies the original installation and selects its runtime lock without copying a digest. Missing or changed files cannot enable rendering, and a retained rollback blocks dispatch even if a previous render permission arrives late. The selected Semwright connection must already use the same runtime. An existing owner-provisioned runtime remains available under **Use a separately provisioned runtime**.

Opening a project, reading the inventory or running the creative runtime doctor never triggers a runtime download or installation. Installing a package does not change the project's revision, its sources or the selected owner connection.

## Recovery and rollback

The browser retains the exact operation before requesting permission or dispatching it. The backend records the original intent before copying package bytes. A known rejection before admission, such as an expired grant, is reported distinctly from an unconfirmed admitted operation and permits a new explicit selection.

If a response is lost, reopen the project and choose **Read original runtime outcome** or **Read original files**. These commands read the original SQLite operation and its original directory. They do not repeat installation, rollback or rendering. Inventory entries labelled **Copy completed** describe the retained completion record; **Installed · original files verified** follows an actual read of the current files.

A partial copy is preserved. Use **Roll back and keep files** to move the original installed directory into its unique disabled location under the same private runtime area. The original package, other installations and project data remain unchanged. An existing rollback destination is never replaced. A completed rollback cannot be repeated, and a lost rollback acknowledgement can be recovered from the original retained directory identity without another move.

Rollback does not terminate a provider that was already running or rewrite independently configured Host paths. Stop or reconfigure that provider separately. If the original directory identity cannot be established, the operation remains unconfirmed for inspection; an unreadable path is not treated as permission to reconstruct, overwrite or delete files. Local project editing remains available.

## Verification scope

The source checks exercise the actual Desktop copier, original SQLite records, close/reopen, independent package description utility, scope and one-time grants, symlink/hardlink/FIFO rejection, directory replacement, changed bytes, lost replies and preserved rollback files. Mac checks additionally exercise native architecture refusal, extended ACLs, quarantine/other attributes and exclusive rollback collisions. Native executable inputs in those filesystem tests are explicitly inert byte fixtures. Browser interaction tests use synthetic IPC. Those checks do not claim an installed OS smoke test, actual runtime execution, provider admission, human acceptance or release authorization.
