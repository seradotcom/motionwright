# R30 effect-grant boundary

R30 separates privileged application effects at the native desktop boundary. The mechanism is an
application capability, not proof that a human approved an operation.

## Supported local effects

The desktop runtime can mint only these short-lived capabilities:

| Effect | Native operations |
| --- | --- |
| `project_edit` | versioned project mutation through the service |
| `import_local` | local asset/voice import or portable-project import |
| `render_local` | canonical Semwright Native SDK production dispatch |
| `deliver_local` | portable bundle, caption sidecar and OTIO local export |
| `workflow_mutation` | mutating canonical Semwright workflow commands |

A grant is held only in process memory, expires after 45 seconds, is bound to one effect and one
subject, and is removed before validation on first use. Reusing a token, changing its effect,
changing its subject, or changing its project generation/revision fails closed.

Project-scoped grants are issued only against the currently loaded generation and revision. Because
active-branch changes are versioned project mutations, the exact generation/revision pair also pins
the branch state that was current when the grant was issued.

Portable-project import is the single supported unscoped grant: the destination project does not
exist yet, so the token is instead bound to the exact import path and the `import_local` effect.

## Unsupported external effects

The effect vocabulary intentionally includes `remote_egress`, `upload_external`,
`install_runtime` and `publish_external`, but the registry refuses to mint those grants.
Motionwright therefore cannot reinterpret an edit/render/delivery capability as authority to send
data over the network, install software, or publish a release.

Model preflight remains non-dispatching in Studio. Extension permission descriptors remain requests
describing an integration; they are not runtime grants and do not activate optional runtimes.

## Native enforcement

The privileged Tauri commands consume the matching grant before the side effect begins. React
cannot pass a generic "approved" boolean and there is no shared all-powerful desktop permission.
The same token cannot cross from edit to render, render to delivery, or any local effect to an
external effect.

The first-party Studio wrapper requests the narrow local capability immediately before invoking the
matching native command. This is application intent separation. It must not be described as
identity, human consent, organizational authorization, or a security prompt.

## Evidence

The pure Rust registry has regression tests for:

- one-time consumption;
- cross-effect rejection;
- exact project/generation/revision scope;
- exact subject binding;
- fail-closed expiry;
- refusal to issue external-effect grants;
- unscoped authority being limited to local project import.

Security and Delivery CI runs those tests in the desktop boundary job at the exact candidate SHA.
Source policy continues to reject sandbox-disabling flags and unreviewed first-party raw
process/network primitives.

## Residual boundary

R30 does not claim actor identity, organization/tenant authorization, human attestation, external
publishing authority, runtime installation authority, release signing/notarization, or universal
renderer containment. Those require separate product and deployment evidence.
