# Acceptance ledger

This directory tracks implementation and acceptance without publishing the private specification text.

The private source package defines 208 requirement IDs and 60 product acceptance IDs. Motionwright keeps only identifiers, status and evidence pointers here. A green build is never converted automatically into product acceptance.

## Baseline

- repository: seradotcom/motionwright
- implementation PR: #12
- tested PR head: 744facffc43a6a9c1e0b314306df9bf40d3321e9
- merge on main: 930c5f089db3bc50cf27ecd779dc1976950be8a1
- exact Semwright pin remains governed by SOURCE_LOCK.json

## Status model

EVIDENCE_PRESENT means source/test evidence exists for the implementation area. It does not mean every criterion in that area has passed product acceptance.

PARTIAL_OR_GAP means at least one relevant implementation path exists but unreviewed or known gaps remain.

BLOCKED_UPSTREAM is reserved for criteria that cannot be satisfied honestly on the current canonical upstream capability set.

Every product acceptance case begins as NOT_RUN. PASS requires non-empty evidence and independent=true; the policy checker rejects self-certified PASS entries.

## Known open evidence

- Native Manim execution/readback/jobs are blocked until a canonical Semwright Manim driver exists. The declarative compiler is not called a native renderer.
- Canonical Graph/Effects consumer compatibility is green, but that is not artifact-level Graph admission or a product Effect Conformance PASS.
- Workflow Distillation V1/V2/V3 is implemented behind the canonical Broker/Policy boundary, but live product-level recorder -> compile/proposal -> replay -> promotion acceptance evidence is still required.
- Production/performance datasets, three-project coverage and revision-ten maintenance acceptance remain separate from unit/contract CI.
- Platform/account/worker/remote-review acceptance remains separate from the local workstation boundary.

## Policy

Run python3 tooling/acceptance_policy.py.

The policy enforces exact ledger cardinality and IDs, evidence-path existence, and prevents VERIFIED or product PASS without evidence. Product PASS additionally requires independent review.
