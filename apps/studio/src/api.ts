import { appendCreativePatchRecord, previewCreativePatchUndo } from "./creativeUndo";
import { applyProductionDesignChange, emptyProductionDesign, sameValue } from "./creativeProduction";
import type { CreativePatch, ScopedCanvasEdit } from "./creativeProduction";
import { invoke } from "@tauri-apps/api/core";
import { fixtureBootstrap } from "./fixture";
import type {
  AssetIntegrityPage,
  Bootstrap,
  BranchState,
  CanvasKeyframe,
  CaptionExportResult,
  Change,
  DataClass,
  EffectGrantReceipt,
  EffectKind,
  LockKind,
  ModelContextDisclosure,
  ModelRequestDraft,
  ModelRequestPreflight,
  MotionCanvasFilmOptions,
  MotionCanvasRenderEvidence,
  MotionCanvasProjectionPreflight,
  MultiSegmentReadinessReport,
  MltAvMasterEvidence,
  MasterExportReceipt,
  PortableMediaVerification,
  OtioExportResult,
  PortableBundleExport,
  PortableBundlePlan,
  Project,
  ProjectEvent,
  ProductionJobProjection,
  ProductionJobsHistoryCursor,
  ProductionJobsHistoryPage,
  ProductionRuntimeCompatibility,
  WaveformPage,
  WorkflowAction,
  WorkflowActionResult,
  WorkflowOverview,
} from "./types";
import { rationalSeconds } from "./types";

const isTauri = () => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
let browserState = structuredClone(fixtureBootstrap);
const browserEvents: ProjectEvent[] = [];

const projectResource = (project: Project) => "project:" + project.id;
const sceneResource = (sceneId: string) => "scene:" + sceneId;

const extensionKindForRenderer = (renderer: Project["scenes"][number]["renderer"]) => {
  if (renderer === "remotion") return "remotion-renderer";
  if (renderer === "manim-gl") return "manim-gl-renderer";
  return null;
};

const rendererEnabled = (project: Project, renderer: Project["scenes"][number]["renderer"]) => {
  const kind = extensionKindForRenderer(renderer);
  return kind === null || project.extensions.some(
    (extension) => extension.kind === kind && extension.enabled && extension.rights_status === "cleared",
  );
};

function assertUnlocked(project: Project, resource: string, kinds: LockKind[]) {
  const hit = project.locks.find((lock) => lock.resource === resource && kinds.includes(lock.kind));
  if (hit) throw new Error(`resource is locked: ${resource} (${hit.kind})`);
}

function captureBranchState(project: Project): BranchState {
  return structuredClone({
    scenes: project.scenes,
    markers: project.markers,
    locks: project.locks,
    deliverables: project.deliverables,
    brief: project.brief,
    narrative: project.narrative,
    audio: project.audio,
    visual_language: project.visual_language,
    proposal_sets: project.proposal_sets,
    model_invocations: project.model_invocations,
    production_design: project.production_design ?? emptyProductionDesign(),
  });
}

function restoreBranchState(project: Project, state: BranchState) {
  project.scenes = structuredClone(state.scenes);
  project.markers = structuredClone(state.markers);
  project.locks = structuredClone(state.locks);
  project.deliverables = structuredClone(state.deliverables);
  project.brief = structuredClone(state.brief);
  project.narrative = structuredClone(state.narrative);
  project.audio = structuredClone(state.audio);
  project.visual_language = structuredClone(state.visual_language);
  project.proposal_sets = structuredClone(state.proposal_sets);
  project.model_invocations = structuredClone(state.model_invocations);
  project.production_design = structuredClone(state.production_design ?? emptyProductionDesign());
}

function saveActiveWorkspace(project: Project) {
  const state = captureBranchState(project);
  const branch = project.branches.find((candidate) => candidate.id === project.active_branch);
  if (!branch) throw new Error("active branch is missing");
  const existing = project.branch_workspaces.find((workspace) => workspace.branch_id === branch.id);
  if (existing) {
    existing.current_state = state;
  } else {
    project.branch_workspaces.push({
      branch_id: branch.id,
      base_revision: branch.base_revision,
      base_state: structuredClone(state),
      current_state: state,
    });
  }
}

function branchStateEqual(left: unknown, right: unknown) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function mergeBranchState(base: BranchState, target: BranchState, source: BranchState): BranchState {
  const fields: Array<keyof BranchState> = [
    "scenes", "markers", "locks", "deliverables", "brief", "narrative",
    "audio", "visual_language", "proposal_sets", "model_invocations", "production_design",
  ];
  const result = structuredClone(target);
  const conflicts: string[] = [];
  for (const field of fields) {
    const baseValue = base[field];
    const targetValue = target[field];
    const sourceValue = source[field];
    if (branchStateEqual(sourceValue, baseValue) || branchStateEqual(sourceValue, targetValue)) continue;
    if (branchStateEqual(targetValue, baseValue)) {
      Object.assign(result, { [field]: structuredClone(sourceValue) });
    } else {
      conflicts.push(field);
    }
  }
  if (conflicts.length) throw new Error("semantic merge conflict in: " + conflicts.join(", "));
  return result;
}

export async function bootstrap(): Promise<Bootstrap> {
  if (isTauri()) return invoke<Bootstrap>("bootstrap");
  return structuredClone(browserState);
}

function browserModelDisclosure(
  project: Project,
  resourceRef: string,
  dataClass: DataClass,
): ModelContextDisclosure {
  const encode = (value: unknown) => new TextEncoder().encode(JSON.stringify(value)).length;
  if (resourceRef === projectResource(project)) {
    if (dataClass === "metadata") {
      const value = {
        id: project.id,
        title: project.title,
        revision: project.revision,
        active_branch: project.active_branch,
        scene_count: project.scenes.length,
        asset_count: project.assets.length,
      };
      return {
        resource_ref: resourceRef, data_class: dataClass, label: project.title,
        media_type: null, estimated_bytes: encode(value), content_sha256: null,
        preview: null, preview_truncated: false, untrusted_data: true,
      };
    }
    if (dataClass === "text") {
      const value = JSON.stringify({
        title: project.title, brief: project.brief, narrative: project.narrative,
      });
      return {
        resource_ref: resourceRef, data_class: dataClass, label: project.title,
        media_type: null, estimated_bytes: new TextEncoder().encode(value).length,
        content_sha256: null, preview: value.slice(0, 640),
        preview_truncated: value.length > 640, untrusted_data: true,
      };
    }
    throw new Error("Project resource exposes only metadata or text context.");
  }

  if (resourceRef.startsWith("scene:")) {
    const scene = project.scenes.find((candidate) => candidate.id === resourceRef.slice(6));
    if (!scene) throw new Error("Model request references an unknown scene.");
    if (dataClass === "metadata") {
      const value = {
        id: scene.id, name: scene.name, start: scene.start, duration: scene.duration,
        renderer: scene.renderer, status: scene.status,
        beat_count: scene.beats.length, node_count: scene.nodes.length,
      };
      return {
        resource_ref: resourceRef, data_class: dataClass, label: scene.name,
        media_type: null, estimated_bytes: encode(value), content_sha256: null,
        preview: null, preview_truncated: false, untrusted_data: true,
      };
    }
    if (dataClass === "text") {
      const value = JSON.stringify({
        name: scene.name,
        objective: scene.objective,
        beats: scene.beats.map((beat) => ({
          id: beat.id, label: beat.label, objective: beat.objective,
        })),
        node_text: scene.nodes.flatMap((node) =>
          node.text ? [{ id: node.id, name: node.name, text: node.text }] : []
        ),
      });
      return {
        resource_ref: resourceRef, data_class: dataClass, label: scene.name,
        media_type: null, estimated_bytes: new TextEncoder().encode(value).length,
        content_sha256: null, preview: value.slice(0, 640),
        preview_truncated: value.length > 640, untrusted_data: true,
      };
    }
    throw new Error("Scene resource exposes only metadata or text in browser demo mode.");
  }

  if (resourceRef.startsWith("asset:")) {
    const asset = project.assets.find((candidate) => candidate.id === resourceRef.slice(6));
    if (!asset) throw new Error("Model request references an unknown asset.");
    if (dataClass !== "metadata") {
      throw new Error("Browser demo mode cannot inspect local asset bytes. Use desktop preflight for source data.");
    }
    return {
      resource_ref: resourceRef, data_class: dataClass, label: asset.name,
      media_type: asset.media_type, estimated_bytes: encode(asset),
      content_sha256: asset.content_sha256, preview: null,
      preview_truncated: false, untrusted_data: true,
    };
  }

  throw new Error("Model request references an unsupported resource.");
}

