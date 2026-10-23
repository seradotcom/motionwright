import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { PreviewSurface } from "../../src/App";
import { fixtureProject } from "../../src/fixture";
import { seconds, type MotionCanvasRenderEvidence, type MltAvMasterEvidence, type Project } from "../../src/types";
import {
  captureDecodedPreview, PreviewRecoveryStore, PREVIEW_RECOVERY_DB, PREVIEW_RECOVERY_STORE,
  previewRecoveryScope, type RetainedPreviewFrame,
} from "../../src/previewRecovery";
import "../../src/styles.css";

// The real App PreviewSurface, NativeFrameStage, recovery hook, browser PNG
// decoder and IndexedDB run here. The Tauri reply is explicitly synthetic.
// This exercises caller/persistence behavior, not StudioService, renderer,
// installed WebView, native acceptance or the formal ACC-010 verdict.
const fixtureKey = "TEST-ONLY.preview-recovery.props.v1";
const firstVisit = sessionStorage.getItem(fixtureKey) === null;
let project = firstVisit ? structuredClone(fixtureProject)
  : JSON.parse(sessionStorage.getItem(fixtureKey)!) as Project;
let sceneId = project.scenes.find(scene => scene.renderer === "motion-canvas")!.id;
let profileId = project.deliverables[0].id, playhead = 0, mounted = true;
let disconnected = !firstVisit, hold = false;
const png = Uint8Array.from(atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4XyX4HwAGkAKKXzfbLwAAAABJRU5ErkJggg=="), c => c.charCodeAt(0));
const root = createRoot(document.getElementById("preview-root")!);
const reads: Array<{ project_id: string; generation: string; revision: number; token: string; frame_index: number }> = [];
const held: Array<{ resolve(value: Uint8Array): void; reject(reason: Error): void }> = [];
let receipt: MotionCanvasRenderEvidence | null = null;
let master: MltAvMasterEvidence | null = null;
const avReads: Array<Record<string, unknown>> = [];
function rendered() {
  const scene = project.scenes.find(value => value.id === sceneId)!;
  receipt = { project_resource: "project:" + project.id, generation: project.generation,
    revision: project.revision, deliverable_id: profileId, frame_rate: { num: 30, den: 1 },
    segments: [{ segment_id: "synthetic-preview-segment", scene_ids: [scene.id], frame_count: 450,
      plan_ref: "synthetic-plan", fingerprint: "synthetic-fingerprint", job_ref: "synthetic-job",
      artifact: {}, verification: {} }],
    preview: [{ token: "synthetic-session-grant", segment_id: "synthetic-preview-segment", scene_ids: [scene.id], frame_count: 450 }],
  } as MotionCanvasRenderEvidence;
  playhead = seconds(scene.start);
}

type MasterAuthorityField = "id" | "generation" | "active_branch" | "revision" | "profile" | "frameToken" | "exportToken";
let authorityBackup: { project: Project; receipt: MotionCanvasRenderEvidence; master: MltAvMasterEvidence;
  profileId: string; sceneId: string; playhead: number } | null = null;
function renderedMaster() {
  const scenes = project.scenes.slice(0, 2);
  if (scenes.length !== 2 || scenes.some(scene => scene.renderer !== "motion-canvas")) {
    throw new Error("Synthetic AV continuity fixture requires two Canvas scenes.");
  }
  sceneId = scenes[0].id; profileId = project.deliverables[0].id;
  const sceneIds = scenes.map(scene => scene.id);
  const frames = Math.round(scenes.reduce((sum, scene) => sum + seconds(scene.duration), 0) * 30);
  receipt = { project_resource: "project:" + project.id, generation: project.generation,
    revision: project.revision, deliverable_id: profileId, frame_rate: { num: 30, den: 1 },
    segments: [{ segment_id: "synthetic-av-continuity", scene_ids: sceneIds, frame_count: frames,
      plan_ref: "synthetic-plan", fingerprint: "synthetic-fingerprint", job_ref: "synthetic-job",
      artifact: {}, verification: {} }],
    preview: [{ token: "synthetic-av-frame-grant", segment_id: "synthetic-av-continuity", scene_ids: sceneIds, frame_count: frames }],
  } as MotionCanvasRenderEvidence;
  master = {
    export_token: "synthetic-av-continuity-grant", project_resource: receipt.project_resource,
    generation: project.generation, revision: project.revision, deliverable_id: profileId,
    motion_segment_id: "synthetic-av-continuity", frame_rate: receipt.frame_rate, frame_count: frames,
    mezzanine: {}, source_audio: { relative_path: "synthetic.wav", sha256: "a".repeat(64), sample_rate: 48000, channels: 2 },
    master: { profile: "h264-aac-mp4", artifact: { sha256: "b".repeat(64) } }, decoded_audio: {}, sync: null,
  } as MltAvMasterEvidence;
  playhead = seconds(scenes[0].start);
}

if (firstVisit) rendered();
const bridge = window as typeof window & { __TAURI_INTERNALS__: { invoke(command: string, args?: Record<string, unknown>): Promise<unknown> } };
bridge.__TAURI_INTERNALS__ = { async invoke(command, args) {
  if (command === "review_native_av_master") {
    avReads.push(structuredClone(args?.request as Record<string, unknown>));
    // Intentionally unresolved: this fixture proves component lifetime and
    // source-scoped reads only. No synthetic bytes pretend to decode as MP4.
    return new Promise<never>(() => {});
  }
  if (command !== "preview_native_frame") throw new Error("Unsupported synthetic preview command " + command);
  const request = args?.request as (typeof reads)[number];
  reads.push(structuredClone(request));
  if (disconnected) throw new Error("Synthetic renderer disconnected for recovery regression.");
  if (hold) return new Promise<Uint8Array>((resolve, reject) => held.push({ resolve, reject }));
  return new Uint8Array(png);
} };
function render() {
  sessionStorage.setItem(fixtureKey, JSON.stringify(project));
  const scene = project.scenes.find(value => value.id === sceneId) ?? null;
  const profile = project.deliverables.find(value => value.id === profileId) ?? null;
  const key = JSON.stringify([project.id, project.generation, project.active_branch, profile?.id]);
  flushSync(() => root.render(mounted ? <StrictMode><PreviewSurface key={key} project={project} scene={scene}
    playhead={playhead} rate={{ num: "30", den: "1" }} mode="ndf" profile={profile}
    renderEvidence={receipt} avEvidence={master} onInspectRender={() => { inspected++; }}
    onSeek={value => { playhead = value; render(); }} onPauseEditorial={() => {}}
    editorialPlaying={false}/></StrictMode> : null));
}
let inspected = 0;
const paint = () => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
async function database() {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(PREVIEW_RECOVERY_DB, 1);
    request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
  });
}
async function rawRows(): Promise<RetainedPreviewFrame[]> {
  const db = await database();
  try { return await new Promise((resolve, reject) => {
    const tx = db.transaction(PREVIEW_RECOVERY_STORE, "readonly"), request = tx.objectStore(PREVIEW_RECOVERY_STORE).getAll();
    tx.oncomplete = () => resolve(request.result as RetainedPreviewFrame[]);
    tx.onabort = tx.onerror = () => reject(tx.error);
  }); } finally { db.close(); }
}
async function changeRaw(id: string, mutate: (value: RetainedPreviewFrame) => RetainedPreviewFrame) {
  const db = await database();
  try { await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(PREVIEW_RECOVERY_STORE, "readwrite"), store = tx.objectStore(PREVIEW_RECOVERY_STORE), read = store.get(id);
    read.onsuccess = () => store.put(mutate(read.result as RetainedPreviewFrame));
    tx.oncomplete = () => resolve(); tx.onabort = tx.onerror = () => reject(tx.error);
  }); } finally { db.close(); }
}
let quotaRestore: (() => void) | null = null, releaseHash: (() => void) | null = null;
export const harness = {
  async withMaster() { renderedMaster(); render(); await paint(); },
  avReads: () => structuredClone(avReads),
  async masterScene(index: number) {
    sceneId = project.scenes[index].id; playhead = seconds(project.scenes[index].start);
    render(); await paint();
  },
  async changeMasterAuthority(field: MasterAuthorityField) {
    if (!receipt || !master || authorityBackup) throw new Error("Expected one current synthetic master.");
    authorityBackup = { project: structuredClone(project), receipt: structuredClone(receipt),
      master: structuredClone(master), profileId, sceneId, playhead };
    if (field === "id" || field === "generation") project[field] = crypto.randomUUID();
    else if (field === "active_branch") project.active_branch = "synthetic-other-branch";
    else if (field === "revision") project.revision++;
    else if (field === "profile") profileId = project.deliverables[1].id;
    else if (field === "frameToken") receipt.preview![0].token = "replacement-synthetic-frame-grant";
    else master.export_token = "replacement-synthetic-master-grant";
    receipt.project_resource = master.project_resource = "project:" + project.id;
    receipt.generation = master.generation = project.generation;
    receipt.revision = master.revision = project.revision;
    receipt.deliverable_id = master.deliverable_id = profileId;
    render(); await paint();
  },
  async restoreMasterAuthority() {
    if (!authorityBackup) throw new Error("No saved authority in this test.");
    ({ project, receipt, master, profileId, sceneId, playhead } = authorityBackup);
    authorityBackup = null; render(); await paint();
  },

  project: () => structuredClone(project), reads: () => structuredClone(reads), inspected: () => inspected,
  pending: () => held.length,
  async revise() { project.revision++; project.scenes.find(value => value.id === sceneId)!.objective += " edited"; render(); await paint(); },
  async seek(frame: number) { playhead = seconds(project.scenes.find(value => value.id === sceneId)!.start) + frame / 30; render(); await paint(); },
  async setIdentity(field: "id" | "generation" | "active_branch", value: string) { project[field] = value; project.revision++; render(); await paint(); },
  async profile(index: number) { profileId = project.deliverables[index].id; render(); await paint(); },
  async scene(index: number) { sceneId = project.scenes[index].id; render(); await paint(); },
  async unmount() { mounted = false; render(); await paint(); },
  async mount() { mounted = true; render(); await paint(); },
  async renderNew() { disconnected = false; rendered(); render(); await paint(); },
  disconnect() { disconnected = true; }, hold() { hold = true; },
  release(invalid = false) { const waiting = held.shift(); if (!waiting) throw new Error("No held preview read");
    const bytes = new Uint8Array(png); if (invalid) bytes.fill(0, 16); waiting.resolve(bytes); },
  async rows() { return (await rawRows()).map(({ blob: _blob, ...row }) => row); },
  async corrupt(id: string) { await changeRaw(id, row => {
    const bytes = new Uint8Array(png); bytes[40] ^= 1;
    return { ...row, blob: new Blob([bytes], { type: "image/png" }) };
  }); },
  async peerCopy(frameIndex = 1, writerId: string = crypto.randomUUID()) {
    const scope = previewRecoveryScope(project, project.scenes.find(value => value.id === sceneId)!, project.deliverables.find(value => value.id === profileId)!)!;
    const store = new PreviewRecoveryStore(writerId);
    const capture = captureDecodedPreview(scope, project.revision, "Peer saved scene", {
      bytes: png, frameIndex, frameCount: 450, pixelWidth: 1, pixelHeight: 1,
    }, store.writerId);
    return (await store.save(capture)).frame.id;
  },
  quotaFail() {
    const original = IDBObjectStore.prototype.add;
    IDBObjectStore.prototype.add = function(value: unknown, key?: IDBValidKey) {
      if (this.name === PREVIEW_RECOVERY_STORE) throw new DOMException("Deliberate component-test quota fault", "QuotaExceededError");
      return original.call(this, value, key);
    };
    quotaRestore = () => { IDBObjectStore.prototype.add = original; quotaRestore = null; };
  },
  quotaRestore() { quotaRestore?.(); },
  holdNextHash() {
    const original = crypto.subtle.digest.bind(crypto.subtle); let first = true;
    crypto.subtle.digest = async (...args: Parameters<SubtleCrypto["digest"]>) => {
      if (first) { first = false; await new Promise<void>(resolve => { releaseHash = resolve; }); }
      return original(...args);
    };
  },
  releaseHash() { if (!releaseHash) throw new Error("No held hash"); const release = releaseHash; releaseHash = null; release(); },
  hashPending: () => releaseHash !== null,
  freezeCaptureTime() { const value = Date.now() + 1_000; Date.now = () => value; },
};
declare global { interface Window { __PREVIEW_RECOVERY__: typeof harness } }
window.__PREVIEW_RECOVERY__ = harness;
render();
