# Fast Graph development checks

This command verifies source-only contracts in seconds. It does **not** run
the actual Graph6 Native Host, edit SQLite, issue R74 status or grant G2/G3.

```sh
python -B tooling/ci_graph_dev_fast.py --repo . --base HEAD^ --mode fast
```

The first check compares the Cargo producer with the exact captured
`semwright`, `semwrightd` and `semwright_sandbox` executable roster under
`target/mw-r74-captured`. One inert positive and nine tampering negatives
run before the small Python and Node source tests.

Use `--mode plan` to inspect affected paths without running any checks.
Use `--mode impact` to include affected Rust and Studio UI checks. Rust
requires `--cargo-target-dir` pointing to an **existing** hot target and
always stays offline and locked. Missing hot dependencies produce HOLD,
not a silent PASS; targets and worktrees are never created automatically.

The source-only `.github/workflows/graph-development-fast.yml` runs on
Graph-related pushes to the existing integration branch, using the exact
push SHA and the previous commit as its comparison ancestor. It never
issues R74 statuses or runs the Native owner. On the default branch,
the same workflow also supports manual dispatch with explicit SHA and
ancestor inputs. Superseded source-only runs may be cancelled; a missing
ancestor fails closed and does not trigger a full Graph acceptance job.

`PASS_SOURCE_ONLY` is not Native acceptance; `PASS_DEV_WIP_SOURCE_ONLY`
is not a committed source attestation. The authentic `graph-source-review`
and remaining five cases must still pass with real Host/Store and a
separately authenticated same-run R74 selection. Prior RED/UNKNOWN
outcomes are preserved and never replayed or marked accepted.
