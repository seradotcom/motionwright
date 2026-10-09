# Automated inspection of unsigned desktop candidate packages

Motionwright's Candidate Packages workflow builds release-shaped packages from one exact Git revision on disposable GitHub-hosted Linux, Windows and macOS runners. The original `package_receipt.py` records the source SHA, pinned Semwright Native SDK revision and SHA-256 of every generated installer.

The additional **post-build candidate smoke** tests validate what the installers contain—and whether the Linux AppImage can actually create an X11 application window—rather than treating successful bundling alone as a working desktop app.

## What CI checks

| Candidate | Isolated package operation | Verified evidence |
|---|---|---|
| Linux Debian `.deb` | `dpkg-deb --extract` into a fresh runner temporary directory, without installing a system package | Exactly one installed executable and desktop entry; source archive SHA-256, correct 64-bit x86 ELF, desktop name/Exec identity, executable digest |
| Linux `.AppImage` | Runtime-independent `--appimage-extract` into an isolated temporary directory | Exact AppImage SHA-256, contained executable/desktop entry, ELF architecture, internal AppRun path; a rootless **Xvfb** run creates an actual window titled **Motionwright** |
| Windows NSIS `-setup.exe` | Real NSIS **silent current-user installation** to a new temporary install directory on the disposable Windows runner | Installer SHA-256, expected installed app executable and 64-bit x86 Windows PE header, executable byte count/hash; no admin-level install |
| macOS `.dmg` | Read-only DMG mount, `ditto` copy of the contained `.app` into the disposable runner's temporary directory | Exact DMG SHA-256, unique app bundle, exact `CFBundleIdentifier`, matching `CFBundleExecutable` and correct Mach-O machine architecture |

All smoke receipts are tied to the **same exact workflow `GITHUB_SHA`** used in the original candidate receipts. A mismatched installer, altered checksum, wrong binary architecture, substituted Desktop Entry or `Info.plist`, or unreviewed publication/signing claim fails the pipeline.

The Linux GUI probe uses a disposable Xvfb server, isolated XDG config/data/cache directories, no owner Semwright connection and no root privileges. It only reports success if the app process survives long enough to create a real window titled Motionwright. The process and child group are terminated after the bounded probe. **No WebKit sandbox or OS protection is disabled to make the test pass.**

## Separate, intentionally unclaimed gates

Passing these jobs does **not** prove:
- that a user can install, launch and update the packages on a supported physical device;
- that the Tauri WebView can render video/audio correctly on every Windows, Linux or macOS configuration;
- that a Semwright Broker/Driver Host or any real creative renderer is installed, connected or executing work;
- that macOS binaries are signed/notarized, that Windows Authenticode verification passed, or that the packages were publicly released;
- that an installer can uninstall cleanly in every end-user environment;
- that the 60 independently reviewed product acceptance scenarios have been executed.

The macOS smoke **does not launch** an unsigned app and never alters Gatekeeper settings. The Windows job tests current-user installer output and executable identity, but is not a GUI/codec playback test. The Linux Debian job extracts without root rather than changing runner-wide dpkg state; the Linux AppImage receives the stronger automated GUI window probe.

Each new `ci-evidence/*-smoke.json` records `automated_inspection.status=PASS` **only** for the checks actually performed. The accompanying `claims.human_install_acceptance` is always `NOT_RUN`, `signing` and `notarization` remain `NOT_CLAIMED`, `release_published=false`, and renderer runtime acceptance is `SEPARATE_GATE`. A green workflow is technical **CI evidence**, never independent end-user product approval.

## How it is enforced

`tooling/candidate_smoke.py` refuses to consume archive bytes different from the build receipt, verifies source revision against the caller-provided exact GitHub SHA and validates platform-specific executable headers without invoking arbitrary external commands. Its 8 adversarial Python unit tests run in the fast `receipt-policy` job, before any heavyweight packages are built. The additional isolated Linux GUI probe lives in `tooling/linux_gui_probe.py`.

The real package checks run only after the corresponding `tauri build` and candidate receipt creation. Their SHA-256 findings are uploaded alongside the existing unsigned installers and source-bound candidate receipt, without changing the release/tag/update policy. A failed smoke gate retains the build evidence in the workflow logs; it must be corrected or documented, never replaced with a fabricated PASS.

For actual independent validation, use [private acceptance intake](acceptance/REVIEW_INTAKE.md) on explicitly chosen installed builds, with human reviewers and original private acceptance criteria.
