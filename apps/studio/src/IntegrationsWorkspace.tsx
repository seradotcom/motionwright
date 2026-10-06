import { CircleCheck, CircleDashed, LockKeyhole, Package, ShieldCheck, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import type {
  Bootstrap,
  Change,
  ExternalResourceKind,
  ExtensionKind,
  ExtensionProfile,
  HandoffBinding,
  HandoffDirection,
  Project,
  RightsStatus,
} from "./types";

const kindLabels: Record<ExtensionKind, string> = {
  "remotion-renderer": "Remotion renderer",
  "manim-gl-renderer": "ManimGL renderer",
  "generative-assets": "Generative assets",
  "catalog-package": "Catalog package",
};

const newDraft = (): ExtensionProfile => ({
  id: crypto.randomUUID(),
  name: "Reviewed optional package",
  kind: "remotion-renderer",
  package_version: "",
  digest_sha256: "",
  license: "",
  source: "",
  rights_status: "unknown",
  enabled: false,
  permissions: ["read_project"],
});

function BoundaryRow({
  name,
  status,
  body,
  tone = "unknown",
}: {
  name: string;
  status: string;
  body: string;
  tone?: "current" | "unknown" | "stale";
}) {
  return (
    <div className="integration-boundary-row">
      <div>
        <strong>{name}</strong>
        <span>{body}</span>
      </div>
      <span className={"status-pill status-" + tone}>{status}</span>
    </div>
  );
}

export default function IntegrationsWorkspace({
  project,
  nativeSdk,
  commit,
}: {
  project: Project;
  nativeSdk: Bootstrap["native_sdk"];
  commit: (change: Change) => Promise<void>;
}) {
  const [draft, setDraft] = useState<ExtensionProfile>(newDraft);
  const [showForm, setShowForm] = useState(false);
  const [handoffDraft, setHandoffDraft] = useState<HandoffBinding>(() => ({
    id: crypto.randomUUID(),
    system: "launchwright",
    direction: "context_input",
    external_kind: "release",
    external_id: "",
    external_revision: null,
    local_resource: "project:" + project.id,
    artifact_sha256: null,
  }));
  const validDigest = /^[0-9a-f]{64}$/.test(draft.digest_sha256);
  const validDraft = Boolean(
    draft.name.trim() &&
      draft.package_version.trim() &&
      draft.license.trim() &&
      draft.source.trim() &&
      validDigest,
  );
  const activeKinds = useMemo(
    () => new Set(project.extensions.filter((extension) => extension.enabled).map((extension) => extension.kind)),
    [project.extensions],
  );

  const saveDraft = async () => {
    if (!validDraft) return;
    await commit({ type: "upsert_extension", extension: draft });
    setDraft(newDraft());
    setShowForm(false);
  };

  const handoffNeedsDigest =
    handoffDraft.direction === "artifact_output" || handoffDraft.direction === "evidence_output";
  const handoffValid = Boolean(
    handoffDraft.external_id.trim() &&
      handoffDraft.local_resource &&
      (handoffNeedsDigest ? /^[0-9a-f]{64}$/.test(handoffDraft.artifact_sha256 ?? "") : handoffDraft.artifact_sha256 === null),
  );

  const saveHandoff = async () => {
    if (!handoffValid) return;
    await commit({ type: "upsert_handoff", binding: handoffDraft });
    setHandoffDraft({
      id: crypto.randomUUID(),
      system: "launchwright",
      direction: "context_input",
      external_kind: "release",
      external_id: "",
      external_revision: null,
      local_resource: "project:" + project.id,
      artifact_sha256: null,
    });
  };

  const setPermission = (permission: ExtensionProfile["permissions"][number], checked: boolean) => {
    setDraft((current) => ({
      ...current,
      permissions: checked
        ? Array.from(new Set([...current.permissions, permission]))
        : current.permissions.filter((candidate) => candidate !== permission),
    }));
  };

  return (
    <div className="workspace-scroll integrations-view" aria-label="Integrations and extension gates">
      <header className="workspace-heading">
        <div>
          <h2>Integrations</h2>
          <p>Explicit boundaries only. A descriptor is not authority, installation is not permission, and optional runtimes never activate themselves.</p>
        </div>
        <span className="count-label">{project.extensions.length} descriptors</span>
      </header>

      <section className="integration-section" aria-label="Runtime boundaries">
        <div className="integration-section-heading">
          <div>
            <strong>Runtime boundaries</strong>
            <span>What this project can prove at the current source revision.</span>
          </div>
          <ShieldCheck size={17} aria-hidden="true" />
        </div>
        <div className="integration-boundaries">
          <BoundaryRow
            name="Semwright Native SDK"
            status={nativeSdk.mode === "tauri" ? "CONNECTED" : "BROWSER DEMO"}
            tone={nativeSdk.mode === "tauri" ? "current" : "unknown"}
            body={"Exact source pin " + nativeSdk.pinned_revision.slice(0, 12) + "…; application state and transactions remain Motionwright-owned."}
          />
          <BoundaryRow
            name="Remote Semwright Platform"
            status="UPSTREAM GATE"
            body="The pinned public source exposes Native SDK and local platform/OS contracts, but no public remote account/worker client SDK is available here. No mock account is promoted to live."
          />
          <BoundaryRow
            name="Offline project work"
            status="AVAILABLE"
            tone="current"
            body="Local project editing, portable bundles and browser-demo contract checks do not require a cloud account."
          />
          <BoundaryRow
            name="Launchwright handoff"
            status="CONTRACT ONLY"
            body="Inter-product handoff must use explicit public resource/artifact references. Motionwright does not read or write Launchwright private storage."
          />
        </div>
      </section>

      <section className="integration-section" aria-label="Launchwright public handoff bindings">
        <div className="integration-section-heading">
          <div>
            <strong>Launchwright handoff bindings</strong>
            <span>Only public IDs, revisions and artifact digests cross this boundary. No Launchwright storage or private domain model is mounted here.</span>
          </div>
          <span className="status-pill status-unknown">{project.handoffs.length} BINDINGS</span>
        </div>
        <div className="handoff-editor">
          <div className="handoff-form-grid">
            <label>
              <span className="field-label">Direction</span>
              <select
                aria-label="Handoff direction"
                value={handoffDraft.direction}
                onChange={(event) => {
                  const direction = event.target.value as HandoffDirection;
                  const output = direction === "artifact_output" || direction === "evidence_output";
                  setHandoffDraft({
                    ...handoffDraft,
                    direction,
                    external_kind: direction === "artifact_output" ? "artifact" : direction === "evidence_output" ? "evidence" : "release",
                    artifact_sha256: output ? "" : null,
                  });
                }}
              >
                <option value="context_input">Consume context</option>
                <option value="context_output">Return context binding</option>
                <option value="artifact_output">Return artifact binding</option>
                <option value="evidence_output">Return evidence binding</option>
              </select>
            </label>
            <label>
              <span className="field-label">External kind</span>
              <select
                aria-label="External resource kind"
                value={handoffDraft.external_kind}
                onChange={(event) => setHandoffDraft({ ...handoffDraft, external_kind: event.target.value as ExternalResourceKind })}
              >
                <option value="release">Release</option>
                <option value="target">Target</option>
                <option value="context">Context</option>
                <option value="artifact">Artifact</option>
                <option value="evidence">Evidence</option>
              </select>
            </label>
            <label>
              <span className="field-label">Public resource ID</span>
              <input
                aria-label="External resource ID"
                value={handoffDraft.external_id}
                placeholder="public resource id"
                onChange={(event) => setHandoffDraft({ ...handoffDraft, external_id: event.target.value })}
              />
            </label>
            <label>
              <span className="field-label">External revision</span>
              <input
                aria-label="External resource revision"
                value={handoffDraft.external_revision ?? ""}
                placeholder="optional exact revision"
                onChange={(event) => setHandoffDraft({ ...handoffDraft, external_revision: event.target.value.trim() ? event.target.value : null })}
              />
            </label>
            <label className="span-two">
              <span className="field-label">Local binding</span>
              <select
                aria-label="Local handoff resource"
                value={handoffDraft.local_resource}
                onChange={(event) => setHandoffDraft({ ...handoffDraft, local_resource: event.target.value })}
              >
                <option value={"project:" + project.id}>Project · {project.title}</option>
                {project.scenes.map((scene) => <option key={scene.id} value={"scene:" + scene.id}>Scene · {scene.name}</option>)}
                {project.assets.map((asset) => <option key={asset.id} value={"asset:" + asset.id}>Asset · {asset.name}</option>)}
                {project.deliverables.map((profile) => <option key={profile.id} value={"deliverable:" + profile.id}>Deliverable · {profile.name}</option>)}
              </select>
            </label>
            {handoffNeedsDigest && (
              <label className="span-two">
                <span className="field-label">Exact output SHA-256</span>
                <input
                  className="mono"
                  aria-label="Handoff artifact digest"
                  value={handoffDraft.artifact_sha256 ?? ""}
                  maxLength={64}
                  placeholder="64 lowercase hex characters"
                  onChange={(event) => setHandoffDraft({ ...handoffDraft, artifact_sha256: event.target.value.trim().toLowerCase() })}
                />
              </label>
            )}
          </div>
          <div className="handoff-editor-footer">
            <span>No remote mutation is performed by creating this binding.</span>
            <button className="button button-primary compact" type="button" disabled={!handoffValid} onClick={() => void saveHandoff()}>
              Record binding
            </button>
          </div>
        </div>
        <div className="handoff-list">
          {project.handoffs.length === 0 ? (
            <div className="integration-empty compact">
              <strong>No external handoff bindings.</strong>
              <span>Record a public Release/Target reference or an exact output digest when a real handoff exists.</span>
            </div>
          ) : project.handoffs.map((binding) => (
            <div className="handoff-row" key={binding.id}>
              <div>
                <strong>{binding.direction.replaceAll("_", " ")}</strong>
                <span>Launchwright · {binding.external_kind} · <code>{binding.external_id}</code></span>
              </div>
              <span className="mono">{binding.external_revision ?? "unversioned"}</span>
              <span className="mono">{binding.artifact_sha256 ? binding.artifact_sha256.slice(0, 12) + "…" : binding.local_resource}</span>
              <button className="plain-icon" type="button" aria-label={"Remove handoff " + binding.external_id} onClick={() => void commit({ type: "remove_handoff", binding_id: binding.id })}>
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      </section>

      <section className="integration-section" aria-label="Optional renderer catalog">
        <div className="integration-section-heading">
          <div>
            <strong>Optional capabilities</strong>
            <span>Remotion and ManimGL are separate opt-in identities. The free base path remains independent.</span>
          </div>
          <button className="button button-primary compact" type="button" onClick={() => setShowForm((value) => !value)}>
            {showForm ? "Close form" : "Register descriptor"}
          </button>
        </div>

        <div className="extension-capability-strip">
          <span><i className={activeKinds.has("remotion-renderer") ? "gate-on" : "gate-off"} /> Remotion {activeKinds.has("remotion-renderer") ? "enabled" : "closed"}</span>
          <span><i className={activeKinds.has("manim-gl-renderer") ? "gate-on" : "gate-off"} /> ManimGL {activeKinds.has("manim-gl-renderer") ? "enabled" : "closed"}</span>
          <span><i className={activeKinds.has("generative-assets") ? "gate-on" : "gate-off"} /> Generative assets {activeKinds.has("generative-assets") ? "enabled" : "closed"}</span>
        </div>

        {showForm && (
          <div className="extension-editor" role="region" aria-label="Extension descriptor editor">
            <div className="extension-form-grid">
              <label>
                <span className="field-label">Capability</span>
                <select
                  aria-label="Extension capability"
                  value={draft.kind}
                  onChange={(event) => setDraft({ ...draft, kind: event.target.value as ExtensionKind, enabled: false })}
                >
                  {Object.entries(kindLabels).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}
                </select>
              </label>
              <label>
                <span className="field-label">Display name</span>
                <input aria-label="Extension display name" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
              </label>
              <label>
                <span className="field-label">Package version</span>
                <input aria-label="Extension version" value={draft.package_version} placeholder="1.0.0" onChange={(event) => setDraft({ ...draft, package_version: event.target.value })} />
              </label>
              <label>
                <span className="field-label">License expression</span>
                <input aria-label="Extension license" value={draft.license} placeholder="MIT" onChange={(event) => setDraft({ ...draft, license: event.target.value })} />
              </label>
              <label className="span-two">
                <span className="field-label">Reviewed source / package locator</span>
                <input aria-label="Extension source" value={draft.source} placeholder="https://…" onChange={(event) => setDraft({ ...draft, source: event.target.value })} />
              </label>
              <label className="span-two">
                <span className="field-label">SHA-256 digest</span>
                <input
                  className="mono"
                  aria-label="Extension digest"
                  value={draft.digest_sha256}
                  maxLength={64}
                  placeholder="64 lowercase hex characters"
                  onChange={(event) => setDraft({ ...draft, digest_sha256: event.target.value.trim().toLowerCase() })}
                />
              </label>
              <label>
                <span className="field-label">Rights state</span>
                <select
                  aria-label="Extension rights state"
                  value={draft.rights_status}
                  onChange={(event) => {
                    const rights = event.target.value as RightsStatus;
                    setDraft({ ...draft, rights_status: rights, enabled: rights === "cleared" ? draft.enabled : false });
                  }}
                >
                  <option value="unknown">Unknown</option>
                  <option value="restricted">Restricted</option>
                  <option value="cleared">Cleared</option>
                </select>
              </label>
              <label className="extension-opt-in">
                <input
                  type="checkbox"
                  checked={draft.enabled}
                  disabled={draft.rights_status !== "cleared"}
                  onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
                />
                <span><strong>Explicitly enable</strong><small>Never implied by registration.</small></span>
              </label>
            </div>
            <fieldset className="permission-fieldset">
              <legend>Requested project permissions</legend>
              {(["read_project", "read_assets", "write_artifacts", "network"] as const).map((permission) => (
                <label key={permission}>
                  <input
                    type="checkbox"
                    checked={draft.permissions.includes(permission)}
                    onChange={(event) => setPermission(permission, event.target.checked)}
                  />
                  <span>{permission.replace("_", " ")}</span>
                </label>
              ))}
            </fieldset>
            <div className="extension-editor-footer">
              <span className={validDigest ? "descriptor-valid" : "descriptor-warning"}>
                {validDigest ? "Digest shape valid." : "A reviewed SHA-256 digest is required."}
              </span>
              <button className="button button-primary" type="button" disabled={!validDraft} onClick={() => void saveDraft()}>
                Register versioned descriptor
              </button>
            </div>
          </div>
        )}

        <div className="extension-list">
          {project.extensions.length === 0 ? (
            <div className="integration-empty">
              <Package size={20} />
              <strong>No optional package is trusted yet.</strong>
              <span>Base Motion Canvas, MLT, Blender and Manim Community paths remain available without registering an optional package.</span>
            </div>
          ) : project.extensions.map((extension) => (
            <article className="extension-row" key={extension.id}>
              <div className="extension-main">
                <div className="extension-title-line">
                  <strong>{extension.name}</strong>
                  <span className={"status-pill " + (extension.enabled ? "status-current" : "status-unknown")}>
                    {extension.enabled ? "OPTED IN" : "REGISTERED"}
                  </span>
                </div>
                <span>{kindLabels[extension.kind]} · {extension.package_version} · {extension.license}</span>
                <code>{extension.digest_sha256.slice(0, 16)}…</code>
              </div>
              <div className="extension-permissions">
                {extension.permissions.length ? extension.permissions.map((permission) => <span key={permission}>{permission}</span>) : <span>no permissions</span>}
              </div>
              <div className="extension-actions">
                <button
                  className="button compact"
                  type="button"
                  disabled={!extension.enabled && extension.rights_status !== "cleared"}
                  onClick={() => void commit({ type: "upsert_extension", extension: { ...extension, enabled: !extension.enabled } })}
                >
                  {extension.enabled ? <LockKeyhole size={13} /> : <CircleCheck size={13} />}
                  {extension.enabled ? "Disable" : "Enable"}
                </button>
                <button className="plain-icon" type="button" aria-label={"Remove extension " + extension.name} onClick={() => void commit({ type: "remove_extension", extension_id: extension.id })}>
                  <Trash2 size={14} />
                </button>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="integration-section compact-integration-note" aria-label="Future capability boundary">
        <CircleDashed size={17} />
        <div>
          <strong>Future profiles stay future.</strong>
          <span>Interactive players and monetization are not presented as shipped capabilities. Generative assets require explicit provenance and rights before any output can become project evidence.</span>
        </div>
      </section>
    </div>
  );
}
