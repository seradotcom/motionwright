# R29 security review

R29 is a source-and-runtime boundary pass for Motionwright at the exact revision that carries this
document. It does not convert missing deployment evidence into a security PASS.

## Review boundary

The candidate covers the desktop WebView boundary, local project import/export, model-context
preflight, canonical Semwright production execution, dependency advisories, source publishing
policy and CI evidence. It does not claim a cloud tenant service, release signing/notarization,
penetration testing, or universal renderer sandboxing.

## Requirement mapping

| Requirement | R29 evidence | Assessment after this candidate |
| --- | --- | --- |
| CR-SEC-01 WebView mínimo | Strict Tauri CSP, no shell/fs/http/process plugins, source-policy gate | Implemented source boundary; desktop CI still supplies exact-SHA execution evidence |
| CR-SEC-02 Import controlado | Regular-file and symlink rejection, canonical portable-blob paths, byte/digest limits, active SVG controls in storage/service tests | Strong local coverage; ZIP is not a supported portable format and is therefore not claimed |
| CR-SEC-03 Grants por efecto | Model source egress is explicit and network dispatch remains unsupported; canonical production commands are allowlisted | Partial: a universal user-facing grant model for upload/deliver/install is not yet implemented |
| CR-SEC-04 Código cerrado | Production accepts a fixed canonical command set and rejects arbitrary Python/shell-style command names; UI dynamic-code primitives are source-gated | Implemented for the supported production surfaces |
| CR-SEC-05 Aislamiento real | Production executes through the owner-provisioned Semwright Native SDK/Host boundary; source policy rejects sandbox-disable flags and new raw process/network primitives | Partial: each optional renderer still needs its own runtime containment evidence |
| CR-SEC-06 Secrets fuera del proyecto | Source scanning plus portable metadata/text-asset credential scanner; export fails before destination creation and import inspection fails before ingestion | Implemented for bounded supported portable text surfaces; binary decoder content is not scanned |
| CR-SEC-07 Tenant scope | Project/generation/revision checks, branch-anchored history, cache scope fingerprints, and a regression rejecting a foreign-project model resource | Partial: local project isolation is evidenced; cloud organization/tenant actor authorization does not exist in this repository |
| CR-SEC-08 Revisión nueva | This R29 review, exact dependency policy, exact source manifests and candidate-specific CI | Candidate-specific review exists; no external security certification is claimed |

## RustSec policy

Two transitive warnings remain explicitly open in the reviewed Linux desktop dependency graph.
They are recorded in `known-rustsec-advisories.json`. CI requires zero vulnerabilities and the
exact reviewed warning IDs/classes at the exact locked dependency context. Any new or removed
warning, class change, affected first-party API use, or dependency drift requires another review.

This policy is not a waiver and does not relabel an upstream warning as fixed.

## Portable-secret boundary

Portable project metadata is serialized and scanned before an export staging directory is created.
Text-like project blobs are scanned before copy. Import inspection applies the same checks before
blob ingestion. Error messages identify only the boundary that failed and do not echo the matching
secret-shaped value.

The scanner intentionally covers common private-key and credential token shapes. Text-like blobs
larger than the bounded scan budget fail closed instead of bypassing inspection. Binary media is
not interpreted as text and therefore remains outside this scanner; those files are still
content-addressed and digest-verified.

## Residual work

R29 intentionally leaves these items open:

- a complete effect-grant UX and policy model for upload, deliver, runtime install and publication;
- actor/organization/tenant authorization for a future multi-user platform;
- release signing, notarization and reproducible installer verification;
- per-renderer containment evidence for every optional backend;
- production penetration testing and external security review.

Those gaps must remain visible in the acceptance ledger until independent evidence exists.
