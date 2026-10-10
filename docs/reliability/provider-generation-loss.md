# Native Motion Canvas provider-generation loss (issue #49)

**Status: open.** This change hardens Motionwright's response when a previously accepted native render loses its status provider; it does not fix the pinned Semwright Driver Host lifecycle.

## Observed source evidence

The real [AV master CI failure](https://github.com/seradotcom/motionwright/actions/runs/37856456507) on Motionwright SHA `b7ff2d5a7e02a0a798cb3438ec11be3878de29b6`, against exact Semwright pin `8fa191250ae68274182570c65f067f7a60f85625`, contained this chronological receipt sequence: render.start acknowledged; render.status reported rendering; subsequent read-only status returned Timeout with `outcome_known=false`; next status returned Unavailable with message "Provider capability generation is inactive". The native render did **not** return a verified terminal result. The runner's daemon log showed normal Broker startup but no conclusive provider exit cause. A rerun later passed, which does not establish a permanent fix.

Previously, Motionwright propagated the last read-only `Unavailable/outcome_known=true` to the overall render operation. **The observation failure is known; the started render outcome is NOT.** Production Jobs could also keep the last `rendering` state after a failed known status read. Neither is safe to represent as a current confirmed state.

## Runtime correction and recovery

Following an acknowledged `driver.motion-canvas.render.start`, an unsuccessful status observation propagates the original typed error code but returns `outcome_known=false` for the **render operation**, retaining recipe progress that a start was acknowledged. Only read-only Timeout statuses can be retried inside the existing bounded poll deadline. Provider-generation Unavailable, stale, malformed or permission errors never trigger a new mutation, replacement render, arbitrary process reinitialization or a fabricated successful receipt. Confirmed terminal failed/cancelled responses remain known; actual SUCCESS still requires native result, verification and artifact SHA-256.

Production Jobs now marks a nonterminal job `outcome_unknown` after a failed status/result observation, even if that *read* failed with `outcome_known=true`. An already-proven terminal result remains terminal; a later authenticated result for the **same** native job may reconcile an earlier unknown. No unrelated job result can establish success.

**Operator procedure:** retain the private original project/generation/revision, request receipt and job reference; reconcile the **same** job with the canonical Broker/Driver Host under authorized original source scope. If that provider generation no longer exists, report UNKNOWN and do not retry mutations automatically. A file or manifest on disk without native result/verified bytes is not sufficient. Any independently authorized new render must be a distinct explicit operation after reconciliation.

## CI diagnostic artifacts

The exact-SHA native Motion Canvas → MLT AV workflow now writes `native-provider-lifecycle.json`: passive two-second samples of the ephemeral CI daemon's descendants, whitelisted process roles, PID and parent PID, RSS/high-water memory, numeric cgroup memory/OOM counters, and daemon exit code/signal **before deliberate test cleanup**. This reads no process argv, environment, cwd, credentials, session files, media, or user workstation files. An OOM delta or disappearing PID is **correlation evidence, not proof of causation**.

If a render diagnostic is present, the runner writes `provider-status-reconciliation.json` containing only typed status/error codes, timestamps, ordered stages and a cautious render conclusion. It checks job identities in memory but **never serializes** private job references, raw provider payload, file paths or session tokens into this summary. The original private evidence may be retained separately for authorized investigation. Both records are CI artifacts, not native product telemetry or public monitoring.

Five lightweight adversarial Python tests cover role isolation, redaction, parent/child sampling, memory counters, lifecycle summary and the synthetic Timeout→Unavailable sequence, including refusal to treat another job's result as confirmation. Rust tests cover uncertain render outcome, stable request replay/no duplicate render-start and honest job projections. Full pinned renderer and AV E2E CI remains mandatory; these tests are not the 60 independent product acceptance cases.

**Outstanding:** determine why the pinned Driver Host generation disappeared (provider process exit, sandbox/Firefox resource pressure, lifecycle timeout or Broker behavior) using a genuinely instrumented recurrence. Do not close [issue #49](https://github.com/seradotcom/motionwright/issues/49) or upgrade Semwright's immutable pin speculatively.
