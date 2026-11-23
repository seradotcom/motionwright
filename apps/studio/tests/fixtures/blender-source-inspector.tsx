/** Explicit browser transport fixture; no Blender, Broker, SQLite or physical acceptance. */
import { StrictMode } from "react";
import "../../src/styles.css";
import "../../src/production-design.css";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import NativeSourceWorkbench from "../../src/NativeSourceWorkbench";
import { BLENDER_RECIPE_MEDIA_TYPE, type BlenderSourceSnapshot, type BlenderAttemptProjection } from "../../src/blenderSourceApi";
import { fixtureProject } from "../../src/fixture";
import type { Project } from "../../src/types";

const capsuleId = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb";
const assetId = "dddddddd-4444-4444-8444-dddddddddddd";
const digest = "a".repeat(64);
const flags = { execution_grant_inherited: false, native_dispatch_performed: false,
  full_1200_frame_render: false, decoded_pixel_difference_verified: false,
  visual_acceptance: false, human_acceptance: false } as const;
let project: Project = structuredClone(fixtureProject); project.revision = 4;
project.scenes[0].renderer = "blender";
project.assets.push({ id: assetId, name: "product.json", media_type: BLENDER_RECIPE_MEDIA_TYPE,
  content_sha256: digest, source_revision: "explicit-browser-fixture" });
project.production_design = { ...(project.production_design ?? { plan: null, heroes: [], capsules: [] }), capsules: [{
  id: capsuleId, scene_id: project.scenes[0].id, source_asset_id: assetId, source_sha256: digest,
  label: "Preserved Blender product", editable_parameters: ["product.annotation.body"], native_editor_hint: "Blender",
  fidelity: { renderer: "Blender", renderer_version: "unobserved", visual: "unavailable",
    temporal: "unavailable", structural: "unavailable", editable: "unavailable",
    losses: ["Explicit browser transport fixture"], evidence_sha256: null },
}] };
const host = document.getElementById("source-root");
if (!host) throw new Error("Missing source fixture root.");
const root = createRoot(host);
const unavailable = async (): Promise<never> => { throw new Error("Mutation is outside this read-only fixture."); };
const render = () => flushSync(() => root.render(<StrictMode>
  <NativeSourceWorkbench project={project} scene={project.scenes[0]} busy={false}
    saveSource={unavailable} refreshProject={unavailable} exportSource={unavailable}/>
</StrictMode>));
const settle = () => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
type Read = { id: number; command: string; request: unknown };
type Pending = { record: Read; project: Project; resolve(value: unknown): void; reject(error: Error): void };
const records: Read[] = [], pending = new Map<number, Pending>();
const take = (id: number) => {
  const value = pending.get(id); if (!value) throw new Error("No pending read " + id);
  pending.delete(id); return value;
};
const source = (p: Project, annotation: string): BlenderSourceSnapshot => ({
  schema: "motionwright.blender.product-source-snapshot/1", project: structuredClone(p),
  scene_id: p.scenes[0].id, capsule_id: capsuleId, current_source: { asset_id: assetId, sha256: digest },
  original_recipe: { asset_id: assetId, sha256: digest },
  effective_recipe: { version: 1, namespace: "SavedProduct", coating: "ocean", roughness: 0.28,
    metallic: 0.65, samples: 64, annotation, camera_curve: "bezier_auto_clamped" },
  source_kind: "authored_recipe", lineage: null, attempt: null,
  source_cas_verified: true, source_acceptance_inherited: false, ...flags,
});
const unknownAttempt = (p: Project, request: string): BlenderAttemptProjection => ({
  schema: "motionwright.blender.product-attempt-projection/1", project: structuredClone(p),
  scene_id: p.scenes[0].id, capsule_id: capsuleId, attempt: {
    original_request_id: request, expected_before: { resource: "project:" + p.id, generation: p.generation, revision: 2 },
    claim_receipt_id: "11111111-2222-4333-8444-555555555555", terminal_receipt_id: null,
    state: "outcome_unknown", native_outcome_known: false, error_code: null,
  }, lineage: null, current_source_matches: false, source_cas_verified: false, effective_recipe: null,
  source_unavailable_reason: "original_attempt_has_no_completed_lineage", automatic_retry: false, ...flags,
});
(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {
  invoke(command: string, args: { request: unknown }) {
    if (!["native_blender_product_source", "native_blender_product_attempt"].includes(command) || !args?.request)
      return Promise.reject(new Error("Unexpected command in read-only fixture: " + command));
    const record = { id: records.length + 1, command, request: structuredClone(args.request) }; records.push(record);
    return new Promise<unknown>((resolve, reject) => pending.set(record.id, {
      record, project: structuredClone(project), resolve, reject,
    }));
  },
};
interface Harness {
  capsuleId: string;
  requests(): Read[];
  setRevision(revision: number): Promise<void>;
  source(id: number, annotation: string): Promise<void>;
  unknown(id: number): Promise<void>;
  reject(id: number): Promise<void>;
}
declare global { interface Window { __BLENDER_SOURCE__: Harness } }
window.__BLENDER_SOURCE__ = {
  capsuleId, requests: () => structuredClone(records),
  async setRevision(revision) { project = { ...project, revision }; render(); await settle(); },
  async source(id, annotation) { const read = take(id); read.resolve(source(read.project, annotation)); await settle(); },
  async unknown(id) {
    const read = take(id), request = read.record.request as { original_request_id: string };
    read.resolve(unknownAttempt(read.project, request.original_request_id)); await settle();
  },
  async reject(id) { take(id).reject(new Error("Obsolete read transport failure.")); await settle(); },
};
render();
