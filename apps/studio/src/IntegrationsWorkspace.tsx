import { CircleCheck, CircleDashed, LockKeyhole, Package, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import CreativeRuntimeDoctorPanel from "./CreativeRuntimeDoctorPanel";
import {sameValue} from "./creativeProduction";
import {integrationScope,integrationEmptyInput,integrationExtension,integrationHandoff,
  integrationExtensionInput,integrationHandoffInput,integrationSnapshot} from "./integrationAuthoringDraft";
import {useIntegrationAuthoringDraft,IntegrationRecoveryFeedback} from "./useIntegrationAuthoringDraft";
import {
  chooseSemwrightConnection,
  clearSavedSemwrightConnection,
  productionRuntimeCompatibility,
} from "./api";
import type {
  Bootstrap,
  Change,
  ExternalResourceKind,
  ExtensionKind,
  ExtensionProfile,
  HandoffBinding,
  HandoffDirection,
  Project,
  ProductionRuntimeCompatibility,
  RightsStatus,
} from "./types";

const kindLabels: Record<ExtensionKind, string> = {
  "remotion-renderer": "Remotion renderer",
  "manim-gl-renderer": "ManimGL renderer",
  "generative-assets": "Generative assets",
  "catalog-package": "Catalog package",
};


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

type IntegrationProps={
  project:Project;nativeSdk:Bootstrap["native_sdk"];commit:(change:Change)=>Promise<Project>;
};
export default function IntegrationsWorkspace(props:IntegrationProps) {
  const key=JSON.stringify([integrationScope(props.project),props.nativeSdk.mode,
    props.nativeSdk.pinned_revision,props.nativeSdk.version]);
  return <IntegrationWorkspaceEditor key={key} {...props}/>;
}
function IntegrationWorkspaceEditor({project,nativeSdk,commit}:IntegrationProps) {
  const editor=useIntegrationAuthoringDraft(project,commit);
  const input=editor.draft.input;
  const draft:ExtensionProfile={
    id:input.extensionId,name:input.extensionName,kind:input.extensionKind,package_version:input.extensionVersion,
    digest_sha256:input.extensionDigest,license:input.extensionLicense,source:input.extensionSource,
    rights_status:input.extensionRights,enabled:input.extensionEnabled,permissions:input.extensionPermissions,
  };
  const setDraft=(next:ExtensionProfile|((before:ExtensionProfile)=>ExtensionProfile))=>{
    editor.editMany(integrationExtensionInput(typeof next==="function"?next(draft):next));
  };
  const showForm=input.showExtensionForm;
  const setShowForm=(next:boolean|((before:boolean)=>boolean))=>
    editor.edit("showExtensionForm",typeof next==="function"?next(showForm):next);
  const mounted = useRef(false);
  const runtimeEpoch = useRef(0);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; runtimeEpoch.current++; };
  }, []);
  const [runtimeCompatibility, setRuntimeCompatibility] =
    useState<ProductionRuntimeCompatibility | null>(null);
  const [runtimeProbeFailed, setRuntimeProbeFailed] = useState(false);
  const [runtimeBusy, setRuntimeBusy] = useState(false);
  const [runtimeActionNote, setRuntimeActionNote] = useState<string | null>(null);
  const handoffDraft:HandoffBinding={
    id:input.handoffId,system:"launchwright",direction:input.handoffDirection,external_kind:input.handoffKind,
    external_id:input.handoffExternalId,external_revision:input.handoffRevision,
    local_resource:input.handoffLocalResource,artifact_sha256:input.handoffDigest,
  };
  const setHandoffDraft=(next:HandoffBinding)=>editor.editMany(integrationHandoffInput(next));
  const validDigest=/^[0-9a-f]{64}$/.test(draft.digest_sha256);
  const extensionProblem=(()=>{
    try {const value=integrationExtension(input);
      return project.extensions.some(existing=>sameValue(existing,value))?"These exact descriptor fields are already saved.":null;}
    catch(error){return error instanceof Error?error.message:String(error);}
  })();
  const validDraft=extensionProblem===null&&!editor.blocked;
  const activeKinds = useMemo(
    () => new Set(project.extensions.filter((extension) => extension.enabled).map((extension) => extension.kind)),
    [project.extensions],
  );

  useEffect(() => {
    const epoch = ++runtimeEpoch.current;
    const current = () => mounted.current && runtimeEpoch.current === epoch;
    setRuntimeCompatibility(null);
    setRuntimeProbeFailed(false);
    void productionRuntimeCompatibility(project)
      .then((status) => {
        if (current()) setRuntimeCompatibility(status);
      })
      .catch(() => {
        if (current()) setRuntimeProbeFailed(true);
      });
    return () => {
      if (runtimeEpoch.current === epoch) runtimeEpoch.current++;
    };
  }, [project.id, project.generation, nativeSdk.mode]);

  const updateRuntimeSelection = async (action: "choose" | "clear") => {
    if (runtimeBusy || nativeSdk.mode !== "tauri" || !mounted.current) return;
    // Initial compatibility reads and explicit owner actions share authority.
    // Starting an action revokes older reads; it does not cancel native work.
    const epoch = ++runtimeEpoch.current;
    const current = () => mounted.current && runtimeEpoch.current === epoch;
    setRuntimeCompatibility(null);
    setRuntimeProbeFailed(false);
    setRuntimeBusy(true);
    setRuntimeActionNote(null);
    try {
      const changed = action === "choose"
        ? await chooseSemwrightConnection(project)
        : await clearSavedSemwrightConnection(project);
      if (!current()) return;
      // A canceled native chooser does not install/replace anything.
      // Revoking only the saved pointer never stops an existing provider job.
      const status = await productionRuntimeCompatibility(project);
      if (!current()) return;
      setRuntimeCompatibility(status);
      setRuntimeProbeFailed(false);
      setRuntimeActionNote(action === "choose"
        ? (changed
            ? "Owner-selected runtime saved for this project. All production commands still require native policy and one-time grants."
            : "File selection canceled. The previous runtime selection was preserved.")
        : (changed
            ? "Saved project connection reference removed. Existing native jobs and the owner's original connection file were not touched."
            : "No saved project connection was present. An explicit environment connection may still apply."));
    } catch (error) {
      if (!current()) return;
      setRuntimeActionNote(error instanceof Error ? error.message : String(error));
      // An invalid selection must not overwrite a previously working
      // connection. Probe again; report its actual live state.
      try {
        const status = await productionRuntimeCompatibility(project);
        if (!current()) return;
        setRuntimeCompatibility(status);
        setRuntimeProbeFailed(false);
      } catch {
        if (current()) setRuntimeProbeFailed(true);
      }
    } finally {
      if (current()) setRuntimeBusy(false);
    }
  };

  const runtimePresentation = useMemo(() => {
    if (runtimeProbeFailed) {
      return {
        status: "UNAVAILABLE",
        tone: "stale" as const,
        body: "The desktop bridge could not return the bounded Semwright runtime probe. No compatibility is assumed.",
      };
    }
    if (!runtimeCompatibility) {
      return {
        status: "CHECKING",
        tone: "unknown" as const,
        body: "Validating the owner-provisioned connection and digest-pinned Semwright CLI without mutating project state.",
      };
    }
    const sourcePin =
      " Motionwright source pin " + runtimeCompatibility.pinned_revision.slice(0, 12) +
      "… is dependency evidence, not a binary source-revision attestation.";
    switch (runtimeCompatibility.status) {
      case "ready":
        return {
          status: "READY",
          tone: "current" as const,
          body:
            "Semwright CLI " + runtimeCompatibility.observed_version +
            " matches supported " + runtimeCompatibility.expected_version +
            "; executable " + (runtimeCompatibility.executable_sha256?.slice(0, 12) ?? "unknown") +
            "… and connection " + (runtimeCompatibility.connection_identity?.slice(0, 12) ?? "unknown") +
            "… were locally validated." + sourcePin,
        };
      case "incompatible":
        return {
          status: "INCOMPATIBLE",
          tone: "stale" as const,
          body: (runtimeCompatibility.reason ?? "The configured Semwright CLI version is not supported.") + sourcePin,
        };
      case "browser_demo":
        return {
          status: "BROWSER DEMO",
          tone: "unknown" as const,
          body: runtimeCompatibility.reason ?? "Runtime compatibility is unavailable outside the desktop shell.",
        };
      case "unconfigured":
        return {
          status: "UNCONFIGURED",
          tone: "unknown" as const,
          body: runtimeCompatibility.reason ?? "No canonical Semwright connection is configured.",
        };
      case "resource_mismatch":
        return {
          status: "WRONG RESOURCE",
          tone: "stale" as const,
          body: (runtimeCompatibility.reason ?? "The configured connection belongs to another project.") + sourcePin,
        };
      case "invalid":
        return {
          status: "INVALID",
          tone: "stale" as const,
          body: (runtimeCompatibility.reason ?? "The owner-provisioned connection failed validation.") + sourcePin,
        };
      case "unavailable":
        return {
          status: "UNAVAILABLE",
          tone: "stale" as const,
          body: (runtimeCompatibility.reason ?? "The bounded Semwright runtime probe could not complete.") + sourcePin,
        };
    }
  }, [runtimeCompatibility, runtimeProbeFailed]);

  const saveDraft=async()=>{if(validDraft)await editor.send("extension");};
  const handoffNeedsDigest=handoffDraft.direction==="artifact_output"||handoffDraft.direction==="evidence_output";
  const handoffProblem=(()=>{
    try {const value=integrationHandoff(input,integrationSnapshot(project).resources);
      return project.handoffs.some(existing=>sameValue(existing,value))?"These exact binding fields are already saved.":null;}
    catch(error){return error instanceof Error?error.message:String(error);}
  })();
  const handoffValid=handoffProblem===null&&!editor.blocked;
  const saveHandoff=async()=>{if(handoffValid)await editor.send("handoff");};
  const startExtension=()=>{
    const empty=integrationEmptyInput(project);
    editor.loadFields(integrationExtensionInput({
      id:empty.extensionId,name:empty.extensionName,kind:empty.extensionKind,package_version:empty.extensionVersion,
      digest_sha256:empty.extensionDigest,license:empty.extensionLicense,source:empty.extensionSource,
      rights_status:empty.extensionRights,enabled:empty.extensionEnabled,permissions:empty.extensionPermissions,
    }));
  };
  const startHandoff=()=>{
    const empty=integrationEmptyInput(project);
    editor.loadFields({handoffId:empty.handoffId,handoffDirection:empty.handoffDirection,handoffKind:empty.handoffKind,
      handoffExternalId:empty.handoffExternalId,handoffRevision:empty.handoffRevision,
      handoffLocalResource:empty.handoffLocalResource,handoffDigest:empty.handoffDigest});
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

      <IntegrationRecoveryFeedback editor={editor}/>
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
            body={"Semwright " + nativeSdk.version + " · exact source pin " + nativeSdk.pinned_revision.slice(0, 12) + "…; application state and transactions remain Motionwright-owned."}
          />
          <BoundaryRow
            name="Semwright production runtime"
            status={runtimePresentation.status}
            tone={runtimePresentation.tone}
            body={runtimePresentation.body}
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
        <div className="integration-runtime-setup" aria-label="Configure Semwright runtime">
          <div>
            <strong>Connect an existing Semwright runtime</strong>
            <p>Choose an owner-provisioned JSON connection with the operating system's file picker. Motionwright validates its project binding, executable digest and supported version before saving a local reference. This does not install Semwright, grant execution permission or expose the connection file to the editor.</p>
            <p>Required resource: <code className="mono">project:{project.id}</code>. The owner-created connection must name this exact project.</p>
          </div>
          <div className="integration-runtime-actions">
            <button
              type="button"
              className="button"
              disabled={nativeSdk.mode !== "tauri" || runtimeBusy}
              onClick={() => void updateRuntimeSelection("choose")}
            >
              {runtimeBusy ? "Validating…" : "Choose Semwright connection"}
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={nativeSdk.mode !== "tauri" || runtimeBusy}
              onClick={() => void updateRuntimeSelection("clear")}
            >
              Remove saved selection
            </button>
          </div>
          <p className="integration-runtime-note" role="status" aria-live="polite">
            {runtimeActionNote ?? (
              nativeSdk.mode === "tauri"
                ? "Local editing works without a renderer. A saved selection takes precedence over the optional legacy environment path; each production call revalidates native authority."
                : "Runtime selection is available only in the installed desktop app. Browser demo mode cannot access local runtime files."
            )}
          </p>
        </div>
        <CreativeRuntimeDoctorPanel project={project} nativeSdk={nativeSdk}
          runtime={runtimeCompatibility} />
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
                  // A direction change does not erase a separately authored kind or output digest.
                  setHandoffDraft({...handoffDraft,direction});
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
                onChange={(event) => setHandoffDraft({ ...handoffDraft, external_revision: event.target.value })}
              />
            </label>
            <label className="span-two">
              <span className="field-label">Local binding</span>
              <select
                aria-label="Local handoff resource"
                value={handoffDraft.local_resource}
                onChange={(event) => setHandoffDraft({ ...handoffDraft, local_resource: event.target.value })}
              >
                {!integrationSnapshot(project).resources.includes(handoffDraft.local_resource)&&
                  <option value={handoffDraft.local_resource}>Missing original binding · {handoffDraft.local_resource}</option>}
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
                  placeholder="64 lowercase hex characters"
                  onChange={(event) => setHandoffDraft({ ...handoffDraft, artifact_sha256: event.target.value })}
                />
              </label>
            )}
          </div>
          <div className="handoff-editor-footer">
            <span>No remote mutation is performed by creating this binding.</span>
            <button type="button" className="button secondary compact" disabled={editor.busy||!!editor.draft.pending}
              onClick={startHandoff}>New binding</button>
            <button className="button button-primary compact" type="button" disabled={!handoffValid} onClick={() => void saveHandoff()}>
              Record binding
            </button>
          </div>
        </div>
        {handoffProblem&&<p className="integration-runtime-note" role="status">{handoffProblem}</p>}
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
              <button className="button secondary compact" type="button" disabled={editor.blocked}
                onClick={()=>editor.loadFields(integrationHandoffInput(binding))}>Edit binding {binding.external_id}</button>
              <button className="plain-icon" type="button" aria-label={"Remove handoff " + binding.external_id}
                disabled={editor.blocked} onClick={() => void editor.send("remove_handoff",binding.id)}>
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
          <button type="button" className="button secondary compact" disabled={editor.busy||!!editor.draft.pending}
            onClick={startExtension}>New descriptor</button>
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
                  placeholder="64 lowercase hex characters"
                  onChange={(event) => setDraft({ ...draft, digest_sha256: event.target.value })}
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
              {extensionProblem&&<span role="status">{extensionProblem}</span>}
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
                <button className="button secondary compact" type="button" disabled={editor.blocked}
                  onClick={()=>editor.loadFields(integrationExtensionInput(extension))}>Edit descriptor {extension.name}</button>
                <button
                  className="button compact"
                  type="button"
                  disabled={editor.blocked||!extension.enabled && extension.rights_status !== "cleared"}
                  onClick={() => void editor.send("toggle_extension",extension.id)}
                >
                  {extension.enabled ? <LockKeyhole size={13} /> : <CircleCheck size={13} />}
                  {extension.enabled ? "Disable" : "Enable"}
                </button>
                <button className="plain-icon" type="button" aria-label={"Remove extension " + extension.name} disabled={editor.blocked}
                  onClick={() => void editor.send("remove_extension",extension.id)}>
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