export async function modelRequestPreflight(
  project: Project,
  draft: ModelRequestDraft,
): Promise<ModelRequestPreflight> {
  if (isTauri()) {
    return invoke<ModelRequestPreflight>("model_request_preflight", {
      request: { project_id: project.id, draft },
    });
  }
  const resourceRefs = [...new Set(draft.resource_refs)].sort();
  const classOrder: DataClass[] = ["audio", "frame", "metadata", "source_code", "text"];
  const dataClasses = [...new Set(draft.data_classes)].sort(
    (left, right) => classOrder.indexOf(left) - classOrder.indexOf(right),
  );
  if (!resourceRefs.length || !dataClasses.length) {
    throw new Error("Model request scope and data classes are required.");
  }
  const disclosures = resourceRefs.flatMap((resource) =>
    dataClasses.map((dataClass) => browserModelDisclosure(project, resource, dataClass))
  );
  const sourceDataClasses = dataClasses.filter((dataClass) => dataClass !== "metadata");
  return {
    schema: "motionwright-model-preflight/1",
    project_id: project.id,
    generation: project.generation,
    base_revision: project.revision,
    provider_kind: draft.provider_kind,
    provider: draft.provider,
    model: draft.model,
    resource_refs: resourceRefs,
    data_classes: dataClasses,
    budget: structuredClone(draft.budget),
    disclosures,
    estimated_total_bytes: disclosures.reduce((sum, row) => sum + row.estimated_bytes, 0),
    source_data_classes: sourceDataClasses,
    explicit_source_consent_required:
      draft.provider_kind === "remote" && sourceDataClasses.length > 0,
    fallback_provider: null,
    studio_network_dispatch_supported: false,
    network_dispatched: false,
    fingerprint_sha256: "browser-demo-" + project.revision.toString(16).padStart(8, "0"),
  };
}

async function issueEffectGrant(
  effect: EffectKind,
  project: Project | null,
  subject: string,
): Promise<EffectGrantReceipt> {
  if (!isTauri()) {
    throw new Error("Native effect grants require the Motionwright desktop runtime.");
  }
  return invoke<EffectGrantReceipt>("issue_effect_grant", {
    request: {
      effect,
      project_id: project?.id ?? null,
      generation: project?.generation ?? null,
      revision: project?.revision ?? null,
      subject: subject.trim(),
    },
  });
}

const workflowCommand = (action: WorkflowAction): string => ({
  record_start: "workflow.record.start",
  record_stop: "workflow.record.stop",
  compile: "workflow.compile",
  suggestion_compile: "workflow.suggestion.compile",
  proposal_plan: "workflow.proposal.plan",
  proposal_accept: "workflow.proposal.accept",
  verify: "workflow.verify",
  replay: "workflow.replay",
  promote: "workflow.promote",
})[action];

export async function importAssetFile(
  project: Project,
  path: string,
  name?: string,
  mediaType?: string,
): Promise<Project> {
  if (!isTauri()) {
    throw new Error("Local asset import requires the Motionwright desktop runtime.");
  }
  const grant = await issueEffectGrant("import_local", project, path);
  return invoke<Project>("import_asset_file", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      effect_grant: grant.token,
      path,
      name: name ?? null,
      media_type: mediaType ?? null,
    },
  });
}

export async function importVoiceFile(
  project: Project,
  path: string,
  label?: string,
): Promise<Project> {
  if (!isTauri()) {
    throw new Error("Measured voice import requires the Motionwright desktop runtime.");
  }
  const grant = await issueEffectGrant("import_local", project, path);
  return invoke<Project>("import_voice_file", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      effect_grant: grant.token,
      path,
      name: null,
      media_type: null,
      label: label ?? null,
    },
  });
}

export async function waveformPage(
  project: Project,
  trackId: string,
  pageIndex: number,
  pageSize = 256,
): Promise<WaveformPage | null> {
  if (!isTauri()) return null;
  return invoke<WaveformPage>("waveform_page", {
    request: {
      project_id: project.id,
      track_id: trackId,
      page_index: pageIndex,
      page_size: pageSize,
    },
  });
}

export async function exportProjectBundle(
  project: Project,
  destination: string,
): Promise<PortableBundleExport> {
  if (!isTauri()) {
    throw new Error("Portable project bundles require the Motionwright desktop runtime.");
  }
  const grant = await issueEffectGrant("deliver_local", project, destination);
  return invoke<PortableBundleExport>("export_project_bundle", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      path: destination,
      effect_grant: grant.token,
    },
  });
}

export async function inspectProjectBundle(path: string): Promise<PortableBundlePlan> {
  if (!isTauri()) {
    throw new Error("Portable project bundles require the Motionwright desktop runtime.");
  }
  return invoke<PortableBundlePlan>("inspect_project_bundle", {
    request: { path },
  });
}

export async function importProjectBundle(path: string): Promise<Project> {
  if (!isTauri()) {
    throw new Error("Portable project bundles require the Motionwright desktop runtime.");
  }
  const grant = await issueEffectGrant("import_local", null, path);
  return invoke<Project>("import_project_bundle", {
    request: { path, effect_grant: grant.token },
  });
}

export async function exportCaptionSidecar(
  project: Project,
  profileId: string,
  path: string,
): Promise<CaptionExportResult> {
  if (!isTauri()) {
    throw new Error("Caption sidecar export requires the Motionwright desktop runtime.");
  }
  const grant = await issueEffectGrant("deliver_local", project, path);
  return invoke<CaptionExportResult>("export_caption_sidecar", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      profile_id: profileId,
      path,
      effect_grant: grant.token,
    },
  });
}

export async function exportOtio(project: Project, path: string): Promise<OtioExportResult> {
  if (!isTauri()) {
    throw new Error("OpenTimelineIO export requires the Motionwright desktop runtime.");
  }
  const grant = await issueEffectGrant("deliver_local", project, path);
  return invoke<OtioExportResult>("export_otio", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      path,
      effect_grant: grant.token,
    },
  });
}

export async function productionRuntimeCompatibility(
  project: Project,
): Promise<ProductionRuntimeCompatibility> {
  if (isTauri()) {
    return invoke<ProductionRuntimeCompatibility>("production_runtime_status", {
      request: { project_id: project.id },
    });
  }
  return {
    status: "browser_demo",
    reason:
      "Browser demo mode cannot inspect an owner-provisioned Semwright executable. No runtime compatibility is fabricated.",
    expected_version: fixtureBootstrap.native_sdk.version,
    observed_version: null,
    version_compatible: null,
    pinned_revision: fixtureBootstrap.native_sdk.pinned_revision,
    connection_identity: null,
    executable_sha256: null,
  };
}

export async function workflowOverview(project: Project): Promise<WorkflowOverview> {
  if (isTauri()) {
    return invoke<WorkflowOverview>("workflow_overview", {
      request: { project_id: project.id },
    });
  }
  return {
    status: "browser_demo",
    reason: "Browser demo mode does not attach a canonical Semwright session. No workflow evidence is mocked as live.",
    connection_identity: null,
    authority: null,
    traces: { traces: [] },
    candidates: { candidates: [] },
    patterns: { patterns: [] },
    suggestions: { suggestions: [] },
    proposals: { proposals: [] },
    promotions: { promotions: [] },
  };
}

export async function workflowAction(
  project: Project,
  action: WorkflowAction,
  args: Record<string, unknown> = {},
): Promise<WorkflowActionResult> {
  if (!isTauri()) {
    throw new Error("Canonical workflow actions require the Motionwright desktop runtime.");
  }
  const mutating = action !== "proposal_plan";
  const grant = mutating
    ? await issueEffectGrant("workflow_mutation", project, workflowCommand(action))
    : null;
  return invoke<WorkflowActionResult>("workflow_action", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      action,
      effect_grant: grant?.token ?? null,
      args,
    },
  });
}

/** Audit only already-imported project-bound blobs on demand. The WebView
 * passes no arbitrary file paths and receives no owner filesystem paths.
 */
export async function assetIntegrityPage(
  project: Project,
  offset: number | null = null,
  limit = 8,
): Promise<AssetIntegrityPage> {
  if (!isTauri()) throw new Error("Local asset integrity requires the desktop runtime.");
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 16) {
    throw new Error("Asset integrity page size must be between 1 and 16.");
  }
  if (offset !== null && (!Number.isSafeInteger(offset) || offset < 1 || offset % limit !== 0)) {
    throw new Error("Invalid asset integrity continuation cursor.");
  }
  return invoke<AssetIntegrityPage>("asset_integrity_page", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      offset,
      limit,
    },
  });
}

export async function productionJobs(
  project: Project,
  limit = 32,
): Promise<ProductionJobProjection[]> {
  if (!isTauri()) return [];
  return invoke<ProductionJobProjection[]>("production_jobs", {
    request: {
      project_id: project.id,
      limit,
    },
  });
}

