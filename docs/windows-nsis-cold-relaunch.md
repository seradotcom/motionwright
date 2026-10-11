# Windows NSIS installed cold-boot and project persistence

This is an **automated Windows candidate smoke**, not a released or independently accepted v0.1.0 product. It extends the existing GitHub Actions Windows current-user silent NSIS installation with actual first-launch execution and a separate relaunch of the **installed executable**, never `cargo run` or a source checkout build.

The tested source revision is bound to the exact candidate SHA-256 receipt. The check first refuses a tampered installer receipt, changed PE bytes, a wrong source SHA or any claim that humans approved the candidate. It starts the real installed `motionwright-desktop.exe` under a disposable GitHub-hosted Windows runner without an owner Semwright connection. No production render, provider setup or Broker permission is fabricated.

The script independently observes a **visible top-level Win32 HWND titled Motionwright**, owned by the installed process. It also locates a **real committed SQLite project database** in the runner's bounded application-data roots. It opens SQLite read-only, runs `PRAGMA quick_check`, requires exactly one persistent `Untitled Motion Project` row, and validates that the saved JSON contains the same project ID, generation and revision.

It then sends a real window-close message, confirms a clean process exit and starts the **same installed executable again in a second process lifetime**. The title-owned HWND must reappear. The exact SQLite database path, project identity, generation, revision, title and saved document SHA-256 must still match. The process must close cleanly a second time. Neither merely finding a window nor synthesizing a fixture in Python counts as installed-product acceptance.

## Isolation and evidence

The GitHub-only integration stores its own bounded logs under `RUNNER_TEMP` and deliberately retains the **real, disposable hosted-runner Windows account** (`USERPROFILE`, `APPDATA`, and `LOCALAPPDATA`). Redirecting those environment strings to a synthetic profile is not equivalent to Windows Known Folder or WebView2 runtime isolation and can make an otherwise installed application fail to start. The environment removes the optional Semwright connection variable only. The source report records `disposable_runner_profile_appdata` for the normal runner profile rather than claiming artificial isolation. It **refuses preexisting Motionwright project stores** in the inspected runner app-data roots.

The exact-SHA CI artifact is `ci-evidence/windows-nsis-cold-relaunch.json`. It records source SHA, installed binary SHA, fresh and repeated project identity/document digest, real HWND proof, both clean process lifetimes, and the observed app-data location scope. The report makes no claim about a Semwright provider, signing, notarization, commercial publication, a physical-device install, GPU/video playback, usability, or human acceptance. On failure, an independent CI artifact preserves a bounded static failure reason, process exit category, source SHA and a fixed list of recognized log signals **without uploading raw application logs or private paths**. Installed runtime/browser performance and the original 60 acceptance cases remain separate.

`tooling/windows_cold_start.py` cannot start the real executable outside a Windows GitHub Actions runner, and its pure adversarial tests use temporary data **inside the checked-out Motionwright workspace**. No test bypasses native policy, starts paid rendering or modifies a developer's real project store.

Run the fast contract tests with:

```powershell
python -m unittest tooling/tests/test_windows_cold_start.py
```

Heavy packaging and installed launch run only in `.github/workflows/package-candidate.yml`. Treat a failing HWND, store or relaunch check as evidence that the Windows installed-candidate bootstrap is not yet proven; **never promote it to PASS or remove the gate solely to publish v0.1.0**.
