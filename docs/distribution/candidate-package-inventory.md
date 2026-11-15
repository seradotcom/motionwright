# Candidate package member observations

The producer/consumer is tooling/candidate_package_inventory.py. Its detached
motionwright.candidate-package-inventory/1 document records every member of the
filesystem roots it actually enumerates, including empty directories, zero-byte
files and symlink literals. An expected file list cannot substitute for this scan.

Version 1 does not claim complete container metadata/member coverage, complete
native or final linker provenance, legal clearance, G2/G3 acceptance or release
authority. These remain explicit gaps. full_package_inventory is always
NOT_VERIFIED; no input switch can promote it.

## Authority and remaining gaps

| Observation | Authority reused or established | Remaining obligation |
| --- | --- | --- |
| Source and candidate | Existing exact source/Cargo readers; candidate receipt rebuilt from original artifact paths and actual bytes | Independent acceptance remains separate; receipt and attestation schemas stay unchanged |
| Extracted roots | Actual complete namespace, file hashes, metadata, empty directories, non-followed symlink literals and second namespace comparison | Original archive headers, duplicate archive entries, extended attributes, resource forks and container program code are not inventoried |
| Tracked source relation | Byte equality to an authenticated tracked file, including Git blob and checkout transformation | This does not prove compilation from that source |
| Captured Cargo root | Byte equality to the executable authenticated by the existing Cargo capture | Transitive native libraries and final static-link incorporation remain unproven |
| Cargo notices | Original index/text hashes, component IDs and all observed members containing those exact text bytes | Presence does not establish location/UI presentation, applicability, compatibility or permission |
| Other native members | Header classification and complete actual file hashes without executing them | Upstream provenance and applicable native notices remain member-specific gaps |
| NSIS generated member | Existing independent static reference and actual installed uninstaller comparison | Other listed member bytes and installer program/effects are not inferred |

Every regular member has a relation row, including unresolved rows. Each
symlink has an internal, external, missing-member, cycle or unresolved-complex
literal relation. Paths with dot components or intermediate symlinks are not
normalized into an invented member relation. The reader
recomputes these relations, component notice joins, counts, member digests,
format comparison and gaps. Edited derived fields or claims are refused.
Caller-provided origin maps, expected rosters and legal assertions are not inputs.

## Bounds and custody

Fixed limits: 4,096 members across observed roots, depth 16, 512 MiB per file
and aggregate observed file bytes, 32 MiB per inventory, and 8 MiB per command
stream. Both DMG volume and copy count toward the aggregate bound. Exceeding a
bound fails without truncation or a partial-success document.

The existing retained reader excludes Windows writers/deleters and compares
the same handle's ChangeTime. Path-stat and descriptor-stat timestamp domains
remain separate. POSIX uses a non-following descriptor and revalidates identity,
size, timestamps and change time; it does not impose a mandatory filesystem lock.
Original namespace metadata stays in the report. Access time is not a stability
predicate because reading can change it.

Plain ancestor directories are required. Other reparse types, special files,
hardlinked regular members, nested filesystem changes and case aliases fail.
Symlinks are recorded without following targets. Output cannot be inside an
observed root or contain one.

The Linux extraction wrapper holds the original candidate descriptor across the
operation, binds tool bytes, creates a fresh external work directory, and retains
exclusive input/request/completion/stdout/stderr originals. The scoped owner in
tooling/candidate_package_extraction.py reuses startup_capture_core.py; the common
run_captured helper is unchanged. Before exec it independently binds the
supervisor and command identities while the exec barrier is closed and retains
both pidfds. Success requires the original subreaper transcript, a terminal
waitid ECHILD proof for all descendants, complete streams, and the exact
supervisor's successful waitid(P_PIDFD) after its retained pidfd becomes readable.
A leader exit or a closed stdout/stderr pair is insufficient.

The original 90-second ceiling includes 88 seconds of operation, one second
for descendant closure/stream joining, and a final second for the exact
supervisor wait. No retry or extended cleanup budget is permitted. Failure
retains original prefixes and failure records; it cannot publish an inventory.
The original kernel transcript is bounded at 128 KiB, each metadata record at
64 KiB, and each command stream at the existing 8 MiB limit. Source, candidate
and tool identities are rechecked before publication. No installed application,
Native host, compiler, download, or second extraction is introduced.

macOS has no equivalent admitted kernel subtree owner in this unit. The new
extract API refuses DMG before creating work or invoking hdiutil. Its existing
workflow remains in place, with package inventory and complete process closure
NOT_VERIFIED. A successful detach or a caller's boolean cannot fill this gap.

## Wiring into the existing workflow

A1 owns package-candidate.yml and release_scope_catalog.json; neither changes
in this unit. Keep the existing source-admitted scopes and tools. Replace each
existing extraction with its wrapper; do not leave the old extraction before
or after it. The output directory must be fresh inside the checkout. The work
directory must be fresh outside the checkout, with an existing plain parent.

