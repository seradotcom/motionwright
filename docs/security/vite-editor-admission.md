# Vite Windows editor command admission

## Requirement and repair

This bounded dependency repair supports v0.1 CR-SEC-08 candidate security
review and the development shell boundary related to CR-SEC-01/04. It does
not establish the complete installed WebView or renderer acceptance criteria.

GHSA-698v-4r3x-3vcv / CVE-2026-112943 identifies Vite 7.0.0 through 7.3.6 as
affected and 7.3.7 as patched. The vulnerable launch-editor code is bundled
inside Vite, so updating an unrelated launch-editor dependency would not
replace Studio's embedded implementation.

Studio's allowed minimum becomes Vite ^7.3.7 and its exact lock resolves
7.3.7. The npm-generated lock change updates only the root declaration and
the Vite entry; all other dependency entries remain unchanged. This is a
7.x patch update. It does not change the Semwright pin or desktop ABI.

Primary sources:

- https://github.com/vitejs/launch-editor/security/advisories/GHSA-698v-4r3x-3vcv
- https://github.com/vitejs/vite/releases/tag/v7.3.7

## Causal before/after regression

The existing escapeCmdArgs implementation does not change. The fixed
launch-editor 2.14.2 command constructor instead rejects arguments containing
percent signs or line breaks before any child process invocation. The
vulnerable Vite 7.3.6 bundle contains launch-editor 2.14.1.

tooling/vite_editor_security_probe.mjs loads the actual launch-editor regions
from the installed Vite config bundle. It does not replace the admission
algorithm with a copy of the expected implementation. A VM runs those regions
with Windows path behavior, virtual file existence and an explicitly selected
one-token editor. Every child_process primitive is intercepted and never
delegated to the operating system. Unrecognized imports fail.

The ten controls cover:

- Percent environment and command-line-substring tokens: explicit rejection
  and zero intercepted process calls.
- Synthetic carriage-return and line-feed arguments: the same no-effect
  rule. These are argument probes, not legal Windows filesystem fixtures.
- Ordinary paths, spaces, ampersands, carets and line/column arguments:
  unchanged expected command construction and exactly one intercepted call.
- A missing file: no attempted effect.

The authenticated npm 7.3.6 bundle produces six passing controls and four
failing rejection assertions; each failing input reaches the intercepted exec
entry. The authenticated 7.3.7 bundle passes all ten controls. Original logs,
bundle hashes and registry/tarball integrity evidence are retained privately.
Those observations belong to the exact probe/package bytes recorded there;
they are not installed-application executions.

The existing npm test command in apps/studio now runs this probe as its
pretest lifecycle step before the unchanged Vitest suite. This tests the exact
installed lock dependency in the existing Studio CI job without a workflow
change. The build and watch commands retain their existing behavior.

After npm ci in apps/studio, run from the repository root:

    node tooling/vite_editor_security_probe.mjs

For a retained authentic baseline bundle, pass --bundle PATH. For an original
JSON receipt, pass --report PATH; an existing receipt is refused. The JSON
contains the probe/package/lock and actual bundle/region SHA-256, the locked
and installed Vite versions, bundled launch-editor version, each case
observation and final counts. The default mode requires the installed Vite
version to match the lock. An explicit --bundle fixture is labeled separately
and has no claimed installed Vite version. In CI the report also records the
source SHA, run and attempt from the existing workflow environment; original
checkout and source-job records remain authoritative for that identity. Missing or changed module structure fails
rather than reporting a successful empty selection.

## Verification limits

The probe verifies the real packaged control path before an effect. It does
not run cmd.exe, open an editor, create NTFS alternate streams, expose a web
server or prove exploitability from a packaged WebView. The VM is a controlled
test harness for authenticated dependency code, not a general sandbox for
untrusted programs.

Retain the exact-source Studio build/tests and an audit including development
dependencies. An omit-dev audit would exclude this finding. Independent
integration and Dependabot reconciliation remain necessary; a local package
test does not close the alert or grant release approval. Glib #89, provider
lifecycle #49, MLT authorization #91 and distribution #126 remain separate.
