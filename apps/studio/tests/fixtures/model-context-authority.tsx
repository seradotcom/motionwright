import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import ModelContextInspector from "../../src/ModelContextInspector";
import { fixtureBootstrap } from "../../src/fixture";
import type { ModelRequestDraft, ModelRequestPreflight, Project, Scene } from "../../src/types";

/** Only props and transport are controlled here. The component and api.ts are real. */
export interface ModelContextScope {
  project_id: string;
  generation: string;
  revision: number;
  branch_id: string;
  scene_id: string;
}

export interface RecordedPreflight {
  id: number;
  command: "model_request_preflight";
  project_id: string;
  draft: ModelRequestDraft;
  scope: ModelContextScope;
  settled: boolean;
}

export interface ModelContextAuthorityHarness {
  scope(): ModelContextScope;
  requests(): RecordedPreflight[];
  setScope(patch: Partial<ModelContextScope>): Promise<void>;
  resolve(id: number, marker: string, patch?: Partial<ModelRequestPreflight>): Promise<void>;
  reject(id: number, message: string): Promise<void>;
  unmount(): Promise<void>;
  mount(): Promise<void>;
}

declare global {
  interface Window {
    __MODEL_CONTEXT_AUTHORITY__: ModelContextAuthorityHarness;
  }
}

type PendingPreflight = {
  record: RecordedPreflight;
  resolve(value: ModelRequestPreflight): void;
  reject(reason: Error): void;
};

const container = document.getElementById("model-context-root");
if (!container) throw new Error("Model context fixture root is missing.");
const root = createRoot(container);
let project: Project = structuredClone(fixtureBootstrap.project);
let scene: Scene = project.scenes[0];
if (!scene) throw new Error("Model context fixture requires a scene.");
let mounted = true;
const records: RecordedPreflight[] = [];
const pending = new Map<number, PendingPreflight>();

const scope = (): ModelContextScope => ({
  project_id: project.id,
  generation: project.generation,
  revision: project.revision,
  branch_id: project.active_branch,
  scene_id: scene.id,
});

const render = () => flushSync(() => root.render(mounted
  ? <StrictMode><ModelContextInspector project={project} scene={scene} /></StrictMode>
  : null));

// Wait through React's promise continuation and a paint before negative assertions.
const settleUi = () => new Promise<void>((resolve) => {
  requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
});

const takePending = (id: number) => {
  const request = pending.get(id);
  if (!request) throw new Error("No pending preflight " + id);
  pending.delete(id);
  request.record.settled = true;
  return request;
};

// Synthetic preflight data belongs exclusively to this component regression fixture.
// It does not implement or replace any inspector state, cancellation, or freshness logic.
const responseFor = (record: RecordedPreflight, marker: string): ModelRequestPreflight => {
  const { draft } = record;
  const disclosures = draft.resource_refs.flatMap((resource_ref) => draft.data_classes.map((data_class) => ({
    resource_ref,
    data_class,
    label: marker + " · synthetic transport fixture",
    media_type: null,
    estimated_bytes: 16,
    content_sha256: null,
    preview: marker,
    preview_truncated: false,
    untrusted_data: true,
  })));
  const sourceClasses = draft.data_classes.filter((dataClass) => dataClass !== "metadata");
  return {
    schema: "motionwright-model-preflight/1",
    project_id: record.project_id,
    generation: record.scope.generation,
    base_revision: record.scope.revision,
    ...structuredClone(draft),
    disclosures,
    estimated_total_bytes: disclosures.length * 16,
    source_data_classes: sourceClasses,
    explicit_source_consent_required: draft.provider_kind === "remote" && sourceClasses.length > 0,
    fallback_provider: null,
    studio_network_dispatch_supported: false,
    network_dispatched: false,
    fingerprint_sha256: "component-test-" + marker,
  };
};

// The actual api.ts calls the actual @tauri-apps/api invoke entry point. Only this
// read-only transport is delayed; no product API module or React state is replaced.
const runtime = window as unknown as Record<string, unknown>;
runtime.__TAURI_INTERNALS__ = {
  invoke(command: string, args: { request: { project_id: string; draft: ModelRequestDraft } }) {
    if (command !== "model_request_preflight" || !args?.request) {
      return Promise.reject(new Error("Unexpected command in preflight-only fixture: " + command));
    }
    const record: RecordedPreflight = {
      id: records.length + 1,
      command,
      project_id: args.request.project_id,
      draft: structuredClone(args.request.draft),
      scope: scope(),
      settled: false,
    };
    records.push(record);
    return new Promise<ModelRequestPreflight>((resolve, reject) => {
      pending.set(record.id, { record, resolve, reject });
    });
  },
};

window.__MODEL_CONTEXT_AUTHORITY__ = {
  scope,
  requests: () => structuredClone(records),
  async setScope(patch) {
    const previousSceneId = scene.id;
    scene = { ...scene, id: patch.scene_id ?? scene.id };
    project = {
      ...project,
      id: patch.project_id ?? project.id,
      generation: patch.generation ?? project.generation,
      revision: patch.revision ?? project.revision,
      active_branch: patch.branch_id ?? project.active_branch,
      scenes: project.scenes.map((candidate) => candidate.id === previousSceneId ? scene : candidate),
    };
    render();
    await settleUi();
  },
  async resolve(id, marker, patch = {}) {
    const request = takePending(id);
    request.resolve({ ...responseFor(request.record, marker), ...patch });
    await settleUi();
  },
  async reject(id, message) {
    takePending(id).reject(new Error(message));
    await settleUi();
  },
  async unmount() {
    mounted = false;
    render();
    await settleUi();
  },
  async mount() {
    mounted = true;
    render();
    await settleUi();
  },
};

render();