/** Enumerate all locally persisted jobs for one source- and
 * receipt-watermark-bound snapshot without querying Semwright's live driver.
 * A browser-only demo never fabricates receipt history.
 */
export async function productionJobsHistory(
  project: Project,
  limit = 16,
  cursor: ProductionJobsHistoryCursor | null = null,
): Promise<ProductionJobsHistoryPage> {
  if (!isTauri()) {
    throw new Error("Historical production receipts require the desktop runtime.");
  }
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 64) {
    throw new Error("Historical production jobs page size is outside the 1–64 limit.");
  }
  return invoke<ProductionJobsHistoryPage>("production_jobs_history", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      limit,
      cursor,
    },
  });
}

/** Read-only semantic Film projection. It cannot establish real render support. */
export async function preflightMotionCanvas(
  project: Project,
  deliverableId: string,
  options: MotionCanvasFilmOptions,
): Promise<MotionCanvasProjectionPreflight> {
  if (!isTauri()) {
    throw new Error("Canonical Film preflight requires the Motionwright desktop runtime.");
  }
  return invoke<MotionCanvasProjectionPreflight>("motion_canvas_preflight", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      deliverable_id: deliverableId,
      options,
    },
  });
}

export async function renderMotionCanvas(
  project: Project,
  deliverableId: string,
  options: MotionCanvasFilmOptions,
): Promise<MotionCanvasRenderEvidence> {
  if (!isTauri()) {
    throw new Error("Canonical Motion Canvas production requires the Motionwright desktop runtime.");
  }
  const requestId = crypto.randomUUID();
  const grant = await issueEffectGrant("render_local", project, requestId);
  return invoke<MotionCanvasRenderEvidence>("render_motion_canvas", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      request_id: requestId,
      effect_grant: grant.token,
      deliverable_id: deliverableId,
      options,
    },
  });
}

/** Check manifest-source readiness of a multi-segment native cut.
 * Backend resolves original render options, evidence and root from the
 * opaque owner-issued token; no caller-provided file paths or hashes.
 */
export async function preflightMultiSegmentReadiness(
  project: Project,
  deliverableId: string,
  previewToken: string,
): Promise<MultiSegmentReadinessReport> {
  if (!isTauri()) {
    throw new Error("Native multi-segment readiness requires the desktop runtime.");
  }
  return invoke<MultiSegmentReadinessReport>("preflight_multi_segment_mlt_readiness", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      deliverable_id: deliverableId,
      preview_token: previewToken,
    },
  });
}

// Most-recent-first keyset read, bounded to the same max 256 events as storage.
export async function projectHistoryRecent(
  project: Project,
  throughRevision = project.revision,
  limit = 101,
): Promise<ProjectEvent[]> {
  const bounded = Math.max(1, Math.min(256, Math.trunc(limit)));
  if (!Number.isSafeInteger(throughRevision) || throughRevision < 0) {
    throw new Error("invalid history revision cursor");
  }
  if (isTauri()) {
    return invoke<ProjectEvent[]>("project_history_recent", {
      request: {
        project_id: project.id,
        through_revision: throughRevision,
        limit: bounded,
      },
    });
  }
  return structuredClone(browserEvents
    .filter((event) => event.revision <= throughRevision)
    .sort((left, right) => right.revision - left.revision)
    .slice(0, bounded));
}

/** Produce a canonical H.264/AAC master from one already-authenticated native
 * segment and an exact imported measured voice take. No caller file paths.
 */
export async function assembleNativeAvMaster(
  project: Project,
  deliverableId: string,
  voiceTrackId: string,
  previewToken: string,
): Promise<MltAvMasterEvidence> {
  if (!isTauri()) {
    throw new Error("Native AV mastering requires the desktop Semwright runtime.");
  }
  const requestId = crypto.randomUUID();
  const grant = await issueEffectGrant("render_local", project, requestId);
  return invoke<MltAvMasterEvidence>("assemble_av_master", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      request_id: requestId,
      effect_grant: grant.token,
      deliverable_id: deliverableId,
      preview_token: previewToken,
      voice_track_id: voiceTrackId,
    },
  });
}

/** Deliver one session-authenticated MP4 to an explicit absolute, create-new
 * local destination. The WebView never names the hidden source artifact.
 */
export async function exportVerifiedNativeMaster(
  project: Project,
  exportToken: string,
  destination: string,
  includeIntegrityManifest = false,
): Promise<MasterExportReceipt> {
  if (!isTauri()) {
    throw new Error("Verified native MP4 delivery requires the desktop runtime.");
  }
  const absolutePath = destination.trim();
  if (!absolutePath) throw new Error("Choose an absolute .mp4 delivery destination.");
  const grant = await issueEffectGrant("deliver_local", project, absolutePath);
  return invoke<MasterExportReceipt>("export_native_av_master", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      effect_grant: grant.token,
      export_token: exportToken,
      destination: absolutePath,
      include_integrity_manifest: includeIntegrityManifest,
    },
  });
}

/** Compare an explicitly supplied local MP4 against its portable unsigned
 * receipt in the desktop backend. No effect grant or creative mutation occurs.
 * The optional SHA-256 anchor must originate outside the receipt itself.
 */
export async function verifyPortableMediaReceipt(
  manifestPath: string,
  trustedSha256?: string,
): Promise<PortableMediaVerification> {
  if (!isTauri()) throw new Error("Portable MP4 verification requires the desktop runtime.");
  const path = manifestPath.trim();
  if (!path) throw new Error("Choose the absolute path of a portable MP4 receipt.");
  const anchor = trustedSha256?.trim() || null;
  if (anchor !== null && !/^[0-9a-f]{64}$/.test(anchor)) {
    throw new Error("Independent trusted SHA-256 must be 64 lowercase hexadecimal characters.");
  }
  return invoke<PortableMediaVerification>("verify_local_media_integrity", {
    request: { manifest_path: path, trusted_sha256: anchor },
  });
}

/** Read only a small, exact SHA-256 verified native master through a
 * session token. Larger masters remain available through streamed disk export.
 */
export async function readVerifiedNativeAvReview(
  project: Project,
  exportToken: string,
): Promise<Uint8Array> {
  if (!isTauri()) throw new Error("Native AV review requires the desktop runtime.");
  const data = await invoke<Uint8Array | ArrayBuffer | number[]>("review_native_av_master", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      export_token: exportToken,
    },
  });
  const bytes = data instanceof Uint8Array ? data
    : data instanceof ArrayBuffer ? new Uint8Array(data)
      : Array.isArray(data) ? Uint8Array.from(data) : null;
  if (!bytes || bytes.length < 12 || bytes.length > 16 * 1024 * 1024 ||
    bytes[4] !== 102 || bytes[5] !== 116 || bytes[6] !== 121 || bytes[7] !== 112) {
    throw new Error("Verified native AV review returned an invalid or unbounded MP4.");
  }
  return bytes;
}

/** Fetch one source-verified PNG. This cannot read an arbitrary disk path.
 * The token comes only from a completed desktop Semwright production result.
 */
export async function readNativePreviewFrame(
  project: Project,
  token: string,
  frameIndex: number,
): Promise<Uint8Array> {
  if (!isTauri()) throw new Error("Native frame readback requires the desktop runtime.");
  if (!Number.isSafeInteger(frameIndex) || frameIndex < 0) {
    throw new Error("Native frame index must be a nonnegative safe integer.");
  }
  const data = await invoke<Uint8Array | ArrayBuffer | number[]>("preview_native_frame", {
    request: {
      project_id: project.id,
      generation: project.generation,
      revision: project.revision,
      token,
      frame_index: frameIndex,
    },
  });
  const bytes = data instanceof Uint8Array ? data
    : data instanceof ArrayBuffer ? new Uint8Array(data)
      : Array.isArray(data) ? Uint8Array.from(data)
        : null;
  if (!bytes || bytes.length < 8 || bytes[0] !== 137 || bytes[1] !== 80 ||
      bytes[2] !== 78 || bytes[3] !== 71 || bytes[4] !== 13 ||
      bytes[5] !== 10 || bytes[6] !== 26 || bytes[7] !== 10) {
    throw new Error("Native preview did not return valid PNG bytes.");
  }
  return bytes;
}

export async function projectHistory(
  project: Project,
  afterRevision = 0,
  limit = 100,
): Promise<ProjectEvent[]> {
  if (isTauri()) {
    return invoke<ProjectEvent[]>("project_history", {
      request: {
        project_id: project.id,
        after_revision: afterRevision,
        limit,
      },
    });
  }
  return structuredClone(
    browserEvents
      .filter((event) => event.revision > afterRevision)
      .slice(0, Math.max(1, Math.min(limit, 256))),
  );
}

