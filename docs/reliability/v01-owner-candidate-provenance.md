# Motionwright v0.1 — owner candidate provenance handoff

The installed-owner acceptance gate requires a real candidate binary linked to
the exact source checkout; an untrusted JSON hash is not enough. The v0.1
owner_candidate_provenance.py tool closes only the ARTIFACT AUTHENTICITY
PREFLIGHT: it verifies an unsigned candidate against its source-bound
CI receipt and GitHub's independently verified Sigstore attestation.

It does NOT install, launch, sign, notarize, trust an OS publisher, authorize
an effect, or mark any private acceptance scenario PASS.

## Operator prerequisites

Use a clean, exact Git worktree at the 40-character Motionwright source SHA
that built the candidate, not whatever main points to today. Obtain the
matching candidate file (Linux AppImage/DEB, Windows NSIS setup.exe, macOS DMG)
and its ci-evidence/<platform>-candidate.json from the SAME completed GitHub
Candidate Packages workflow run. That run must contain a SUCCESS for its
separate, trusted Candidate GH provenance job.

Use a trusted GitHub CLI with network access and access to the original
repository. No arbitrary signer or repository override is accepted, and
GH credential material, stdout and stderr are never published.

For a Windows candidate, run from the clean exact source worktree in
PowerShell (edit local paths for the chosen temporary candidate):

    $sha = (git rev-parse HEAD).Trim()
    python tooling/owner_candidate_provenance.py --source-sha $sha --bundle-kind nsis --artifact "C:\candidate\Motionwright_0.1.0_x64-setup.exe" --candidate-receipt "C:\candidate\windows-candidate.json" --output "C:\private-review\candidate-provenance.json"

The private output parent must already exist outside this public checkout
and must not contain symlink/NTFS junction components. Output is created
EXCLUSIVELY; existing receipts are never overwritten.

The report contains only filename, size, SHA-256, platform/architecture,
source SHA, Native SDK source pin, fixed signer workflow, and explicitly
negative release/installed-owner claims. It never copies absolute owner paths,
session tokens, project refs, GitHub credentials, or raw provider logs.

## Fail-closed admission

All five conditions are mandatory:

1. Match the exact clean tracked local Motionwright Git source SHA and
   its pinned Semwright source revision.
2. Admit the bounded regular-file candidate receipt with exact source,
   platform, version, expected bundle kinds, SHA-256 and unsigned claims.
3. Rehash actual local candidate bytes and refuse symlinks, NTFS junctions,
   nonregular files, corrupt/extra metadata and TOCTOU swaps.
4. Independently invoke gh attestation verify against this exact file with
   the fixed repo seradotcom/motionwright, fixed signer workflow
   .github/workflows/package-candidate.yml, and exact source digest.
5. Rehash the artifact and receipt and recheck their physical identity and
   source checkout after GitHub verification to catch mid-verification swaps.

Missing GitHub attestation, wrong signer or digest, changed source, false
receipt, unsafe local file or GH CLI failure MUST fail. This is not an atomic
filesystem transaction; a malicious host, compromised GitHub CLI or
untrusted trust root cannot be independently authenticated here.

## Independent work remaining

The owner still has to INSTALL and EXECUTE the candidate with OS protections
enabled, validate the real Semwright Broker/Driver Host with genuine native
effect grants, inspect real audio/video, and obtain separate human reviewers
for all 60 cases from the original private acceptance specification.

The private review files remain outside GitHub. Even a VERIFIED package
provenance does NOT make v0.1 release-ready; issues #49, #89, #91 and #126
remain independent and OPEN.
