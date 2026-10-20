# Operational state after portable backup restoration

## Requirement and capability

This component test supports REQUIRED v0.1 CR-PROJECT-06 (a consistent
metadata/blob snapshot and a usable import) and ACC-003 (save, restart and
restore preserve project, scene, branch, revision and blob identities).

The existing seven-case service portability suite previously used a positive
fixture with two assets but no scenes or edited alternate branch. Equality of
those empty collections did not establish operational restoration of the full
project structure. This change strengthens the existing positive test without
changing its name or the other six cases.

Base: 652f32444f3d11e7c1f26c7ea1ff576ddc870233. This is independent of the
exclusive export-publication correction 2c1e41fe14350bd478ea02d9301de75ccd0b29cc.
No product defect or failing product baseline is claimed by this coverage
improvement.

## Real service and storage path

The test
service_inspected_bundle_transfers_real_cas_and_reopens_without_operational_receipts
uses real SQLite databases, Store CAS, StudioService calls and portable bundle
inspection/import. It does not mock persistence or substitute an in-memory
round trip.

1. Ingest two different source files, remove those input files, and build two
   scenes on main. Create a second branch, change the closing scene's objective,
   and move that scene from index 1 to index 0. Return to main and verify its
   original scene state is unchanged.
2. Close and reopen the source service. Export revision 9 with the current
   generation/revision stamp. Compare its document, nine-event
   journal, format, schema and payload digest with the separately captured
   backup. Each export has its own export timestamp.
3. Inspect the complete bundle through a separate empty preflight store before
   removing only the two verified source CAS files. Inspection includes target
   admission, so inspecting against the occupied source correctly returns
   ProjectExists. Require those files to be inside the isolated source directory.
   No production data, unknown path, database or operational process is removed.
4. Open a separate, empty target database/CAS. Inspect and import the exact
   manifest fingerprint. Compare the entire project, normalizing only the fresh
   generation and import timestamp. Require the new generation to be non-nil
   and different. Compare all original journal entries before target edits.
5. Confirm a second import after a known successful result returns ProjectExists
   and leaves one project. Reopen the target; verify the original file-import
   request has no recoverable receipt and both blobs have their exact bytes.
6. Checkout the restored alternate branch and compare its independently saved
   scene state. Edit it, close/reopen again, and compare the complete persisted
   result. Visit main to verify its original state, then return to the edited
   alternate branch and read both target CAS blobs again.

The second import is a controlled duplicate after a confirmed result. It does
not authorize a retry after an UNKNOWN outcome.

## Execution and limits

Run the unchanged seven-case selection on each supported operating system:

    cargo test --locked -p motionwright-service --test project_portability_authority -- --test-threads=1

Source formatting and policy gates are recorded separately from Rust results.
Only a successful run at the resulting exact source SHA can establish GREEN;
older executions of the same test name contain the weaker fixture.

This supports the service/storage component of CR-PROJECT-06 and ACC-003. A
fresh test database is not evidence of a physically installed application,
Studio interaction, platform signing, human acceptance, Semwright/Broker
connection, concurrent publication, power-loss durability, or cross-resource
SQLite/CAS crash atomicity. The receipt assertion concerns the specified
original request; it is not an exhaustive audit of every operational table.

The first coverage source ccd02cf5447bd81f189942b216a6bd070e71d2ce failed
in fixture preparation because its preflight inspector reused the occupied
source store. That was a test setup error, not a product regression. Its
original CI result is preserved; the successor uses a separate empty inspector
and does not change the destination-admission rule.