Retain input.json, inventory.json and every original process stream/receipt
together. Keep the producer's SHA-256 as the independently retained expected
seal for consumption; do not silently calculate a replacement from changed bytes.

### Linux DEB: one extraction including control members

Replace the current dpkg-deb extraction with:

~~~sh
python tooling/candidate_package_inventory.py extract \
  --kind deb --artifact "$GITHUB_WORKSPACE/$deb" \
  --candidate-receipt "$GITHUB_WORKSPACE/ci-evidence/linux-candidate.json" \
  --work "$work/deb-inventory" \
  --directory "$GITHUB_WORKSPACE/ci-evidence/linux-deb-package-inventory"
~~~

The fixed command is /usr/bin/dpkg-deb --raw-extract ORIGINAL_DEB FRESH_WORK/payload.
This replaces one --extract operation with one --raw-extract operation so data
and DEBIAN control files are observed together. Point the existing binary,
desktop and resource checks at "$work/deb-inventory/payload". Control scripts
are not executed. If that additional observation scope is not admitted, use
observe on the existing data extraction; both missing-control and missing-custody
gaps remain explicit. A DEBIAN/control file does not establish original ar/tar
headers or duplicate-member closure.

### Linux AppImage

Replace the command currently discarding extraction stdout with:

~~~sh
python tooling/candidate_package_inventory.py extract \
  --kind appimage --artifact "$GITHUB_WORKSPACE/$appimage" \
  --candidate-receipt "$GITHUB_WORKSPACE/ci-evidence/linux-candidate.json" \
  --work "$work/appimage-inventory" \
  --directory "$GITHUB_WORKSPACE/ci-evidence/linux-appimage-package-inventory"
~~~

The exact authenticated candidate runs once with --appimage-extract in fresh
work. Continue existing inspection at "$work/appimage-inventory/squashfs-root".
Runtime-prefix and SquashFS-container metadata remain gaps.

### macOS DMG: extraction wiring deferred

Do not replace the existing attach/copy/detach workflow with this new extractor.
The DMG namespace reader and adapter are source-only building blocks until an
equivalent process owner can prove closure for attach, copy and detach. The
remaining obligation is a single read-only mount, whole-volume scan plus
complete copied application comparison, original process streams, and genuine
closure including detach, with the same source/package identities and bounded
failure cleanup. No remount may be used to reconstruct an earlier observation.

The existing flow does not gain inventory credit here. Extended attributes,
resource forks and container metadata remain unverified as before.

### NSIS: reuse the owned install and original static listing

The common wrapper has no NSIS execution profile. After the existing single
owned install, use the adapter:

~~~python
reference = ci_windows_dependency_capture.package_inventory(
    repo, source, run_id=run_id, install_root=destination,
    expected_inventory_sha256=preinstall_inventory_seal,
    directory=repo / "ci-evidence/windows-package-inventory",
)
~~~

It requires the existing static uninstaller proof, revalidates the closed actual
destination, and binds retained listing.stdout to that verified reference.
The new listing view retains every member and original property row, rejects
aliases/traversal even on nonselected members, and preserves unknown solid
member sizes as None. It does not rerun 7-Zip, download or extract another member.

For later consumption call the same adapter with
expected_package_inventory_sha256=reference["sha256"]. The actual destination
and retained originals are reread. Setup member names do not become byte hashes;
installer program/effects and unobserved member bytes stay explicit gaps.

## Detached consumption

~~~sh
python tooling/candidate_package_inventory.py verify \
  --kind appimage --artifact "$GITHUB_WORKSPACE/$appimage" \
  --candidate-receipt "$GITHUB_WORKSPACE/ci-evidence/linux-candidate.json" \
  --directory "$GITHUB_WORKSPACE/ci-evidence/linux-appimage-package-inventory" \
  --expected-sha256 "$retained_inventory_sha256" \
  --root "payload=$work/appimage-inventory/squashfs-root"
~~~

Supplying roots requires a fresh physical scan. Omitting roots reports
RETAINED_ORIGINALS_REVALIDATED, never live readback; omit roots after DMG detach.
The Linux and macOS adapters expose verify_package_inventory without changing
their existing output schemas. Keep the detached reference as its own evidence
member. NSIS should use its adapter to revalidate the original static proof.

observe reads an existing extraction without replaying it and always retains
EXTRACTION_CUSTODY_NOT_OBSERVED. An after-the-fact read cannot acquire the direct
wrapper's custody claim retroactively.

## Focused checks

tooling/tests/test_candidate_package_inventory.py uses actual temporary files
and real Git-backed inert candidate fixtures. It covers namespace/file changes,
bounds, complete empty-member coverage, hardlink/symlink policy, forged claims,
closed relations, package/seal substitution, DMG copy membership, original
process receipts and ambiguous NSIS paths. The only process observation runs
an inert Python text command. These fixtures prove neither package extraction
nor acceptance. The existing test_nsis_static_uninstaller.py checks preserve
the prior reader and generated-member contracts.
