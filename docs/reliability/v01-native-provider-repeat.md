# Motionwright v0.1 — two independent real native provider lifetimes

**Scope:** additional GitHub-hosted reliability and evidence collection for issue #49. This is not a fix for Semwright Driver Host generation loss, automatic native recovery, installed-owner first-use acceptance, real-user creative approval or a stable release.

## Opt-in, real-render CI gate

The existing source-pinned **Native Motion Canvas production** workflow may run a second full Motionwright → Semwright Broker/Driver Host → Motion Canvas render after its first independent direct-vs-mediated 60-frame success, without recompiling Semwright twice.

- On the dedicated PR branch feat/v01-provider-repeat-ci, the repeat runs in PR CI for independent proof.
- After integration on main, workflow_dispatch supports the explicit boolean input v01_provider_repeat=true. Ordinary PR/main runs remain a single native E2E to avoid gratuitous capacity/cost.
- Both legs create fresh separate temporary home, project data, Broker socket, ephemeral Semwright daemon, sandbox, private owner session and native render IDs. The repeat is an intentional second test, not a retry of a previous unknown job.
- Each full run independently verifies 60 actual PNG frames with exact source/native SHA, same-source direct-vs-mediated full decoded RGBA pixel parity, driver authority, lifecycle/process metrics and native render receipts. Failure in either one fails the CI job; both roots are retained as exact-source artifacts.
- The repeat writes its evidence into a second exclusive directory GITHUB_SHA-repeat-02; the first source-SHA directory is never overwritten. The only accepted value of MOTIONWRIGHT_NATIVE_RUN_LABEL is repeat-02; arbitrary path suffixes are rejected.

After both real runs, tooling/native_provider_repeat.py reads only bounded source evidence, rejects symlinks/duplicate JSON keys, verifies the file-level bytes/hashes of manifest and sampled PNGs, checks both CI source/Semwright pins and Driver executable digests, rejects provider daemon exit/OOM observed before cleanup, and requires distinct source project IDs and generations. It refuses to equate an incomplete receipt to an independent native result.

If the canonical Film SHA-256 is identical across runs, its entire ordered decoded RGBA digest must also be identical or the run fails. Different generated Film source IDs/digests are explicitly classified as not cross-instance comparable, even if the visible pixels match; no unsound exact-input determinism claim is made.

The report v01-independent-provider-repeat.json records only safe source hashes and fixed states. The status TWO_INDEPENDENT_NATIVE_E2E_RUNS_VERIFIED means these exact two CI render lifetimes passed; it does not prove the intermittent generation-loss bug can no longer recur, or that a real installed-user Broker connection works. No timings are converted into inferred performance savings.

## Failure and operator protocol

A provider Timeout followed by Unavailable must remain an UNKNOWN original render outcome. The harness never sends a replacement render.start for the same job. If one CI lifetime fails, retain both existing per-lifetime failure artifacts and lifecycle counters; do not overwrite the first run or label either incomplete render as accepted.

Repeat only when explicitly requested in CI, not on a personal workstation. A human owner still must verify authenticated original-job reconciliation on an installed desktop using an authorized Broker and Driver Host. Issues #49, #91 and the v0.1 release issue #126 remain open independently.

## Lightweight negative contracts

The fixture-only unit tests under tooling/tests/test_native_provider_repeat.py intentionally use fake bytes and are not native evidence. They check swapped source SHA/pin, changed driver bytes, same owner session, corrupt/altered evidence, daemon exit/OOM, duplicate fields, symlink traversal, exact-Film pixel disagreement and explicit abstention for nonidentical source films.

The actual native pass requires the workflow's real Semwright build, separate live Broker and provider lifetimes and the original decoded-frame parity evidence. A green fixture unit test cannot publish a real render result.
