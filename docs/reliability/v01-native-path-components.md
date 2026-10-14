# Motionwright v0.1 — native owner path component boundary

**Scope:** Validation before using owner-selected Semwright CLI, socket,
session file, production connection JSON and output-root media. No native
render command, effect grant, provider restart or SDK source pin is changed.

## Corrected gap

The old production-connection reader called symlink_metadata on **only the
last path component**. A regular config, executable, session or socket under
a symlinked parent could pass the check. Likewise, verified media resolution
canonicalized the owner output root **before** checking whether that root
was redirected, and checked descendants for symbolic links without rejecting
all Windows NTFS junction/reparse forms. This left room to follow a mutable
intermediate path that did not belong to the owner's declared filesystem
location.

The updated v0.1 production reader walks each absolute path component,
refuses dot/parent traversal, arbitrary symbolic links and, on Windows, any
filesystem component with FILE_ATTRIBUTE_REPARSE_POINT. On macOS, only
three fixed system aliases (/tmp, /var, /etc) may be normalized, and only
when they resolve exactly to the respective /private/tmp, /private/var and
/private/etc directories through an unlinked /private parent. This preserves
standard macOS temporary paths without allowing arbitrary user links. Intermediate components must
be directories. The prior Unix permissions check on the selected final
component remains enforced; no new global chmod policy is introduced for
shared system ancestors. Before resolving an original verified media path,
the owner output root is rechecked by this same component-aware guard.
The result is still bounded by the native content digest and size. Final
media descendants also reject symlinks and Windows reparse points before
being canonicalized and digest-verified.

## Tests

Cross-platform native crate unit fixtures check that ordinary absolute
owner files and digest-bound media still work, while non-normalized paths
and swapped parent directories fail closed. Linux tests use real symlinks
at both config and media intermediate parents. Windows tests use an actual
NTFS junction created through the OS's mklink /J command: the junction has
the reparse attribute and its removal does not delete the original fixture.
The same rejection path is exercised through ProductionConnection::load
and the public digest-bound read helper. The Native SDK GitHub Actions workflow
prepares the junction using the **trusted Windows runner shell** and injects
only its disposable fixture paths into a separately selected ignored Rust
unit test. Rust code never gains a new process-launch primitive, preserving
the existing source-policy boundary. The fixture is removed by CI, not by
the source SDK. A separate macOS Native SDK job exercises the original
system aliases and arbitrary-link rejection on a real macOS runner.

Tests are non-destructive filesystem fixtures. Heavy Rust/native integration
and platform smoke stay in exact-head GitHub Actions; a successful unit suite
cannot by itself prove a live installed owner's Broker identity.

## Remaining authority limits

This is component validation at inspection time, **not** a race-free
handle-bound openat/NT API guarantee against a concurrently malicious
filesystem writer. Owner runtime trust, effect grants, immutable binary
provenance and native provider generation remain separate checks.
Known intermittently inactive provider generations (#49), foreground
authorized native project cleanup (#91), glib GTK/WebKit advisory (#89),
and external 60-case human acceptance/release authorization (#126)
remain OPEN. Do not claim these are resolved by path preflight.