export async function applyChange(project: Project, change: Change): Promise<Project> {
  const requestId = crypto.randomUUID();
  if (isTauri()) {
    const grant = await issueEffectGrant("project_edit", project, requestId);
    return invoke<Project>("apply_change", {
      request: {
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        request_id: requestId,
        effect_grant: grant.token,
        change
      }
    });
  }

  return simulateChange(project, change, requestId, true);
}

async function simulateChange(project: Project, change: Change, requestId: string, record: boolean): Promise<Project> {
  if (![1,2].includes(project.schema_version)) throw new Error("Unsupported project schema.");
  if (project.schema_version===1 && (!sameValue({...emptyProductionDesign(),...project.production_design},emptyProductionDesign()) || project.branch_workspaces.some(workspace=>!sameValue({...emptyProductionDesign(),...workspace.base_state.production_design},emptyProductionDesign()) || !sameValue({...emptyProductionDesign(),...workspace.current_state.production_design},emptyProductionDesign())))) throw new Error("Creative production state requires project schema 2.");
  const next = structuredClone(project);
  next.schema_version = 2;
  next.branch_workspaces ??= [];
  next.reviews ??= [];
  next.merges ??= [];
  switch (change.type) {
    case "upsert_product_hero":
    case "detach_product_hero":
    case "set_production_plan":
    case "upsert_native_capsule":
      await applyProductionDesignChange(next, change);
      break;
    case "undo_creative_patch": {
      const preview=previewCreativePatchUndo(next,change.patch_id);
      appendCreativePatchRecord(next,preview,"Revert creative patch "+change.patch_id,change.patch_id);
      const scene=next.scenes.find(s=>s.id===preview.scene_id)!;
      for(const updated of preview.after) scene.nodes[scene.nodes.findIndex(n=>n.id===updated.id)]=updated;
      scene.status="draft";
      break;
    }
    case "apply_creative_patch": {
      const preview = await previewCreativePatch(next, change.patch);
      appendCreativePatchRecord(next,preview,change.patch.rationale,null);
      const scene = next.scenes.find(s => s.id === change.patch.scene_id)!;
      for (const updated of preview.after) scene.nodes[scene.nodes.findIndex(n => n.id === updated.id)] = updated;
      scene.status = "draft";
      break;
    }
    case "rename_project":
      assertUnlocked(next, projectResource(next), ["content"]);
      next.title = change.title;
      break;
    case "set_brief":
      assertUnlocked(next, projectResource(next), ["content"]);
      next.brief = {
        ...next.brief,
        objective: change.objective,
        audience: change.audience,
        constraints: change.constraints,
        exclusions: change.exclusions,
      };
      break;
    case "set_narrative_premise":
      assertUnlocked(next, projectResource(next), ["content"]);
      next.narrative.premise = change.premise;
      break;
    case "add_scene": {
      assertUnlocked(next, projectResource(next), ["content", "timing"]);
      const start = next.scenes.reduce((total, scene) => total + Number(scene.duration.num) / Number(scene.duration.den), 0);
      next.scenes.push({
        id: crypto.randomUUID(),
        name: change.name,
        objective: change.objective,
        start: { num: String(start), den: "1" },
        duration: { num: String(change.duration_seconds), den: "1" },
        renderer: "motion-canvas",
        status: "draft",
        beats: [],
        nodes: [],
        camera: { center_x: 960, center_y: 540, zoom: 1, rotation_deg: 0, safe_margin: 0.05 },
      });
      break;
    }
    case "move_scene": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content", "timing"]);
      const from = next.scenes.findIndex((scene) => scene.id === change.scene_id);
      if (from >= 0 && change.to_index >= 0 && change.to_index < next.scenes.length) {
        const [scene] = next.scenes.splice(from, 1);
        next.scenes.splice(change.to_index, 0, scene);
        let cursor = 0;
        next.scenes.forEach((entry) => {
          entry.start = { num: String(cursor), den: "1" };
          cursor += Number(entry.duration.num) / Number(entry.duration.den);
        });
      }
      break;
    }
    case "update_scene_objective": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (scene) scene.objective = change.objective;
      break;
    }
    case "set_scene_renderer": {
      assertUnlocked(next, sceneResource(change.scene_id), ["renderer"]);
      if (!rendererEnabled(next, change.renderer)) {
        throw new Error("renderer requires an explicitly enabled project extension");
      }
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (scene) scene.renderer = change.renderer;
      break;
    }
    case "set_scene_status": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (scene) scene.status = change.status;
      break;
    }
    case "set_scene_duration": {
      assertUnlocked(next, sceneResource(change.scene_id), ["timing"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (!scene) throw new Error("resource not found: scene:" + change.scene_id);
      const duration = Number(change.duration.num) / Number(change.duration.den);
      if (!Number.isFinite(duration) || duration <= 0 || duration > 86_400) {
        throw new Error("scene duration is out of bounds");
      }
      if (scene.nodes.some((node) => node.keyframes.some((keyframe) =>
        Number(keyframe.at.num) / Number(keyframe.at.den) >= duration
      ))) {
        throw new Error("scene duration would strand an authored keyframe");
      }
      if (scene.beats.some((beat) => {
        const start = Number(beat.start.num) / Number(beat.start.den);
        const beatDuration = Number(beat.duration.num) / Number(beat.duration.den);
        return !Number.isFinite(start + beatDuration) || start + beatDuration > duration;
      })) {
        throw new Error("scene duration would strand an authored beat");
      }
      scene.duration = structuredClone(change.duration);
      let cursor = 0;
      next.scenes.forEach((entry) => {
        entry.start = rationalSeconds(cursor);
        cursor += Number(entry.duration.num) / Number(entry.duration.den);
      });
      break;
    }
    case "upsert_scene_beat": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content", "timing"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (!scene) throw new Error("resource not found: scene:" + change.scene_id);
      const start = Number(change.beat.start.num) / Number(change.beat.start.den);
      const duration = Number(change.beat.duration.num) / Number(change.beat.duration.den);
      if (
        !change.beat.label.trim() ||
        change.beat.label.length > 160 ||
        change.beat.objective.length > 4_000 ||
        !Number.isFinite(start) ||
        !Number.isFinite(duration) ||
        start < 0 ||
        duration <= 0 ||
        start + duration > Number(scene.duration.num) / Number(scene.duration.den)
      ) {
        throw new Error("scene beat is outside editable bounds");
      }
      if (
        next.narrative.beats.some((beat) => beat.id === change.beat.id) ||
        next.scenes.some((entry) =>
          entry.id !== scene.id && entry.beats.some((beat) => beat.id === change.beat.id)
        )
      ) {
        throw new Error("beat identity already belongs to another project resource");
      }
      const existing = scene.beats.findIndex((beat) => beat.id === change.beat.id);
      if (existing >= 0) {
        scene.beats[existing] = structuredClone(change.beat);
      } else {
        if (scene.beats.length >= 32) throw new Error("scene has more than 32 authored beats");
        scene.beats.push(structuredClone(change.beat));
      }
      scene.beats.sort((left, right) => {
        const leftStart = Number(left.start.num) / Number(left.start.den);
        const rightStart = Number(right.start.num) / Number(right.start.den);
        return leftStart - rightStart || left.id.localeCompare(right.id);
      });
      break;
    }
    case "remove_scene_beat": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content", "timing"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (!scene) throw new Error("resource not found: scene:" + change.scene_id);
      if (!scene.beats.some((beat) => beat.id === change.beat_id)) {
        throw new Error("resource not found: beat:" + change.beat_id);
      }
      scene.beats = scene.beats.filter((beat) => beat.id !== change.beat_id);
      break;
    }
    case "add_canvas_node": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content", "position"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (!scene) throw new Error("resource not found: scene:" + change.scene_id);
      if (!change.node.name.trim() || !change.node.kind.trim()) throw new Error("canvas node identity is invalid");
      if (scene.nodes.some((node) => node.id === change.node.id)) throw new Error("canvas node identity already exists in scene");
      scene.nodes.push(structuredClone(change.node));
      break;
    }
    case "remove_canvas_node": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content", "position"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (!scene) throw new Error("resource not found: scene:" + change.scene_id);
      if (!scene.nodes.some((node) => node.id === change.node_id)) throw new Error("resource not found: node:" + change.node_id);
      if (scene.nodes.some((node) => node.parent_id === change.node_id)) throw new Error("canvas node still has children; reparent them before removal");
      if (scene.nodes.some((node) => node.relations.some((relation) => relation.target_id === change.node_id))) {
        throw new Error("canvas node still has incoming relations; remove them before removal");
      }
      scene.nodes = scene.nodes.filter((node) => node.id !== change.node_id);
      break;
    }
    case "transform_canvas_node": {
      assertUnlocked(next, sceneResource(change.scene_id), ["position"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      const node = scene?.nodes.find((entry) => entry.id === change.node_id);
      if (!node) throw new Error("resource not found: node:" + change.node_id);
      const changesPosition = node.x !== change.transform.x || node.y !== change.transform.y;
      const changesSize = node.width !== change.transform.width || node.height !== change.transform.height;
      const changesRotation = node.rotation_deg !== change.transform.rotation_deg;
      const changesOpacity = node.opacity !== change.transform.opacity;
      if (
        (changesPosition && node.property_locks.includes("position")) ||
        (changesSize && node.property_locks.includes("size")) ||
        (changesRotation && node.property_locks.includes("rotation")) ||
        (changesOpacity && node.property_locks.includes("opacity"))
      ) throw new Error("resource is locked: node transform property");
      Object.assign(node, change.transform);
      break;
    }
    case "set_canvas_keyframe": {
      assertUnlocked(next, sceneResource(change.scene_id), ["position"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      const node = scene?.nodes.find((entry) => entry.id === change.node_id);
      if (!scene || !node) throw new Error("resource not found: node:" + change.node_id);
      const at = Number(change.keyframe.at.num) / Number(change.keyframe.at.den);
      const duration = Number(scene.duration.num) / Number(scene.duration.den);
      const { property, value } = change.keyframe;
      if (!Number.isFinite(at) || at < 0 || at >= duration || !Number.isFinite(value)) {
        throw new Error("canvas keyframe is out of bounds");
      }
      if ((property === "width" || property === "height") && value < 0) {
        throw new Error("canvas keyframe value is out of bounds");
      }
      if (property === "opacity" && (value < 0 || value > 1)) {
        throw new Error("canvas keyframe value is out of bounds");
      }
      const lock = property === "x" || property === "y"
        ? "position"
        : property === "width" || property === "height"
          ? "size"
          : property === "rotation_deg"
            ? "rotation"
            : "opacity";
      if (node.property_locks.includes(lock)) throw new Error("resource is locked: node motion property");
      const index = node.keyframes.findIndex((keyframe) =>
        keyframe.at.num === change.keyframe.at.num
        && keyframe.at.den === change.keyframe.at.den
        && keyframe.property === property
      );
      if (index >= 0) node.keyframes[index] = structuredClone(change.keyframe);
      else {
        if (node.keyframes.length >= 128) throw new Error("canvas keyframe budget exceeded");
        node.keyframes.push(structuredClone(change.keyframe));
      }
      node.keyframes.sort((left, right) => {
        const leftAt = Number(left.at.num) / Number(left.at.den);
        const rightAt = Number(right.at.num) / Number(right.at.den);
        return leftAt - rightAt || left.property.localeCompare(right.property);
      });
      break;
    }
    case "set_canvas_position_keyframe": {
      assertUnlocked(next, sceneResource(change.scene_id), ["position"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      const node = scene?.nodes.find((entry) => entry.id === change.node_id);
      if (!scene || !node) throw new Error("resource not found: node:" + change.node_id);
      const at = Number(change.at.num) / Number(change.at.den);
      const duration = Number(scene.duration.num) / Number(scene.duration.den);
      if (!Number.isFinite(at) || at < 0 || at >= duration ||
          !Number.isFinite(change.x) || !Number.isFinite(change.y)) {
        throw new Error("atomic canvas position keyframe is out of bounds");
      }
      if (node.property_locks.includes("position")) {
        throw new Error("resource is locked: node motion property");
      }
      const keys: CanvasKeyframe[] = [
        { at: change.at, property: "x", value: change.x, interpolation: change.interpolation },
        { at: change.at, property: "y", value: change.y, interpolation: change.interpolation },
      ];
      const existing = keys.filter((key) => node.keyframes.some((item) =>
        item.at.num === key.at.num && item.at.den === key.at.den &&
        item.property === key.property
      )).length;
      if (node.keyframes.length + keys.length - existing > 128) {
        throw new Error("atomic canvas position keyframes exceed node budget");
      }
      for (const key of keys) {
        const index = node.keyframes.findIndex((item) =>
          item.at.num === key.at.num && item.at.den === key.at.den &&
          item.property === key.property
        );
        if (index >= 0) node.keyframes[index] = structuredClone(key);
        else node.keyframes.push(structuredClone(key));
      }
      node.keyframes.sort((left, right) => {
        const atLeft = Number(left.at.num) / Number(left.at.den);
        const atRight = Number(right.at.num) / Number(right.at.den);
        return atLeft - atRight || left.property.localeCompare(right.property);
      });
      break;
    }
    case "set_canvas_linear_position_motion": {
      assertUnlocked(next, sceneResource(change.scene_id), ["position"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      const profile = next.deliverables.find((entry) => entry.id === change.deliverable_id);
      if (!scene || !profile) throw new Error("native linear motion source scene or saved delivery profile is missing");
      const node = scene.nodes.find((entry) => entry.id === change.node_id);
      if (!node) throw new Error("resource not found: node:" + change.node_id);
      if (scene.renderer !== "motion-canvas" || scene.beats.length > 0 ||
        node.parent_id !== null || node.kind === "group" ||
        scene.nodes.some((item) => item.parent_id === node.id)) {
        throw new Error("native linear motion requires an independent Motion Canvas object without authored beats");
      }
      if (node.property_locks.includes("position")) {
        throw new Error("resource is locked: node motion property");
      }
      if (node.keyframes.length > 0) {
        throw new Error("native linear motion never overwrites existing keyframes");
      }
      if (!Number.isFinite(change.start_x) || !Number.isFinite(change.start_y) ||
        (change.start_x === node.x && change.start_y === node.y) ||
        !Number.isInteger(change.end_frame) || change.end_frame < 1 || change.end_frame > 36_000) {
        throw new Error("invalid native linear position source or positive end frame");
      }
      const frameNum = BigInt(profile.frame_rate.num);
      const frameDen = BigInt(profile.frame_rate.den);
      const durationNum = BigInt(scene.duration.num);
      const durationDen = BigInt(scene.duration.den);
      if (frameNum <= 0n || frameDen <= 0n || durationNum <= 0n || durationDen <= 0n) {
        throw new Error("native linear position requires exact positive frame rate and scene duration");
      }
      let timeNum = BigInt(change.end_frame) * frameDen;
      let timeDen = frameNum;
      if (timeNum * durationDen >= durationNum * timeDen) {
        throw new Error("native linear position endpoint must stay inside scene duration");
      }
      let a = timeNum;
      let b = timeDen;
      while (b !== 0n) {
        [a, b] = [b, a % b];
      }
      timeNum /= a;
      timeDen /= a;
      const start = { num: "0", den: "1" };
      const end = { num: timeNum.toString(), den: timeDen.toString() };
      const keys: CanvasKeyframe[] = [
        { at: start, property: "x", value: change.start_x, interpolation: "linear" },
        { at: start, property: "y", value: change.start_y, interpolation: "linear" },
        { at: end, property: "x", value: node.x, interpolation: "linear" },
        { at: end, property: "y", value: node.y, interpolation: "linear" },
      ];
      node.keyframes.push(...keys);
      break;
    }
    case "remove_canvas_keyframe": {
      assertUnlocked(next, sceneResource(change.scene_id), ["position"]);
      const node = next.scenes.find((entry) => entry.id === change.scene_id)?.nodes.find((entry) => entry.id === change.node_id);
      if (!node) throw new Error("resource not found: node:" + change.node_id);
      const lock = change.property === "x" || change.property === "y"
        ? "position"
        : change.property === "width" || change.property === "height"
          ? "size"
          : change.property === "rotation_deg"
            ? "rotation"
            : "opacity";
      if (node.property_locks.includes(lock)) throw new Error("resource is locked: node motion property");
      const index = node.keyframes.findIndex((keyframe) =>
        keyframe.at.num === change.at.num
        && keyframe.at.den === change.at.den
        && keyframe.property === change.property
      );
      if (index < 0) throw new Error("resource not found: canvas keyframe");
      node.keyframes.splice(index, 1);
      break;
    }
    case "update_canvas_text": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content"]);
      const node = next.scenes.find((entry) => entry.id === change.scene_id)?.nodes.find((entry) => entry.id === change.node_id);
      if (!node) throw new Error("resource not found: node:" + change.node_id);
      if (node.property_locks.includes("text")) throw new Error("resource is locked: node text");
      node.text = change.text;
      break;
    }
    case "update_canvas_style": {
      assertUnlocked(next, sceneResource(change.scene_id), ["style"]);
      const node = next.scenes.find((entry) => entry.id === change.scene_id)?.nodes.find((entry) => entry.id === change.node_id);
      if (!node) throw new Error("resource not found: node:" + change.node_id);
      if (node.property_locks.includes("style")) throw new Error("resource is locked: node style");
      node.style = structuredClone(change.style);
      break;
    }
    case "reparent_canvas_node": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content", "position"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (!scene) throw new Error("resource not found: scene:" + change.scene_id);
      if (change.parent_id && (change.parent_id === change.node_id || !scene.nodes.some((node) => node.id === change.parent_id))) {
        throw new Error("canvas parent must be another node in the scene");
      }
      const node = scene.nodes.find((entry) => entry.id === change.node_id);
      if (!node) throw new Error("resource not found: node:" + change.node_id);
      if (node.property_locks.includes("parent") || node.property_locks.includes("order")) throw new Error("resource is locked: node hierarchy");
      node.parent_id = change.parent_id;
      node.z_index = change.z_index;
      break;
    }
    case "set_canvas_relations": {
      assertUnlocked(next, sceneResource(change.scene_id), ["position"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (!scene) throw new Error("resource not found: scene:" + change.scene_id);
      if (change.relations.length > 128) throw new Error("too many canvas relations");
      if (change.relations.some((relation) => relation.target_id === change.node_id || !scene.nodes.some((node) => node.id === relation.target_id))) {
        throw new Error("canvas relation target is invalid");
      }
      const node = scene.nodes.find((entry) => entry.id === change.node_id);
      if (!node) throw new Error("resource not found: node:" + change.node_id);
      if (node.property_locks.includes("position")) throw new Error("resource is locked: node relations");
      node.relations = structuredClone(change.relations);
      break;
    }
    case "set_node_property_lock": {
      assertUnlocked(next, sceneResource(change.scene_id), ["content"]);
      const node = next.scenes.find((entry) => entry.id === change.scene_id)?.nodes.find((entry) => entry.id === change.node_id);
      if (!node) throw new Error("resource not found: node:" + change.node_id);
      if (change.locked && !node.property_locks.includes(change.property)) node.property_locks.push(change.property);
      if (!change.locked) node.property_locks = node.property_locks.filter((property) => property !== change.property);
      break;
    }
    case "set_camera": {
      assertUnlocked(next, sceneResource(change.scene_id), ["position"]);
      const scene = next.scenes.find((entry) => entry.id === change.scene_id);
      if (!scene) throw new Error("resource not found: scene:" + change.scene_id);
      scene.camera = structuredClone(change.camera);
      break;
    }
    case "add_marker":
      assertUnlocked(next, projectResource(next), ["timing"]);
      next.markers.push({ id: crypto.randomUUID(), at: change.at, label: change.label });
      break;
    case "add_asset":
      assertUnlocked(next, projectResource(next), ["content"]);
      if (next.assets.some((asset) => asset.id === change.asset.id)) {
        throw new Error("duplicate asset id");
      }
      next.assets.push(structuredClone(change.asset));
      break;
    case "remove_asset": {
      assertUnlocked(next, projectResource(next), ["content"]);
      if (
        next.audio.voice_tracks.some((track) => track.asset_id === change.asset_id) ||
        next.brief.claims.some((claim) => claim.source?.kind === "asset" && claim.source.asset_id === change.asset_id)
      ) {
        throw new Error("asset is still referenced by project state");
      }
      if (!next.assets.some((asset) => asset.id === change.asset_id)) {
        throw new Error("resource not found: asset:" + change.asset_id);
      }
      next.assets = next.assets.filter((asset) => asset.id !== change.asset_id);
      break;
    }
    case "upsert_deliverable": {
      assertUnlocked(next, projectResource(next), ["content"]);
      const profile = structuredClone(change.profile);
      const frameRate = Number(profile.frame_rate.num) / Number(profile.frame_rate.den);
      if (
        !profile.name.trim() || profile.name.length > 160 ||
        !profile.language.trim() || profile.language.length > 64 ||
        !Number.isInteger(profile.width) || !Number.isInteger(profile.height) ||
        profile.width <= 0 || profile.height <= 0 || profile.width > 16384 || profile.height > 16384 ||
        ![44100, 48000, 96000].includes(profile.audio_sample_rate_hz) ||
        !Number.isFinite(frameRate) || frameRate <= 0 || frameRate > 240 ||
        profile.adaptation_notes.length > 64 ||
        profile.adaptation_notes.some((note) => !note.trim() || note.length > 1000)
      ) throw new Error("deliverable profile is invalid");
      if (profile.parent_profile_id === profile.id) {
        throw new Error("deliverable profile cannot derive from itself");
      }
      if ((profile.parent_profile_id === null) !== (profile.source_revision === null)) {
        throw new Error("derived deliverable profiles require both parent and source revision");
      }
      if (profile.source_revision !== null && profile.source_revision > next.revision) {
        throw new Error("deliverable source revision is newer than project state");
      }
      if (profile.parent_profile_id && profile.adaptation_notes.length === 0) {
        throw new Error("derived deliverable profile requires at least one adaptation note");
      }
      if (profile.framing_strategy === "crop" && !profile.crop_approved) {
        throw new Error("crop framing requires explicit approval");
      }
      if (profile.voice_track_id && !next.audio.voice_tracks.some((track) => track.id === profile.voice_track_id)) {
        throw new Error("deliverable voice track does not exist");
      }
      const includedSceneIds = profile.included_scene_ids.length > 0
        ? new Set(profile.included_scene_ids)
        : null;
      const textNodes = next.scenes
        .filter((scene) => !includedSceneIds || includedSceneIds.has(scene.id))
        .flatMap((scene) => scene.nodes.filter((node) => node.kind === "text"));
      const allTextNodeIds = new Set(next.scenes.flatMap((scene) => scene.nodes).filter((node) => node.kind === "text").map((node) => node.id));
      if (Object.keys(profile.text_overrides).some((nodeId) => !allTextNodeIds.has(nodeId))) {
        throw new Error("deliverable text override targets an unknown text node");
      }
      const included = new Set(profile.included_scene_ids);
      const protectedIds = new Set(profile.protected_scene_ids);
      if (included.size !== profile.included_scene_ids.length || protectedIds.size !== profile.protected_scene_ids.length) {
        throw new Error("deliverable scene lists cannot contain duplicates");
      }
      let lastSceneIndex = -1;
      for (const sceneId of profile.included_scene_ids) {
        const sceneIndex = next.scenes.findIndex((scene) => scene.id === sceneId);
        if (sceneIndex < 0) throw new Error("deliverable cut references an unknown scene");
        if (sceneIndex <= lastSceneIndex) throw new Error("deliverable cut must preserve approved project scene order");
        lastSceneIndex = sceneIndex;
      }
      for (const sceneId of profile.protected_scene_ids) {
        if (!next.scenes.some((scene) => scene.id === sceneId)) {
          throw new Error("deliverable protected scene does not exist");
        }
        if (profile.included_scene_ids.length > 0 && !included.has(sceneId)) {
          throw new Error("deliverable cut cannot omit a protected narrative scene");
        }
      }
      const parent = profile.parent_profile_id
        ? next.deliverables.find((candidate) => candidate.id === profile.parent_profile_id) ?? null
        : null;
      if (profile.parent_profile_id && !parent) {
        throw new Error("deliverable parent profile does not exist");
      }
      if (parent && profile.language !== parent.language) {
        if (parent.voice_track_id && profile.voice_track_id === parent.voice_track_id) {
          throw new Error("localized deliverable cannot reuse the parent language voice track");
        }
        if (textNodes.some((node) => !profile.text_overrides[node.id]?.trim())) {
          throw new Error("localized deliverable requires explicit text for every canvas text node in its cut");
        }
        if (profile.timing_locked && parent.voice_track_id) {
          if (!profile.voice_track_id) {
            throw new Error("localized deliverable with locked timing requires an explicit locale voice track");
          }
          const parentVoice = next.audio.voice_tracks.find((track) => track.id === parent.voice_track_id);
          const localizedVoice = next.audio.voice_tracks.find((track) => track.id === profile.voice_track_id);
          if (!parentVoice || !localizedVoice) throw new Error("deliverable voice track does not exist");
          if (
            parentVoice.measured_duration.num !== localizedVoice.measured_duration.num ||
            parentVoice.measured_duration.den !== localizedVoice.measured_duration.den
          ) {
            throw new Error("localized deliverable cannot keep source timing when measured voice duration changes; re-time the variant or disable timing lock");
          }
        }
      }
      const lineage = new Map(next.deliverables.map((candidate) => [candidate.id, candidate]));
      lineage.set(profile.id, profile);
      const visited = new Set<string>();
      let cursor = profile.parent_profile_id;
      while (cursor) {
        if (visited.has(cursor)) throw new Error("deliverable profile lineage contains a cycle");
        visited.add(cursor);
        const ancestor = lineage.get(cursor);
        if (!ancestor) throw new Error("deliverable parent profile does not exist");
        cursor = ancestor.parent_profile_id;
      }
      const duplicate = next.deliverables.find(
        (candidate) => candidate.id !== profile.id && candidate.name.trim().toLowerCase() === profile.name.trim().toLowerCase(),
      );
      if (duplicate) throw new Error("deliverable profile name is already used");
      const index = next.deliverables.findIndex((candidate) => candidate.id === profile.id);
      if (index >= 0) next.deliverables[index] = profile;
      else {
        if (next.deliverables.length >= 128) throw new Error("too many deliverable profiles");
        next.deliverables.push(profile);
      }
      break;
    }
    case "remove_deliverable": {
      assertUnlocked(next, projectResource(next), ["content"]);
      if (next.deliverables.length <= 1) throw new Error("a project must keep at least one deliverable profile");
      if (next.reviews.some((review) => review.anchor.profile_id === change.profile_id)) {
        throw new Error("deliverable profile is referenced by review history");
      }
      if (!next.deliverables.some((profile) => profile.id === change.profile_id)) {
        throw new Error("resource not found: deliverable:" + change.profile_id);
      }
      next.deliverables = next.deliverables.filter((profile) => profile.id !== change.profile_id);
      break;
    }
    case "set_active_voice_track": {
      assertUnlocked(next, projectResource(next), ["content"]);
      if (!next.audio.voice_tracks.some((track) => track.id === change.track_id)) {
        throw new Error("resource not found: voice-track:" + change.track_id);
      }
      next.audio.active_voice_track_id = change.track_id;
      break;
    }
    case "upsert_transcript_segment": {
      assertUnlocked(next, projectResource(next), ["content", "timing"]);
      const segment = structuredClone(change.segment);
      const start = Number(segment.start.num) / Number(segment.start.den);
      const end = Number(segment.end.num) / Number(segment.end.den);
      if (
        !next.audio.voice_tracks.some((track) => track.id === segment.voice_track_id) ||
        !segment.text.trim() || segment.text.length > 8000 ||
        !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start
      ) throw new Error("transcript segment is invalid");
      if (segment.alignment.kind === "measured") {
        throw new Error("measured transcript alignment requires a qualified alignment boundary");
      }
      const index = next.audio.transcript.findIndex((candidate) => candidate.id === segment.id);
      if (index >= 0) next.audio.transcript[index] = segment;
      else next.audio.transcript.push(segment);
      break;
    }
    case "remove_transcript_segment": {
      assertUnlocked(next, projectResource(next), ["content", "timing"]);
      if (next.audio.cues.some((cue) => cue.source_segment_id === change.segment_id)) {
        throw new Error("transcript segment is referenced by an audio cue");
      }
      if (!next.audio.transcript.some((segment) => segment.id === change.segment_id)) {
        throw new Error("resource not found: transcript-segment:" + change.segment_id);
      }
      next.audio.transcript = next.audio.transcript.filter((segment) => segment.id !== change.segment_id);
      break;
    }
    case "upsert_audio_cue": {
      assertUnlocked(next, projectResource(next), ["timing"]);
      const cue = structuredClone(change.cue);
      const at = Number(cue.at.num) / Number(cue.at.den);
      if (!cue.label.trim() || cue.label.length > 512 || !Number.isFinite(at) || at < 0) {
        throw new Error("audio cue is invalid");
      }
      if (cue.source_segment_id && !next.audio.transcript.some((segment) => segment.id === cue.source_segment_id)) {
        throw new Error("audio cue references an unknown transcript segment");
      }
      if (cue.evidence === "measured" || cue.evidence === "transcript_aligned") {
        throw new Error("measured cue evidence requires a qualified evidence boundary");
      }
      const index = next.audio.cues.findIndex((candidate) => candidate.id === cue.id);
      if (index >= 0) next.audio.cues[index] = cue;
      else next.audio.cues.push(cue);
      break;
    }
    case "remove_audio_cue": {
      assertUnlocked(next, projectResource(next), ["timing"]);
      if (!next.audio.cues.some((cue) => cue.id === change.cue_id)) {
        throw new Error("resource not found: audio-cue:" + change.cue_id);
      }
      next.audio.cues = next.audio.cues.filter((cue) => cue.id !== change.cue_id);
      break;
    }
    case "set_mix_intent": {
      assertUnlocked(next, projectResource(next), ["content"]);
      const values = [change.mix.voice_gain_db, change.mix.music_gain_db];
      if (values.some((value) => !Number.isFinite(value) || value < -120 || value > 24)) {
        throw new Error("mix gain is out of bounds");
      }
      next.audio.mix = structuredClone(change.mix);
      break;
    }
    case "set_visual_language":
      assertUnlocked(next, projectResource(next), ["style"]);
      next.visual_language = structuredClone(change.visual_language);
      break;
    case "add_proposal_set":
      if (change.proposal_set.base_revision !== next.revision) {
        throw new Error("proposal set base does not match current project revision");
      }
      next.proposal_sets.push(structuredClone(change.proposal_set));
      break;
    case "select_proposal": {
      const set = next.proposal_sets.find((entry) => entry.id === change.proposal_set_id);
      if (!set) throw new Error("resource not found: proposal-set:" + change.proposal_set_id);
      if (set.base_revision + 1 !== next.revision) throw new Error("proposal set is stale for current project revision");
      if (!set.proposals.some((proposal) => proposal.id === change.proposal_id)) {
        throw new Error("resource not found: proposal:" + change.proposal_id);
      }
      set.selected = change.proposal_id;
      break;
    }
    case "upsert_extension": {
      assertUnlocked(next, projectResource(next), ["content"]);
      const extension = structuredClone(change.extension);
      if (
        !extension.name.trim() || extension.name.length > 160 ||
        !extension.package_version.trim() || extension.package_version.length > 96 ||
        !/^[0-9a-f]{64}$/.test(extension.digest_sha256) ||
        !extension.license.trim() || extension.license.length > 128 ||
        !extension.source.trim() || extension.source.length > 2048
      ) throw new Error("extension descriptor is invalid");
      if (extension.enabled && extension.rights_status !== "cleared") {
        throw new Error("extension rights must be cleared before opt-in");
      }
      if (extension.enabled && next.extensions.some(
        (candidate) => candidate.id !== extension.id && candidate.enabled && candidate.kind === extension.kind,
      )) throw new Error("another extension of this kind is already enabled");
      const renderer = extension.kind === "remotion-renderer"
        ? "remotion"
        : extension.kind === "manim-gl-renderer" ? "manim-gl" : null;
      if (!extension.enabled && renderer && next.scenes.some((scene) => scene.renderer === renderer)) {
        throw new Error("extension cannot be disabled while its renderer is in use");
      }
      const index = next.extensions.findIndex((candidate) => candidate.id === extension.id);
      if (index >= 0) next.extensions[index] = extension;
      else next.extensions.push(extension);
      break;
    }
    case "remove_extension": {
      assertUnlocked(next, projectResource(next), ["content"]);
      const extension = next.extensions.find((candidate) => candidate.id === change.extension_id);
      if (!extension) throw new Error("resource not found: extension:" + change.extension_id);
      const renderer = extension.kind === "remotion-renderer"
        ? "remotion"
        : extension.kind === "manim-gl-renderer" ? "manim-gl" : null;
      if (renderer && next.scenes.some((scene) => scene.renderer === renderer)) {
        throw new Error("extension cannot be removed while its renderer is in use");
      }
      next.extensions = next.extensions.filter((candidate) => candidate.id !== change.extension_id);
      break;
    }
    case "upsert_handoff": {
      assertUnlocked(next, projectResource(next), ["content"]);
      const binding = structuredClone(change.binding);
      if (
        !binding.external_id.trim() || binding.external_id.length > 256 ||
        !binding.local_resource.trim() || binding.local_resource.length > 512 ||
        (binding.external_revision !== null && (!binding.external_revision.trim() || binding.external_revision.length > 256))
      ) throw new Error("handoff binding is invalid");
      const output = binding.direction === "artifact_output" || binding.direction === "evidence_output";
      const exactDigest = binding.artifact_sha256 !== null && /^[0-9a-f]{64}$/.test(binding.artifact_sha256);
      if (output !== exactDigest) {
        throw new Error("artifact/evidence outputs require an exact digest; context bindings do not carry one");
      }
      const localExists =
        binding.local_resource === projectResource(next) ||
        (binding.local_resource.startsWith("scene:") && next.scenes.some((scene) => "scene:" + scene.id === binding.local_resource)) ||
        (binding.local_resource.startsWith("asset:") && next.assets.some((asset) => "asset:" + asset.id === binding.local_resource)) ||
        (binding.local_resource.startsWith("deliverable:") && next.deliverables.some((profile) => "deliverable:" + profile.id === binding.local_resource));
      if (!localExists) throw new Error("handoff references an unknown local resource");
      const index = next.handoffs.findIndex((candidate) => candidate.id === binding.id);
      if (index >= 0) next.handoffs[index] = binding;
      else next.handoffs.push(binding);
      break;
    }
    case "remove_handoff": {
      assertUnlocked(next, projectResource(next), ["content"]);
      if (!next.handoffs.some((candidate) => candidate.id === change.binding_id)) {
        throw new Error("resource not found: handoff:" + change.binding_id);
      }
      next.handoffs = next.handoffs.filter((candidate) => candidate.id !== change.binding_id);
      break;
    }
    case "record_model_invocation": {
      if (change.receipt.base_revision !== next.revision) {
        throw new Error("model invocation base must match the current project revision");
      }
      if (!change.receipt.resource_refs.length || !change.receipt.data_classes.length) {
        throw new Error("model invocation receipt is incomplete");
      }
      if (new Set(change.receipt.data_classes).size !== change.receipt.data_classes.length) {
        throw new Error("model invocation data classes contain duplicates");
      }
      if (next.model_invocations.some((receipt) => receipt.id === change.receipt.id)) {
        throw new Error("duplicate model invocation receipt id");
      }
      next.model_invocations.push(structuredClone(change.receipt));
      break;
    }
    case "create_branch": {
      const name = change.name.trim();
      if (!name || name.length > 120 || next.branches.some((branch) => branch.name.toLowerCase() === name.toLowerCase())) {
        throw new Error("branch name is invalid or already used");
      }
      saveActiveWorkspace(next);
      const branchId = crypto.randomUUID();
      const state = captureBranchState(next);
      next.branches.push({
        id: branchId,
        name,
        parent_branch: next.active_branch,
        base_revision: next.revision,
        head_revision: next.revision,
        protected: false,
        created_at: new Date().toISOString(),
      });
      next.branch_workspaces.push({
        branch_id: branchId,
        base_revision: next.revision,
        base_state: structuredClone(state),
        current_state: state,
      });
      break;
    }
    case "checkout_branch": {
      if (change.branch_id === next.active_branch) break;
      saveActiveWorkspace(next);
      const workspace = next.branch_workspaces.find((entry) => entry.branch_id === change.branch_id);
      if (!workspace) throw new Error("resource not found: branch:" + change.branch_id);
      restoreBranchState(next, workspace.current_state);
      next.active_branch = change.branch_id;
      break;
    }
    case "merge_branch": {
      if (change.source_branch_id === next.active_branch) throw new Error("cannot merge a branch into itself");
      saveActiveWorkspace(next);
      const sourceBranch = next.branches.find((branch) => branch.id === change.source_branch_id);
      if (!sourceBranch) throw new Error("resource not found: branch:" + change.source_branch_id);
      if (sourceBranch.parent_branch !== next.active_branch) {
        throw new Error("first-party merge currently requires the source branch to descend directly from the active target");
      }
      const sourceWorkspace = next.branch_workspaces.find((entry) => entry.branch_id === change.source_branch_id);
      if (!sourceWorkspace) throw new Error("resource not found: branch:" + change.source_branch_id);
      const merged = mergeBranchState(
        sourceWorkspace.base_state,
        captureBranchState(next),
        sourceWorkspace.current_state,
      );
      restoreBranchState(next, merged);
      next.merges.push({
        id: crypto.randomUUID(),
        source_branch: change.source_branch_id,
        target_branch: next.active_branch,
        base_revision: sourceWorkspace.base_revision,
        committed_revision: null,
        merged_at: new Date().toISOString(),
      });
      break;
    }
    case "add_review":
      next.reviews.push({
        id: crypto.randomUUID(),
        kind: change.kind,
        anchor: {
          resource: change.resource,
          branch_id: next.active_branch,
          revision: next.revision,
          start: structuredClone(change.start),
          end: structuredClone(change.end),
          locale: change.locale,
          profile_id: change.profile_id,
        },
        body: change.body,
        status: "open",
        resolution: null,
        created_at: new Date().toISOString(),
        resolved_at: null,
      });
      break;
    case "resolve_review": {
      const review = next.reviews.find((entry) => entry.id === change.review_id);
      if (!review) throw new Error("resource not found: review:" + change.review_id);
      if (!change.resolution.trim()) throw new Error("review resolution is out of bounds");
      review.status = "resolved";
      review.resolution = change.resolution;
      review.resolved_at = new Date().toISOString();
      break;
    }
    case "reopen_review": {
      const review = next.reviews.find((entry) => entry.id === change.review_id);
      if (!review) throw new Error("resource not found: review:" + change.review_id);
      review.status = "needs_recheck";
      review.resolution = null;
      review.resolved_at = null;
      break;
    }
    case "set_lock":
      if (!next.locks.some((lock) => lock.resource === change.resource && lock.kind === change.kind)) {
        next.locks.push({ id: crypto.randomUUID(), resource: change.resource, kind: change.kind, note: change.note });
      }
      break;
    case "remove_lock":
      if (!next.locks.some((lock) => lock.id === change.lock_id)) throw new Error("resource not found: lock:" + change.lock_id);
      next.locks = next.locks.filter((lock) => lock.id !== change.lock_id);
      break;
  }
  next.revision += 1;
  const activeBranch = next.branches.find((branch) => branch.id === next.active_branch);
  if (!activeBranch) throw new Error("active branch is missing");
  activeBranch.head_revision = next.revision;

  if (change.type === "merge_branch") {
    const pending = [...next.merges].reverse().find(
      (merge) => merge.target_branch === next.active_branch && merge.committed_revision === null,
    );
    if (pending) pending.committed_revision = next.revision;
  }

  const reviewPreservingChanges = new Set<Change["type"]>([
    "create_branch",
    "checkout_branch",
    "add_review",
    "resolve_review",
    "reopen_review",
    "record_model_invocation",
    "set_lock",
    "remove_lock",
  ]);
  if (!reviewPreservingChanges.has(change.type)) {
    next.reviews.forEach((review) => {
      if (
        review.anchor.branch_id === next.active_branch &&
        review.anchor.revision < next.revision &&
        review.status === "resolved"
      ) {
        review.status = "needs_recheck";
        review.resolution = null;
        review.resolved_at = null;
      }
    });
  }

  next.updated_at = new Date().toISOString();
  if (record) {
    browserEvents.push({ revision: next.revision, change: structuredClone(change), created_at: next.updated_at });
    browserState.project = structuredClone(next);
  }
  return next;
}

function scopedEditChange(scene_id: string, edit: ScopedCanvasEdit): Change {
  switch (edit.kind) {
    case "text": return { type:"update_canvas_text", scene_id, node_id:edit.node_id, text:edit.text };
    case "style": return { type:"update_canvas_style", scene_id, node_id:edit.node_id, style:edit.style };
    case "transform": return { type:"transform_canvas_node", scene_id, node_id:edit.node_id, transform:edit.transform };
    case "keyframe": return { type:"set_canvas_keyframe", scene_id, node_id:edit.node_id, keyframe:edit.keyframe };
  }
}

/** Pure editorial A/B: no journal writes, persistence or renderer evidence. */
export async function previewCreativePatch(project: Project, patch: CreativePatch) {
  assertUnlocked(project, projectResource(project), ["content","style","position","timing"]);
  if (patch.base_revision !== project.revision || !patch.rationale.trim() || new TextEncoder().encode(patch.rationale).length > 4000
    || /[\u0000-\u0009\u000b-\u001f\u007f]/u.test(patch.rationale) || !patch.edits.length || patch.edits.length > 128) throw new Error("Creative patch is stale or outside its bounded scope.");
  const source = project.scenes.find(s => s.id === patch.scene_id);
  const ids = new Set(patch.edits.map(edit => edit.node_id));
  if (!source || [...ids].some(id => !source.nodes.some(n => n.id === id))) throw new Error("Patch target is outside the selected scene.");
  let candidate = structuredClone(project);
  for (const edit of patch.edits) candidate = await simulateChange(candidate, scopedEditChange(patch.scene_id,edit), "editorial-preview", false);
  const after = candidate.scenes.find(s => s.id === patch.scene_id)!;
  let num = BigInt(source.start.num)*BigInt(source.duration.den)+BigInt(source.duration.num)*BigInt(source.start.den);
  let den = BigInt(source.start.den)*BigInt(source.duration.den);
  let a = num < 0n ? -num : num, b = den;
  while (b !== 0n) { const r = a%b; a=b; b=r; }
  num /= a || 1n; den /= a || 1n;
  return { source_revision:project.revision, scene_id:source.id,
    before:source.nodes.filter(n => ids.has(n.id)), after:after.nodes.filter(n => ids.has(n.id)),
    dirty_start:source.start, dirty_end:{num:String(num),den:String(den)},
    kind:"semantic_diff_full_scene_invalidation_not_pixel_verification" as const };
}
