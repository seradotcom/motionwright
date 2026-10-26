/**
 * Local API admission regressions. Tauri invoke is an isolated unit fixture:
 * no grant authority, renderer result, provider call or listening is attested.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fixtureProject } from "./fixture";
import type { MotionCanvasFilmOptions, Project, ProjectEvent } from "./types";
import type { NativeDeliveryDispatchCheck } from "./api";
import {
  captureDeliveryDispatch, type DeliveryDispatchView, type ProfileMetadataDraft,
  profileCanonicalFields, profileRawFields, profileMetadataScope, profileMetadataKey,
  newProfileMetadataDraft, editProfileMetadata, proposedProfileMetadata, previewProfileMetadata,
  submitProfileMetadata, reconcileProfileMetadata, profileMetadataConflicts, resolveProfileMetadata,
  decodeProfileMetadata, loadProfileMetadata, persistProfileMetadata, discardUnreadableProfileMetadata,
  profileMetadataCopies, recoverProfileMetadata, rereadProfileMetadata, retainedProfileMetadataCopies,
  acknowledgeProfileMetadata, inspectProfileOutcome, separateProfileIntent, retainSeparateProfileIntent,
  readProfileWriter, verifyProfileWriter, profileCopyVerified, PROFILE_WRITER_PREFIX,
} from "./deliveryProfileDraft";
import { ProjectMutationGate, type ProjectRevision } from "./projectMutation";

const invokeFixture = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeFixture }));

let api: typeof import("./api");
const requestId = "abcdef00-0000-4000-8000-000000000333";
const grantToken = "abcdef00-0000-4000-8000-000000000444";
const destination = "/synthetic-output/master.mp4";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

function source() {
  const project: Project = structuredClone(fixtureProject);
  project.revision = 20;
  project.locks = [];
  for (const scene of project.scenes) scene.renderer = "motion-canvas";
  const profile = project.deliverables[0];
  profile.frame_rate = { num: "30", den: "1" };
  profile.included_scene_ids = [];
  const scene_intents: MotionCanvasFilmOptions["scene_intents"] = project.scenes.map(scene => ({
    scene_id: scene.id, role: "hook", archetype: "statement",
  }));
  project.production_design = {
    ...(project.production_design ?? { plan: null, heroes: [], capsules: [] }),
    delivery_recipes: {
      ...(project.production_design?.delivery_recipes ?? {}),
      [profile.id]: { schema: "motionwright.delivery-recipe/1", scene_intents, original_mix: null },
    },
  };
  const options: MotionCanvasFilmOptions = {
    frame_rate: { num: 30, den: 1 },
    font_family: "Instrument Sans Variable", mono_font_family: "IBM Plex Mono",
    scene_intents,
  };
  return { project, profileId: profile.id, options };
}

type Sample = ReturnType<typeof source>;
interface NativeCase {
  name: string;
  command: string;
  effect: "render_local" | "deliver_local";
  invoke: (sample: Sample, check: NativeDeliveryDispatchCheck) => Promise<unknown>;
}
const cases: NativeCase[] = [
  { name: "Film render", command: "render_motion_canvas", effect: "render_local",
    invoke: (sample, check) => api.renderMotionCanvas(sample.project, sample.profileId, sample.options, check) },
  { name: "single-segment master", command: "assemble_av_master", effect: "render_local",
    invoke: (sample, check) => api.assembleNativeAvMaster(sample.project, sample.profileId, "original-voice", "original-preview", check) },
  { name: "multi-segment master", command: "assemble_multisegment_av_master", effect: "render_local",
    invoke: (sample, check) => api.assembleNativeMultisegmentAvMaster(sample.project, sample.profileId, "original-voice", "original-preview", check) },
  { name: "verified master export", command: "export_native_av_master", effect: "deliver_local",
    invoke: (sample, check) => api.exportVerifiedNativeMaster(sample.project, "original-export", "  " + destination + "  ", true, check) },
];

beforeEach(async () => {
  vi.resetModules();
  invokeFixture.mockReset();
  vi.stubGlobal("window", { __TAURI_INTERNALS__: {} });
  vi.stubGlobal("crypto", { randomUUID: vi.fn(() => requestId) });
  api = await import("./api");
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

for (const target of cases) describe(target.name + " local admission", () => {
  it("refuses pending input before requesting any effect grant", async () => {
    const sample = source();
    const blocked = new Error("Local delivery draft is pending.");
    const check = vi.fn(() => { throw blocked; });
    await expect(target.invoke(sample, check)).rejects.toBe(blocked);
    expect(check).toHaveBeenCalledTimes(1);
    expect(invokeFixture).not.toHaveBeenCalled();
  });

  it("rereads the same gesture after a deferred grant and never dispatches changed input", async () => {
    const sample = source(), held = deferred<{ token: string }>();
    let current = true;
    const changed = new Error("The current delivery gesture changed before dispatch.");
    const check = vi.fn(() => { if (!current) throw changed; });
    invokeFixture.mockImplementation((command: string) => {
      if (command !== "issue_effect_grant") throw new Error("Unexpected native dispatch.");
      return held.promise;
    });
    const result = target.invoke(sample, check);
    const rejected = expect(result).rejects.toBe(changed);
    expect(invokeFixture).toHaveBeenCalledTimes(1);
    expect(check).toHaveBeenCalledTimes(1);
    current = false;
    held.resolve({ token: grantToken });
    await rejected;
    expect(check).toHaveBeenCalledTimes(2);
    expect(invokeFixture).toHaveBeenCalledTimes(1);
    const [command, args] = invokeFixture.mock.calls[0];
    expect(command).toBe("issue_effect_grant");
    expect(args.request).toEqual({
      effect: target.effect, project_id: sample.project.id,
      generation: sample.project.generation, revision: sample.project.revision,
      subject: target.effect === "render_local" ? requestId : destination,
    });
  });

  it("dispatches once with the original source, request and grant when the gesture stays current", async () => {
    const sample = source();
    // This object checks forwarding only; it is deliberately no production receipt.
    const returned = { fixture: "opaque API result, no native evidence" };
    const check = vi.fn();
    invokeFixture.mockImplementation((command: string) => {
      if (command === "issue_effect_grant") return Promise.resolve({ token: grantToken });
      if (command === target.command) return Promise.resolve(returned);
      throw new Error("Unexpected command.");
    });
    await expect(target.invoke(sample, check)).resolves.toBe(returned);
    expect(check).toHaveBeenCalledTimes(2);
    expect(invokeFixture.mock.calls.map(call => call[0])).toEqual(["issue_effect_grant", target.command]);
    const request = invokeFixture.mock.calls[1][1].request;
    expect(request.project_id).toBe(sample.project.id);
    expect(request.generation).toBe(sample.project.generation);
    expect(request.revision).toBe(sample.project.revision);
    expect(request.effect_grant).toBe(grantToken);
    if (target.effect === "render_local") {
      expect(request.request_id).toBe(requestId);
      expect(request.deliverable_id).toBe(sample.profileId);
    } else {
      expect(request.destination).toBe(destination);
      expect(request.export_token).toBe("original-export");
      expect(request.include_integrity_manifest).toBe(true);
    }
    expect(sample.project.revision).toBe(20);
  });

  it("preserves an original failure after dispatch instead of relabeling it as local cancellation or retrying", async () => {
    const sample = source();
    let current = true;
    const original = { code: "Conflict", uncertain: true, message: "Synthetic original failure fixture" };
    const check = vi.fn(() => { if (!current) throw new Error("Changed local gesture."); });
    invokeFixture.mockImplementation((command: string) => {
      if (command === "issue_effect_grant") return Promise.resolve({ token: grantToken });
      if (command !== target.command) throw new Error("Unexpected command.");
      current = false;
      return Promise.reject(original);
    });
    await expect(target.invoke(sample, check)).rejects.toBe(original);
    expect(check).toHaveBeenCalledTimes(2);
    expect(invokeFixture.mock.calls.map(call => call[0])).toEqual(["issue_effect_grant", target.command]);
  });
});

function viewFixture() {
  const project: ProjectRevision = {
    id: "project-a", generation: "generation-a", revision: 8, active_branch: "branch-a",
  };
  const view: DeliveryDispatchView = {
    currentProject: { project: { ...project }, serial: 0, busy: false },
    renderedProject: { ...project }, profileId: "profile-a",
    inputSerial: 0, renderedInputSerial: 0, actionKey: "original-token-and-options",
    ready: true, draftBlocked: false, mounted: true,
  };
  const intent = { project, profileId: "profile-a", inputSerial: 0, actionKey: view.actionKey };
  return { view, intent };
}

const staleViews: Array<{ name: string; change: (view: DeliveryDispatchView) => void }> = [
  { name: "active project", change: view => { view.currentProject.project!.id = "project-b"; } },
  { name: "restored generation", change: view => { view.currentProject.project!.generation = "generation-b"; } },
  { name: "saved revision", change: view => { view.currentProject.project!.revision++; } },
  { name: "active branch", change: view => { view.currentProject.project!.active_branch = "branch-b"; } },
  { name: "rendered source", change: view => { view.renderedProject.revision--; } },
  { name: "delivery profile", change: view => { view.profileId = "profile-b"; } },
  { name: "input before React renders", change: view => { view.inputSerial++; } },
  { name: "input after React renders", change: view => { view.inputSerial++; view.renderedInputSerial++; } },
  { name: "rendered input serial", change: view => { view.renderedInputSerial++; } },
  { name: "completed actor operation", change: view => { view.currentProject.serial++; } },
  { name: "pending actor operation", change: view => { view.currentProject.busy = true; } },
  { name: "source token or destination", change: view => { view.actionKey = "replacement-token-or-options"; } },
  { name: "unmounted view", change: view => { view.mounted = false; } },
  { name: "unready native intent", change: view => { view.ready = false; } },
  { name: "pending or conflicted draft", change: view => { view.draftBlocked = true; } },
  { name: "unavailable project", change: view => { view.currentProject.project = null; } },
];

describe("the current delivery gesture joins the existing Project actor", () => {
  it.each(staleViews)("refuses $name changed during the grant", ({ change }) => {
    const { view, intent } = viewFixture();
    const gesture = captureDeliveryDispatch(intent, () => view);
    expect(gesture.stillCurrent()).toBe(true);
    change(view);
    expect(() => gesture.beforeDispatch()).toThrow("native action was not sent");
    expect(gesture.stillCurrent()).toBe(false);
  });

  it("compares stable values and accepts a newly allocated equivalent view", () => {
    const sample = viewFixture();
    let view = sample.view;
    const gesture = captureDeliveryDispatch(sample.intent, () => view);
    view = structuredClone(view);
    expect(() => gesture.beforeDispatch()).not.toThrow();
    expect(gesture.stillCurrent()).toBe(true);
  });

  it("keeps an edited-then-restored input distinct from the original gesture", () => {
    const { view, intent } = viewFixture();
    const gesture = captureDeliveryDispatch(intent, () => view);
    view.profileId = "profile-b"; view.inputSerial++; view.renderedInputSerial++;
    view.profileId = "profile-a"; view.inputSerial++; view.renderedInputSerial++;
    expect(view.draftBlocked).toBe(false);
    expect(() => gesture.beforeDispatch()).toThrow("native action was not sent");
  });

  it("refuses pending inputs on initial capture instead of creating a grant opportunity", () => {
    const { view, intent } = viewFixture();
    view.draftBlocked = true;
    expect(() => captureDeliveryDispatch(intent, () => view)).toThrow("native action was not sent");
  });

  it("observes failed edits through the same actor even when the Project never changed", async () => {
    const { view, intent } = viewFixture();
    const actor = new ProjectMutationGate();
    const read = () => ({ ...view, currentProject: { project: view.currentProject.project, ...actor.activity } });
    const gesture = captureDeliveryDispatch(intent, read);
    const held = deferred<void>();
    const original = new Error("Synthetic original CAS refusal");
    const operation = vi.fn(async () => { await held.promise; throw original; });
    const editing = actor.run(intent.project, () => view.currentProject.project, operation, vi.fn());
    expect(actor.activity.busy).toBe(true);
    expect(() => gesture.beforeDispatch()).toThrow("native action was not sent");
    const rejected = expect(editing).rejects.toBe(original);
    held.resolve();
    await rejected;
    expect(actor.activity.busy).toBe(false);
    expect(view.currentProject.project).toEqual(intent.project);
    expect(gesture.stillCurrent()).toBe(false);
    expect(operation).toHaveBeenCalledTimes(1);
  });

  it("observes a failed creation without retrying it or replacing the active Project", async () => {
    const { view, intent } = viewFixture();
    const actor = new ProjectMutationGate();
    const read = () => ({ ...view, currentProject: { project: view.currentProject.project, ...actor.activity } });
    const gesture = captureDeliveryDispatch(intent, read);
    const original = new Error("Synthetic original creation refusal");
    const operation = vi.fn(async (): Promise<ProjectRevision> => { throw original; });
    await expect(actor.create(intent.project, () => view.currentProject.project, operation, vi.fn())).rejects.toBe(original);
    expect(actor.activity.busy).toBe(false);
    expect(view.currentProject.project).toEqual(intent.project);
    expect(gesture.stillCurrent()).toBe(false);
    expect(operation).toHaveBeenCalledTimes(1);
  });

  it("observes an actual A-B-A open sequence even if React still shows the original source", async () => {
    const { view, intent } = viewFixture();
    const actor = new ProjectMutationGate();
    let visible: ProjectRevision | null = { ...intent.project };
    const read = () => ({ ...view, currentProject: { project: visible, ...actor.activity } });
    const gesture = captureDeliveryDispatch(intent, read);
    for (const id of ["project-b", "project-a"]) {
      const target = { ...intent.project, id };
      await actor.read({ source: visible, current: () => visible, requestedProjectId: id,
        load: async () => target, project: result => result, publish: result => { visible = result; } });
    }
    expect(visible).toEqual(intent.project);
    expect(view.renderedProject).toEqual(intent.project);
    expect(actor.activity.busy).toBe(false);
    expect(gesture.stillCurrent()).toBe(false);
    expect(() => gesture.beforeDispatch()).toThrow("native action was not sent");
  });
});


// Raw metadata tests use an isolated Storage fixture and canonical Project
// values. Only the opt-in browser suite claims real service/SQLite behavior.
function metadataFixture() {
  const { project } = source();
  const profile = structuredClone(project.deliverables[0]);
  profile.brand_profile = null; profile.cut_label = null;
  profile.adaptation_notes = ["Initial decision"];
  profile.text_overrides = {};
  return { project, profile, current: profileCanonicalFields(profile), scope: profileMetadataScope(project, profile.id) };
}

function originalReceipt(f: ReturnType<typeof metadataFixture>, sent: ProfileMetadataDraft): Project {
  if (!sent.awaiting?.original) throw new Error("The test requires an original capture.");
  const project = structuredClone(f.project);
  project.revision = sent.awaiting.revision + 1;
  project.deliverables = project.deliverables.map(profile => profile.id === f.profile.id
    ? structuredClone(sent.awaiting!.original!.profile) : profile);
  return project;
}

let metadataCopyCounter = 0;
function metadataWriter() {
  return "12345678-1234-4234-8234-" + String(++metadataCopyCounter).padStart(12, "0");
}
function storageFixture() {
  const entries = new Map<string, string>();
  return { entries, storage: {
    get length() { return entries.size; },
    key: (index: number) => [...entries.keys()][index] ?? null,
    getItem: vi.fn((key: string): string | null => entries.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => { entries.set(key, value); }),
    removeItem: vi.fn((key: string) => { entries.delete(key); }),
  } };
}
const rawMetadataCases: [string, string][] = [
  ["name", "  Name still being edited  "], ["width", "1e"], ["height", ""], ["language", " es "],
  ["captions", "false"], ["caption_format", "srt"], ["video_codec", "av1"], ["audio_codec", "opus"],
  ["audio_sample_rate_hz", "96000"], ["brand_profile", " \t "], ["cut_label", "  cut raw  "],
  ["framing_strategy", "crop"], ["crop_approved", "true"], ["timing_locked", "true"],
  ["voice_track_id", "abcde000-1234-4234-8234-000000000111"],
  ["included_scene_ids", '["abcde000-1234-4234-8234-000000000222"]'],
  ["protected_scene_ids", '["abcde000-1234-4234-8234-000000000333"]'],
  ["burn_in_captions", "true"], ["frame_rate", "30000/1001"], ["color_space", "display_p3"],
  ["container", "mkv"], ["adaptation_notes", "  first decision \n\n second decision \n"],
  ["text:abcde000-1234-4234-8234-000000000444", "  localized text being typed  "],
];

describe("frame rate property order", () => {
  it("projects Rust JSON property order without changing the received exact integers", () => {
    const f = metadataFixture();
    const encoded = '{"den":"1001","num":"30000"}';
    const profile = { ...f.profile, frame_rate: JSON.parse(encoded) };
    expect(profileCanonicalFields(profile).frame_rate).toBe('{"num":"30000","den":"1001"}');
    expect(profileRawFields(profile).frame_rate).toBe("30000/1001");
    expect(JSON.stringify(profile.frame_rate)).toBe(encoded);
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "frame_rate", "30/1", profileCanonicalFields(profile));
    expect(decodeProfileMetadata(JSON.stringify(draft), f.scope)).toEqual(draft);
  });

  it("acknowledges all submitted fields from Rust JSON while preserving later frame-rate and incomplete input", () => {
    const f = metadataFixture(), before = { ...f.profile, frame_rate: { num: "24", den: "1" } };
    const current = profileCanonicalFields(before), id = metadataWriter();
    let draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "frame_rate", "30/1", current);
    draft = editProfileMetadata(draft, "language", "es-MX", current);
    const sent = submitProfileMetadata(draft, before, 20, id);
    let later = editProfileMetadata(sent, "frame_rate", "30000/1001", current);
    later = editProfileMetadata(later, "height", "1e", current);
    const partial = { ...before, frame_rate: JSON.parse('{"den":"1","num":"30"}') };
    const unconfirmed = reconcileProfileMetadata(later, profileCanonicalFields(partial), 21, f.scope);
    expect(unconfirmed.awaiting).toEqual(sent.awaiting);
    const receipt = originalReceipt(f, sent);
    const saved = receipt.deliverables.find(profile => profile.id === f.profile.id)!;
    saved.frame_rate = JSON.parse('{"den":"1","num":"30"}');
    const savedFields = profileCanonicalFields(saved);
    const observed = acknowledgeProfileMetadata(unconfirmed, id, receipt);
    expect(observed.awaiting).toBeUndefined();
    expect(observed.edits).toEqual({
      frame_rate: { base: '{"num":"30","den":"1"}', value: "30000/1001" },
      height: { base: current.height, value: "1e" },
    });
    expect(profileMetadataConflicts(observed, savedFields)).toEqual([]);
    expect(decodeProfileMetadata(JSON.stringify(observed), f.scope)).toEqual(observed);
    expect(sent.awaiting?.fields.frame_rate.target).toBe('{"num":"30","den":"1"}');
  });

  it("recovers historical den-num bases and pending targets without rewriting their original bytes", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture();
    const key = profileMetadataKey(f.scope, metadataWriter());
    const historical: ProfileMetadataDraft = {
      version: 1, scope: f.scope, revision: 20,
      edits: { frame_rate: { base: '{"den":"1","num":"24"}', value: "30000/1001" } },
      awaiting: { revision: 20, fields: { frame_rate: { raw: "30/1", target: '{"den":"1","num":"30"}' } } },
    };
    const raw = JSON.stringify(historical); entries.set(key, raw);
    const loaded = loadProfileMetadata(key, f.scope, 21, storage);
    expect(loaded.blocked).toBeNull(); expect(loaded.observedRaw).toBe(raw);
    expect(loaded.draft.edits.frame_rate.base).toBe('{"num":"24","den":"1"}');
    expect(loaded.draft.awaiting?.fields.frame_rate.target).toBe('{"num":"30","den":"1"}');
    const current = profileCanonicalFields({ ...f.profile, frame_rate: JSON.parse('{"den":"1","num":"30"}') });
    const observed = reconcileProfileMetadata(loaded.draft, current, 21, f.scope);
    expect(observed.awaiting).toEqual(loaded.draft.awaiting);
    expect(observed.edits.frame_rate).toEqual({ base: '{"num":"24","den":"1"}', value: "30000/1001" });
    expect(acknowledgeProfileMetadata(observed, metadataWriter(), { ...f.project, revision: 21 })).toEqual(observed);
    expect(entries.get(key)).toBe(raw);
    expect(storage.setItem).not.toHaveBeenCalled(); expect(storage.removeItem).not.toHaveBeenCalled();
  });

  it("keeps distinct i64 values exact rather than comparing rounded numbers or equal ratios", () => {
    const f = metadataFixture();
    const rate = JSON.parse('{"den":"9223372036854775806","num":"9223372036854775807"}');
    expect(profileCanonicalFields({ ...f.profile, frame_rate: rate }).frame_rate)
      .toBe('{"num":"9223372036854775807","den":"9223372036854775806"}');
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "frame_rate", "60/2", f.current);
    const sent = submitProfileMetadata(draft, f.profile, 20);
    const observed = reconcileProfileMetadata(sent, profileCanonicalFields({ ...f.profile, frame_rate: { den: "1", num: "30" } }), 21, f.scope);
    expect(observed.awaiting).toEqual(sent.awaiting);
    expect(observed.edits.frame_rate.value).toBe("60/2");
  });

  const invalidRates = [
    '{"num":"24","num":"30","den":"1"}',
    '{"num":"30","den":"1","den":"1"}',
    '{"den":"1","num":"30","approved":true}',
    '{"num":"30"}', '{"den":"1","num":30}', '{"den":1,"num":"30"}',
    '{"den":"0","num":"30"}', '{"den":"1","num":"0"}',
    '{"den":"1","num":"030"}', '{"den":"1","num":"241"}',
    '{"den":"9223372036854775808","num":"30"}',
  ];
  for (const encoded of invalidRates) it("rejects malformed saved or pending rate " + encoded, () => {
    const f = metadataFixture();
    const draft: ProfileMetadataDraft = { version: 1, scope: f.scope, revision: 20,
      edits: { frame_rate: { base: encoded, value: "30/1" } } };
    expect(() => decodeProfileMetadata(JSON.stringify(draft), f.scope)).toThrow();
    draft.edits.frame_rate.base = '{"num":"24","den":"1"}';
    draft.awaiting = { revision: 20, fields: { frame_rate: { raw: "30/1", target: encoded } } };
    expect(() => decodeProfileMetadata(JSON.stringify(draft), f.scope)).toThrow();
    expect(draft.awaiting.fields.frame_rate.target).toBe(encoded);
  });
});

describe("raw output metadata continuity", () => {
  for (const [field, raw] of rawMetadataCases) it("retains raw " + field + " across an unrelated saved revision", () => {
    const f = metadataFixture(), original = structuredClone(f.profile);
    const edited = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), field, raw, f.current);
    const later = reconcileProfileMetadata(edited, { ...f.current }, 21, f.scope);
    expect(later.edits[field]).toEqual({ base: f.current[field] ?? "null", value: raw });
    expect(later.revision).toBe(21);
    expect(later.awaiting).toBeUndefined();
    expect(f.profile).toEqual(original);
  });

  it("merges only edited fields into the latest profile and preserves an ambiguous saved notes array", () => {
    const f = metadataFixture();
    const edited = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "width", "001280", f.current);
    const latest = { ...f.profile, language: "fr", brand_profile: "Another author's brand", adaptation_notes: ["A\nB"] };
    const proposed = proposedProfileMetadata(latest, edited);
    expect(proposed).toEqual({ ...latest, width: 1280 });
    expect(edited.edits.width.value).toBe("001280");
    expect(latest.width).toBe(f.profile.width);
  });

  it("does not drop authored note lines when a peer saves a different array with the same display text", () => {
    const f = metadataFixture();
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "adaptation_notes", "A\nB", f.current);
    const latest = { ...f.profile, adaptation_notes: ["A\nB"] };
    const current = profileCanonicalFields(latest);
    const observed = reconcileProfileMetadata(draft, current, 21, f.scope);
    expect(observed.edits.adaptation_notes).toEqual({ base: f.current.adaptation_notes, value: "A\nB" });
    expect(profileMetadataConflicts(observed, current)).toEqual(["adaptation_notes"]);
    expect(proposedProfileMetadata(latest, observed).adaptation_notes).toEqual(["A", "B"]);
  });

  it("captures an explicit note-line edit even when a saved single note has the same display text", () => {
    const f = metadataFixture(), profile = { ...f.profile, adaptation_notes: ["A\nB"] }, id = metadataWriter();
    const current = profileCanonicalFields(profile);
    const edited = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "adaptation_notes", "A\nB", current);
    expect(edited.edits.adaptation_notes).toEqual({ base: JSON.stringify(["A\nB"]), value: "A\nB" });
    const sent = submitProfileMetadata(edited, profile, 20, id);
    expect(sent.awaiting?.fields.adaptation_notes.target).toBe(JSON.stringify(["A", "B"]));
    const unconfirmed = reconcileProfileMetadata(sent, current, 21, f.scope);
    expect(unconfirmed.awaiting).toEqual(sent.awaiting);
    expect(unconfirmed.edits).toEqual(edited.edits);
    const confirmed = acknowledgeProfileMetadata(unconfirmed, id, originalReceipt(f, sent));
    expect(confirmed.edits).toEqual({});
    expect(confirmed.awaiting).toBeUndefined();
  });

  it("converts optional blanks and note lines only for explicit submission while retaining the authored spelling", () => {
    const f = metadataFixture();
    let draft = newProfileMetadataDraft(f.scope, 20);
    for (const [field, value] of [["brand_profile", "  "], ["adaptation_notes", "  A \n\n B \n"]] as const) {
      draft = editProfileMetadata(draft, field, value, f.current);
    }
    const profile = proposedProfileMetadata(f.profile, draft);
    expect(profile.brand_profile).toBeNull();
    expect(profile.adaptation_notes).toEqual(["A", "B"]);
    const sent = submitProfileMetadata(draft, f.profile, 20);
    expect(sent.awaiting?.fields).toEqual({
      brand_profile: { raw: "  ", target: "null" },
      adaptation_notes: { raw: "  A \n\n B \n", target: '["A","B"]' },
    });
    expect(draft.edits.brand_profile.value).toBe("  ");
    expect(draft.awaiting).toBeUndefined();
  });

  for (const raw of ["", "-", "1e", "Infinity", "0x10", "0", "16385", "2.5"]) {
    it("rejects incomplete or invalid pixel input " + JSON.stringify(raw) + " without replacing it", () => {
      const f = metadataFixture();
      const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "width", raw, f.current);
      expect(() => proposedProfileMetadata(f.profile, draft)).toThrow();
      expect(draft.edits.width.value).toBe(raw);
      expect(previewProfileMetadata(f.profile, draft).width).toBe(f.profile.width);
      expect(draft.awaiting).toBeUndefined();
    });
  }

  it("uses UTF-8 limits without trimming valid profile names", () => {
    const f = metadataFixture();
    const valid = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "  Ámbito  ", f.current);
    expect(proposedProfileMetadata(f.profile, valid).name).toBe("  Ámbito  ");
    const tooManyBytes = editProfileMetadata(valid, "name", "é".repeat(81), f.current);
    expect(() => proposedProfileMetadata(f.profile, tooManyBytes)).toThrow("160 UTF-8 bytes");
    expect(tooManyBytes.edits.name.value).toBe("é".repeat(81));
  });

  it("distinguishes typed arrays with identical textarea display when detecting a changed base", () => {
    const f = metadataFixture();
    const before = { ...f.profile, adaptation_notes: ["A\nB"] };
    const current = { ...f.profile, adaptation_notes: ["A", "B"] };
    expect(profileRawFields(before).adaptation_notes).toBe(profileRawFields(current).adaptation_notes);
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "adaptation_notes", "My decision", profileCanonicalFields(before));
    expect(draft.edits.adaptation_notes.base).toBe('["A\\nB"]');
    expect(profileMetadataConflicts(draft, profileCanonicalFields(current))).toEqual(["adaptation_notes"]);
  });

  it("keeps equivalent numeric spelling until explicit review or the original confirmed reply", () => {
    const f = metadataFixture(), id = metadataWriter();
    f.profile.width = 1920; f.current = profileCanonicalFields(f.profile);
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "width", "001920", f.current);
    expect(reconcileProfileMetadata(draft, f.current, 21, f.scope).edits.width.value).toBe("001920");
    const sent = submitProfileMetadata(draft, f.profile, 21, id);
    const observed = reconcileProfileMetadata(sent, f.current, 22, f.scope);
    expect(observed.edits.width.value).toBe("001920");
    expect(observed.awaiting).toEqual(sent.awaiting);
    const confirmed = acknowledgeProfileMetadata(observed, id, originalReceipt(f, sent));
    expect(confirmed.edits).toEqual({});
    expect(confirmed.awaiting).toBeUndefined();
  });

  it("keeps C typed after B was sent and rebases C only to the original reply without replay", () => {
    const f = metadataFixture(), id = metadataWriter();
    const b = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Submitted B", f.current);
    const sent = submitProfileMetadata(b, f.profile, 20, id);
    const c = editProfileMetadata(sent, "name", "Current C", f.current);
    const unchanged = reconcileProfileMetadata(c, { ...f.current, name: '"Submitted B"' }, 21, f.scope);
    expect(unchanged.awaiting).toEqual(sent.awaiting);
    const observed = acknowledgeProfileMetadata(unchanged, id, originalReceipt(f, sent));
    expect(observed.edits.name).toEqual({ base: '"Submitted B"', value: "Current C" });
    expect(observed.awaiting).toBeUndefined();
    expect(profileMetadataConflicts(observed, { ...f.current, name: '"Submitted B"' })).toEqual([]);
    expect(sent.awaiting?.fields.name).toEqual({ raw: "Submitted B", target: '"Submitted B"' });
  });

  it("keeps A restored while B is unconfirmed and after the original B acknowledgement", () => {
    const f = metadataFixture(), originalName = f.profile.name, id = metadataWriter();
    const b = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Submitted B", f.current);
    const sent = submitProfileMetadata(b, f.profile, 20, id);
    const restored = editProfileMetadata(sent, "name", originalName, f.current);
    const unchanged = reconcileProfileMetadata(restored, f.current, 21, f.scope);
    expect(unchanged.edits.name.value).toBe(originalName);
    expect(unchanged.awaiting).toEqual(sent.awaiting);
    const observed = acknowledgeProfileMetadata(unchanged, id, originalReceipt(f, sent));
    expect(observed.edits.name).toEqual({ base: '"Submitted B"', value: originalName });
    expect(observed.awaiting).toBeUndefined();
  });

  it("requires the exact original full upsert reply and rejects partial or matching observed values", () => {
    const f = metadataFixture(), id = metadataWriter();
    let draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Submitted", f.current);
    draft = editProfileMetadata(draft, "height", "720", f.current);
    const sent = submitProfileMetadata(draft, f.profile, 20, id);
    const partial = reconcileProfileMetadata(sent, { ...f.current, name: '"Submitted"' }, 21, f.scope);
    expect(partial.edits).toEqual(sent.edits);
    expect(partial.awaiting).toEqual(sent.awaiting);
    const receipt = originalReceipt(f, sent), incomplete = structuredClone(receipt);
    incomplete.deliverables.find(profile => profile.id === f.profile.id)!.height = f.profile.height;
    expect(() => acknowledgeProfileMetadata(partial, id, incomplete)).toThrow("exact profile upsert");
    const matching = reconcileProfileMetadata(partial, { ...f.current, name: '"Submitted"', height: "720" }, 21, f.scope);
    expect(matching.awaiting).toEqual(sent.awaiting);
    const confirmed = acknowledgeProfileMetadata(matching, id, receipt);
    expect(confirmed.edits).toEqual({});
    expect(confirmed.awaiting).toBeUndefined();
  });

  for (const [label, revision, differentScope] of [
    ["same revision", 20, false], ["older revision", 19, false], ["different generation", 21, true],
  ] as const) it("does not confirm pending input from " + label, () => {
    const f = metadataFixture();
    const sent = submitProfileMetadata(editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Submitted", f.current), f.profile, 20);
    const scope = differentScope ? profileMetadataScope({ ...f.project, generation: "abcde000-1234-4234-8234-000000000555" }, f.profile.id) : f.scope;
    expect(reconcileProfileMetadata(sent, { ...f.current, name: '"Submitted"' }, revision, scope)).toEqual(sent);
  });

  it("keeps input and source revision while the visible project is behind a recovered draft", () => {
    const f = metadataFixture();
    const future = editProfileMetadata(newProfileMetadataDraft(f.scope, 30), "name", "Newer local input", f.current);
    expect(reconcileProfileMetadata(future, f.current, 20, f.scope)).toEqual(future);
    expect(() => submitProfileMetadata(future, f.profile, 20)).toThrow();
  });

  it("requires separate outcome review for UNKNOWN while keeping unsent field resolution independent", () => {
    const f = metadataFixture();
    let draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Submitted", f.current);
    draft = editProfileMetadata(draft, "height", "720", f.current);
    const sent = submitProfileMetadata(draft, f.profile, 20);
    expect(() => submitProfileMetadata(sent, f.profile, 20)).toThrow("before sending another save");
    const current = { ...f.current, name: '"A different author"' }, before = JSON.stringify(sent);
    expect(() => resolveProfileMetadata(sent, "name", current, "draft")).toThrow("UNKNOWN");
    expect(() => resolveProfileMetadata(sent, "height", current, "saved")).toThrow("UNKNOWN");
    expect(JSON.stringify(sent)).toBe(before);
    const kept = resolveProfileMetadata(draft, "name", current, "draft");
    expect(kept.edits.name).toEqual({ base: '"A different author"', value: "Submitted" });
    expect(kept.edits.height).toEqual(draft.edits.height);
    const saved = resolveProfileMetadata(kept, "height", current, "saved");
    expect(saved.edits).toEqual({ name: kept.edits.name });
    expect(saved.awaiting).toBeUndefined();
  });

  it("adds or deletes one text override without replacing the latest unrelated override", () => {
    const f = metadataFixture(), editedId = "abcde000-1234-4234-8234-000000000666", otherId = "abcde000-1234-4234-8234-000000000777";
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "text:" + editedId, " Authored text ", f.current);
    const latest = { ...f.profile, text_overrides: { [otherId]: "Other author's text" } };
    expect(proposedProfileMetadata(latest, draft).text_overrides).toEqual({ [otherId]: "Other author's text", [editedId]: " Authored text " });
    const existing = { ...latest, text_overrides: { ...latest.text_overrides, [editedId]: "Before" } };
    const removed = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "text:" + editedId, "", profileCanonicalFields(existing));
    expect(proposedProfileMetadata(existing, removed).text_overrides).toEqual({ [otherId]: "Other author's text" });
  });
});

describe("profile metadata recovery preserves original bytes", () => {
  it("returns A and B separately after A-B-A selection and a later revision", () => {
    const f = metadataFixture(), { storage } = storageFixture(), writer = metadataWriter();
    const scopeB = profileMetadataScope(f.project, "abcde000-1234-4234-8234-000000000888");
    const keyA = profileMetadataKey(f.scope, writer), keyB = profileMetadataKey(scopeB, writer);
    const a = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "width", "1e", f.current);
    const b = editProfileMetadata(newProfileMetadataDraft(scopeB, 20), "name", "B still being typed", f.current);
    persistProfileMetadata(keyA, a, storage); persistProfileMetadata(keyB, b, storage);
    expect(loadProfileMetadata(keyA, f.scope, 21, storage).draft.edits.width.value).toBe("1e");
    expect(loadProfileMetadata(keyB, scopeB, 21, storage).draft.edits.name.value).toBe("B still being typed");
    expect(storage.getItem(keyA)).toBe(JSON.stringify(a));
    expect(storage.getItem(keyB)).toBe(JSON.stringify(b));
  });

  it("preserves invalid raw values in a strict recovery roundtrip", () => {
    const f = metadataFixture();
    const raw = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "width", "1e", f.current);
    expect(decodeProfileMetadata(JSON.stringify(raw), f.scope)).toEqual(raw);
    expect(() => proposedProfileMetadata(f.profile, decodeProfileMetadata(JSON.stringify(raw), f.scope))).toThrow();
  });

  const malformed: [string, (draft: ReturnType<typeof newProfileMetadataDraft>) => string][] = [
    ["duplicate JSON key", d => JSON.stringify(d).replace('"version":1', '"version":9,"version":1')],
    ["extra field", d => JSON.stringify({ ...d, approved: true })],
    ["boolean revision", d => JSON.stringify({ ...d, revision: true })],
    ["fractional revision", d => JSON.stringify({ ...d, revision: 20.5 })],
    ["wrong field shape", d => JSON.stringify({ ...d, edits: { width: { base: "true", value: "12" } } })],
    ["unknown edit key", d => JSON.stringify({ ...d, edits: { request_id: { base: "null", value: "foreign" } } })],
    ["mismatched sent target", d => JSON.stringify({ ...d, edits: { width: { base: "1920", value: "1280" } },
      awaiting: { revision: 20, fields: { width: { raw: "1280", target: "720" } } } })],
    ["sent field missing from edits", d => JSON.stringify({ ...d, awaiting: { revision: 20, fields: { width: { raw: "1280", target: "1280" } } } })],
  ];
  for (const [label, makeRaw] of malformed) it("refuses " + label + " while retaining the original copy and later typing", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture();
    const key = profileMetadataKey(f.scope, metadataWriter());
    const raw = makeRaw(newProfileMetadataDraft(f.scope, 20)); entries.set(key, raw);
    const loaded = loadProfileMetadata(key, f.scope, 20, storage);
    expect(loaded.blocked).not.toBeNull();
    const later = editProfileMetadata(loaded.draft, "name", "Still typing", f.current);
    const retained = persistProfileMetadata(key, later, storage);
    expect(retained.draft.edits.name.value).toBe("Still typing");
    expect(entries.get(key)).toBe(raw);
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
  });

  it("retains inaccessible original storage and all new raw input when read access fails", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture();
    const key = profileMetadataKey(f.scope, metadataWriter()), original = "inaccessible original";
    entries.set(key, original);
    storage.getItem.mockImplementation(() => { throw new Error("Synthetic read refusal"); });
    const loaded = loadProfileMetadata(key, f.scope, 20, storage);
    const draft = editProfileMetadata(loaded.draft, "width", "-", f.current);
    const retained = persistProfileMetadata(key, draft, storage);
    expect(retained.draft.edits.width.value).toBe("-");
    expect(retained.warning).not.toBeNull();
    expect(entries.get(key)).toBe(original);
    expect(storage.setItem).not.toHaveBeenCalled();
    storage.getItem.mockImplementation(k => entries.get(k) ?? null);
    expect(persistProfileMetadata(key, draft, storage).blocked).not.toBeNull();
    expect(entries.get(key)).toBe(original);
  });

  it("keeps later raw in memory after a quota failure without changing the original recovery bytes", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture(), key = profileMetadataKey(f.scope, metadataWriter());
    const first = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "First", f.current);
    persistProfileMetadata(key, first, storage); const original = entries.get(key);
    storage.setItem.mockImplementation(() => { throw new Error("Synthetic quota exceeded"); });
    const later = editProfileMetadata(first, "name", "Later exact  ", f.current);
    const retained = persistProfileMetadata(key, later, storage);
    expect(retained.warning).toContain("quota");
    expect(entries.get(key)).toBe(original);
    expect(loadProfileMetadata(key, f.scope, 20, storage).draft.edits.name.value).toBe("Later exact  ");
  });

  it("does not overwrite a changed same-window key and does not discard later typing when its blocked copy is explicitly removed", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture(), key = profileMetadataKey(f.scope, metadataWriter());
    loadProfileMetadata(key, f.scope, 20, storage);
    entries.set(key, "external replacement");
    const next = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Local new input", f.current);
    expect(persistProfileMetadata(key, next, storage).blocked).not.toBeNull();
    expect(entries.get(key)).toBe("external replacement");
    const cleared = discardUnreadableProfileMetadata(key, f.scope, 20, storage);
    expect(cleared.blocked).toBeNull();
    expect(cleared.draft.edits.name.value).toBe("Local new input");
    expect(decodeProfileMetadata(entries.get(key)!, f.scope).edits.name.value).toBe("Local new input");
  });

  it("marks a mismatched recovery write as blocked while retaining both the observed bytes and input", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture(), key = profileMetadataKey(f.scope, metadataWriter());
    loadProfileMetadata(key, f.scope, 20, storage);
    storage.setItem.mockImplementation((k, _raw) => { entries.set(k, "changed after write"); });
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Original new input", f.current);
    const retained = persistProfileMetadata(key, draft, storage);
    expect(retained.blocked).not.toBeNull();
    expect(retained.observedRaw).toBe("changed after write");
    expect(retained.draft).toEqual(draft);
    expect(entries.get(key)).toBe("changed after write");
  });

  it("keeps oversized raw in memory without claiming that a recovery copy was saved", () => {
    const f = metadataFixture(), { storage } = storageFixture(), key = profileMetadataKey(f.scope, metadataWriter());
    const raw = "z".repeat(3 * 1024 * 1024);
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "adaptation_notes", raw, f.current);
    const retained = persistProfileMetadata(key, draft, storage);
    expect(retained.draft.edits.adaptation_notes.value).toBe(raw);
    expect(retained.warning).not.toBeNull();
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it("recovers only an explicitly chosen same-scope copy without rewriting its original", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture();
    const own = profileMetadataKey(f.scope, metadataWriter()), other = profileMetadataKey(f.scope, metadataWriter());
    const separate = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "height", "7e", f.current);
    entries.set(other, JSON.stringify(separate)); const original = entries.get(other);
    expect(profileMetadataCopies(f.scope, own, storage).copies).toEqual([{ key: other, revision: 20, fields: 1, readable: true }]);
    const recovered = recoverProfileMetadata(own, other, f.scope, f.current, 21, storage);
    expect(recovered.draft.edits.height.value).toBe("7e");
    expect(recovered.draft.revision).toBe(21);
    expect(entries.get(other)).toBe(original);
    expect(() => recoverProfileMetadata(own, other, f.scope, f.current, 21, storage)).toThrow("Keep or resolve");
    const foreignScope = profileMetadataScope({ ...f.project, active_branch: "abcde000-1234-4234-8234-000000000999" }, f.profile.id);
    const foreign = profileMetadataKey(foreignScope, metadataWriter());
    expect(() => recoverProfileMetadata(foreign, other, foreignScope, f.current, 21, storage)).toThrow();
    expect(entries.get(other)).toBe(original);
  });
  it("rejects a cross-scope cache or persistence key before replacing any copy", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture(), key = profileMetadataKey(f.scope, metadataWriter());
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Original scoped input", f.current);
    persistProfileMetadata(key, draft, storage); const original = entries.get(key);
    const foreignScope = profileMetadataScope({ ...f.project, generation: "abcde000-1234-4234-8234-000000001111" }, f.profile.id);
    expect(() => loadProfileMetadata(key, foreignScope, 20, storage)).toThrow("identity");
    expect(() => persistProfileMetadata(key, { ...draft, scope: foreignScope }, storage)).toThrow("identity");
    expect(entries.get(key)).toBe(original);
    expect(loadProfileMetadata(key, f.scope, 20, storage).draft).toEqual(draft);
  });

});

describe("profile recovery admission after a failed own read", () => {
  it("keeps an unread own copy blocked when storage becomes available without an explicit read", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture(), key = profileMetadataKey(f.scope, metadataWriter());
    storage.getItem.mockImplementation(() => { throw new Error("Synthetic own read refusal"); });
    const unknown = loadProfileMetadata(key, f.scope, 20, storage);
    expect(unknown.blocked).not.toBeNull();
    expect(unknown.readUnverified).toBe(true);
    expect(unknown.observedRaw).toBeUndefined();
    expect(unknown.draft.edits).toEqual({});
    storage.getItem.mockImplementation(k => entries.get(k) ?? null);
    storage.getItem.mockClear();
    const retained = persistProfileMetadata(key, unknown.draft, storage);
    expect(retained.blocked).not.toBeNull();
    expect(retained.readUnverified).toBe(true);
    expect(storage.getItem).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
    expect(() => discardUnreadableProfileMetadata(key, f.scope, 20, storage)).toThrow("Read the original");
  });

  it("admits a stable absence only after explicit reading and preserves later raw without writing it", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture(), key = profileMetadataKey(f.scope, metadataWriter());
    storage.getItem.mockImplementation(() => { throw new Error("Synthetic own read refusal"); });
    const unknown = loadProfileMetadata(key, f.scope, 20, storage);
    const later = editProfileMetadata(unknown.draft, "width", "1e", f.current);
    persistProfileMetadata(key, later, storage);
    storage.getItem.mockImplementation(k => entries.get(k) ?? null);
    const observed = rereadProfileMetadata(key, f.scope, 20, storage);
    expect(observed.blocked).toBeNull();
    expect(observed.readUnverified).toBeUndefined();
    expect(observed.observedRaw).toBeNull();
    expect(observed.draft).toEqual(later);
    expect(observed.draft.awaiting).toBeUndefined();
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
    expect(entries.size).toBe(0);
  });

  it("recovers a previously unread valid own copy only when no later input would be replaced", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture(), key = profileMetadataKey(f.scope, metadataWriter());
    const original = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "width", "1e", f.current);
    const raw = JSON.stringify(original); entries.set(key, raw);
    storage.getItem.mockImplementation(() => { throw new Error("Synthetic own read refusal"); });
    loadProfileMetadata(key, f.scope, 20, storage);
    storage.getItem.mockImplementation(k => entries.get(k) ?? null);
    const observed = rereadProfileMetadata(key, f.scope, 20, storage);
    expect(observed.draft).toEqual(original);
    expect(observed.observedRaw).toBe(raw);
    expect(observed.blocked).toBeNull();
    expect(observed.readUnverified).toBeUndefined();
    expect(entries.get(key)).toBe(raw);
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
  });

  for (const kind of ["valid", "malformed"] as const) it("keeps later memory input separate from an explicitly observed " + kind + " own copy", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture(), key = profileMetadataKey(f.scope, metadataWriter());
    const original = kind === "valid"
      ? JSON.stringify(editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "width", "1e", f.current))
      : '{"synthetic":"unreadable original"';
    entries.set(key, original);
    storage.getItem.mockImplementation(() => { throw new Error("Synthetic own read refusal"); });
    const unknown = loadProfileMetadata(key, f.scope, 20, storage);
    const later = editProfileMetadata(unknown.draft, "name", "Later raw name  ", f.current);
    persistProfileMetadata(key, later, storage);
    storage.getItem.mockImplementation(k => entries.get(k) ?? null);
    const observed = rereadProfileMetadata(key, f.scope, 20, storage);
    expect(observed.draft).toEqual(later);
    expect(observed.draft.awaiting).toBeUndefined();
    expect(observed.blocked).not.toBeNull();
    expect(observed.readUnverified).toBeUndefined();
    expect(observed.observedRaw).toBe(original);
    expect(entries.get(key)).toBe(original);
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
  });

  it("blocks a failed prewrite own read and releases only the same original bytes on explicit reading", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture(), key = profileMetadataKey(f.scope, metadataWriter());
    const first = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Before", f.current);
    persistProfileMetadata(key, first, storage); const original = entries.get(key);
    const later = editProfileMetadata(first, "name", "After  ", f.current);
    storage.setItem.mockClear(); storage.removeItem.mockClear();
    storage.getItem.mockImplementation(() => { throw new Error("Synthetic later read refusal"); });
    const blocked = persistProfileMetadata(key, later, storage);
    expect(blocked.blocked).not.toBeNull();
    expect(blocked.readUnverified).toBe(true);
    expect(blocked.observedRaw).toBe(original);
    expect(blocked.draft).toEqual(later);
    expect(entries.get(key)).toBe(original);
    storage.getItem.mockImplementation(k => entries.get(k) ?? null);
    const observed = rereadProfileMetadata(key, f.scope, 20, storage);
    expect(observed.blocked).toBeNull();
    expect(observed.readUnverified).toBeUndefined();
    expect(observed.draft).toEqual(later);
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
  });

  it("blocks a failed postwrite own read and never removes the now-observed different copy automatically", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture(), key = profileMetadataKey(f.scope, metadataWriter());
    const first = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Before", f.current);
    persistProfileMetadata(key, first, storage); const original = entries.get(key);
    const later = editProfileMetadata(first, "name", "After  ", f.current);
    let denyRead = false;
    storage.getItem.mockImplementation(k => { if (denyRead && k === key) throw new Error("Synthetic readback refusal"); return entries.get(k) ?? null; });
    storage.setItem.mockImplementation((k, value) => { entries.set(k, value); denyRead = true; });
    const blocked = persistProfileMetadata(key, later, storage);
    expect(blocked.blocked).not.toBeNull();
    expect(blocked.readUnverified).toBe(true);
    expect(blocked.observedRaw).toBe(original);
    expect(blocked.draft).toEqual(later);
    const retainedRaw = entries.get(key);
    expect(retainedRaw).toBe(JSON.stringify(later));
    storage.setItem.mockClear(); storage.removeItem.mockClear(); denyRead = false;
    const observed = rereadProfileMetadata(key, f.scope, 20, storage);
    expect(observed.readUnverified).toBeUndefined();
    expect(observed.blocked).not.toBeNull();
    expect(observed.observedRaw).toBe(retainedRaw);
    expect(observed.draft).toEqual(later);
    expect(entries.get(key)).toBe(retainedRaw);
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
  });

  it("keeps a changing re-read unknown and forbids discarding either unverified observation", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture(), key = profileMetadataKey(f.scope, metadataWriter());
    storage.getItem.mockImplementation(() => { throw new Error("Synthetic own read refusal"); });
    const unknown = loadProfileMetadata(key, f.scope, 20, storage);
    storage.getItem.mockReturnValueOnce(null).mockReturnValueOnce("changed during read");
    const observed = rereadProfileMetadata(key, f.scope, 20, storage);
    expect(observed.blocked).not.toBeNull();
    expect(observed.readUnverified).toBe(true);
    expect(observed.observedRaw).toBeUndefined();
    expect(observed.draft).toEqual(unknown.draft);
    expect(() => discardUnreadableProfileMetadata(key, f.scope, 20, storage)).toThrow("Read the original");
    expect(entries.size).toBe(0);
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
  });

  it("keeps a known prewrite quota failure distinct from an unknown own read", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture(), key = profileMetadataKey(f.scope, metadataWriter());
    const first = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Before", f.current);
    persistProfileMetadata(key, first, storage); const original = entries.get(key);
    storage.setItem.mockImplementation(() => { throw new Error("Synthetic quota failure before write"); });
    const later = editProfileMetadata(first, "name", "After  ", f.current);
    const retained = persistProfileMetadata(key, later, storage);
    expect(retained.blocked).toBeNull();
    expect(retained.readUnverified).toBeUndefined();
    expect(retained.warning).toContain("quota");
    expect(retained.observedRaw).toBe(original);
    expect(retained.draft).toEqual(later);
    expect(entries.get(key)).toBe(original);
  });
});

for (const target of cases) it(target.name + " keeps otherwise-ready admission blocked until the own copy is actually read", async () => {
  const sample = source(), { storage, entries } = storageFixture();
  const scope = profileMetadataScope(sample.project, sample.profileId), key = profileMetadataKey(scope, metadataWriter());
  storage.getItem.mockImplementation(() => { throw new Error("Synthetic initial own read refusal"); });
  const unknown = loadProfileMetadata(key, scope, sample.project.revision, storage);
  const { view } = viewFixture();
  Object.assign(view, { currentProject: { project: sample.project, serial: 0, busy: false },
    renderedProject: sample.project, profileId: sample.profileId, ready: true, draftBlocked: Boolean(unknown.blocked) });
  const intent = { project: sample.project, profileId: sample.profileId, inputSerial: 0, actionKey: view.actionKey };
  expect(unknown.draft.edits).toEqual({});
  await expect(target.invoke(sample, () => { captureDeliveryDispatch(intent, () => view); })).rejects.toThrow("native action was not sent");
  expect(invokeFixture).not.toHaveBeenCalled();
  storage.getItem.mockImplementation(k => entries.get(k) ?? null);
  const observed = rereadProfileMetadata(key, scope, sample.project.revision, storage);
  view.draftBlocked = Boolean(observed.blocked);
  const gesture = captureDeliveryDispatch(intent, () => view);
  const returned = { fixture: "opaque API result, no native or human evidence" };
  invokeFixture.mockImplementation((command: string) => {
    if (command === "issue_effect_grant") return Promise.resolve({ token: grantToken });
    if (command === target.command) return Promise.resolve(returned);
    throw new Error("Unexpected command.");
  });
  await expect(target.invoke(sample, gesture.beforeDispatch)).resolves.toBe(returned);
  expect(invokeFixture.mock.calls.map(call => call[0])).toEqual(["issue_effect_grant", target.command]);
  expect(storage.setItem).not.toHaveBeenCalled();
  expect(storage.removeItem).not.toHaveBeenCalled();
});

describe("explicit local cut mode", () => {
  const ids = ["abcde000-1234-4234-8234-000000000221", "abcde000-1234-4234-8234-000000000222"];
  function choose(draft: ProfileMetadataDraft, current: Record<string, string>, mode: "full" | "custom", selection: string[]) {
    const list = editProfileMetadata(draft, "included_scene_ids", JSON.stringify(selection), current);
    return editProfileMetadata(list, "cut_mode", mode, current);
  }
  it("retains a custom empty selection separately from the canonical full sequence", () => {
    const f = metadataFixture(), profile = { ...f.profile, included_scene_ids: [] };
    const current = profileCanonicalFields(profile);
    let draft = choose(newProfileMetadataDraft(f.scope, 20), current, "custom", ids);
    draft = choose(draft, current, "custom", []);
    expect(draft.edits.cut_mode.value).toBe("custom");
    expect(() => proposedProfileMetadata(profile, draft)).toThrow("at least one scene");
    expect(submitProfileMetadata.bind(null, draft, profile, 20)).toThrow("at least one scene");
    expect(profile.included_scene_ids).toEqual([]);
    const next = reconcileProfileMetadata(draft, current, 21, f.scope);
    expect(next.edits).toEqual(draft.edits);
    const restored = decodeProfileMetadata(JSON.stringify(next), f.scope);
    expect(restored.edits.cut_mode.value).toBe("custom");
  });
  it("requires an explicit full choice without dropping unrelated raw input", () => {
    const f = metadataFixture(), profile = { ...f.profile, included_scene_ids: [] };
    const current = profileCanonicalFields(profile);
    let draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "width", "1e", current);
    draft = choose(draft, current, "custom", []);
    draft = choose(draft, current, "full", []);
    expect(draft.edits).toEqual({ width: { base: current.width, value: "1e" } });
    expect(() => proposedProfileMetadata(profile, draft)).toThrow("complete decimal");
  });
  it("keeps an empty edit made after a custom selection was sent through the matching ACK", () => {
    const f = metadataFixture(), profile = { ...f.profile, included_scene_ids: [] }, id = metadataWriter();
    const current = profileCanonicalFields(profile);
    const draft = choose(newProfileMetadataDraft(f.scope, 20), current, "custom", ids);
    const sent = submitProfileMetadata(draft, profile, 20, id);
    const later = choose(sent, current, "custom", []);
    const saved = { ...profile, included_scene_ids: ids };
    const observed = acknowledgeProfileMetadata(later, id, originalReceipt(f, sent));
    expect(observed.awaiting).toBeUndefined();
    expect(observed.edits.cut_mode.value).toBe("custom");
    expect(observed.edits.included_scene_ids.value).toBe("[]");
    expect(() => proposedProfileMetadata(saved, observed)).toThrow("at least one scene");
    expect(decodeProfileMetadata(JSON.stringify(observed), f.scope).edits).toEqual(observed.edits);
  });
  it("does not let a peer full-sequence revision reinterpret a pinned empty custom cut", () => {
    const f = metadataFixture(), profile = { ...f.profile, included_scene_ids: ids };
    const current = profileCanonicalFields(profile);
    let draft = choose(newProfileMetadataDraft(f.scope, 20), current, "custom", []);
    draft = editProfileMetadata(draft, "name", "Unrelated authored name", current);
    const latest = { ...profile, included_scene_ids: [] }, now = profileCanonicalFields(latest);
    const observed = reconcileProfileMetadata(draft, now, 21, f.scope);
    expect(observed.edits.cut_mode).toEqual({ base: '"custom"', value: "custom" });
    expect(profileMetadataConflicts(observed, now)).toContain("cut_mode");
    expect(() => proposedProfileMetadata(latest, observed)).toThrow("at least one scene");
    const mine = resolveProfileMetadata(observed, "cut_mode", now, "draft");
    expect(profileMetadataConflicts(mine, now)).toEqual([]);
    expect(() => proposedProfileMetadata(latest, mine)).toThrow("at least one scene");
    const saved = resolveProfileMetadata(observed, "cut_mode", now, "saved");
    expect(saved.edits).toEqual({ name: observed.edits.name });
  });
  it("reviews the mode and scene list together and never adds local mode to the canonical wire", () => {
    const f = metadataFixture(), profile = { ...f.profile, included_scene_ids: ids };
    const current = profileCanonicalFields(profile);
    let draft = choose(newProfileMetadataDraft(f.scope, 20), current, "custom", [ids[0]]);
    draft = editProfileMetadata(draft, "width", "001280", current);
    const proposed = proposedProfileMetadata(profile, draft);
    expect(proposed.included_scene_ids).toEqual([ids[0]]);
    expect(Object.hasOwn(proposed, "cut_mode")).toBe(false);
    const resolved = resolveProfileMetadata(draft, "included_scene_ids", current, "saved");
    expect(resolved.edits).toEqual({ width: draft.edits.width });
  });
  it("does not inherit a peer custom scene list over a locally empty cut from full sequence", () => {
    const f = metadataFixture(), profile = { ...f.profile, included_scene_ids: [] };
    const current = profileCanonicalFields(profile);
    const draft = choose(newProfileMetadataDraft(f.scope, 20), current, "custom", []);
    const peer = { ...profile, included_scene_ids: [ids[1]] }, now = profileCanonicalFields(peer);
    const observed = reconcileProfileMetadata(draft, now, 21, f.scope);
    expect(observed.edits.included_scene_ids?.value).toBe("[]");
    expect(observed.edits.cut_mode?.value).toBe("custom");
    expect(profileMetadataConflicts(observed, now)).toContain("included_scene_ids");
    expect(() => proposedProfileMetadata(peer, observed)).toThrow("at least one scene");
    expect(decodeProfileMetadata(JSON.stringify(observed), f.scope).edits).toEqual(observed.edits);
  });
  it("accepts old array-only recovery copies with their existing full/custom meanings", () => {
    const f = metadataFixture(), profile = { ...f.profile, included_scene_ids: [] };
    const current = profileCanonicalFields(profile);
    const old = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "included_scene_ids", JSON.stringify(ids), current);
    const restored = decodeProfileMetadata(JSON.stringify(old), f.scope);
    expect(proposedProfileMetadata(profile, restored).included_scene_ids).toEqual(ids);
    expect(restored.edits.cut_mode).toBeUndefined();
  });
});

describe("retained metadata for removed profiles", () => {
  function retainedFixture() {
    const f = metadataFixture();
    f.project.id = metadataWriter(); f.project.generation = metadataWriter();
    return { ...f, scope: profileMetadataScope(f.project, f.profile.id),
      removed: { ...f.project, deliverables: f.project.deliverables.filter(profile => profile.id !== f.profile.id) } };
  }
  it("lists one exact durable copy after removal without rewriting storage, and hides it again after restoration", () => {
    const f = retainedFixture(), { storage, entries } = storageFixture();
    const key = profileMetadataKey(f.scope, metadataWriter());
    let draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "width", "", f.current);
    draft = editProfileMetadata(draft, "height", "-", f.current);
    persistProfileMetadata(key, draft, storage);
    const raw = entries.get(key)!;
    storage.setItem.mockClear(); storage.removeItem.mockClear();
    expect(retainedProfileMetadataCopies(f.project, storage).copies).toEqual([]);
    const collected = retainedProfileMetadataCopies(f.removed, storage);
    expect(collected.copies).toEqual([{ key, profileId: f.profile.id, source: "storage", raw, draft, warning: null }]);
    expect(collected.warning).toBeNull();
    expect(retainedProfileMetadataCopies({ ...f.removed, revision: 24 }, storage).copies[0].raw).toBe(raw);
    expect(retainedProfileMetadataCopies(f.project, storage).copies).toEqual([]);
    expect(entries.get(key)).toBe(raw);
    expect(storage.setItem).not.toHaveBeenCalled(); expect(storage.removeItem).not.toHaveBeenCalled();
  });
  it("does not mix project, generation, branch or present-profile copies", () => {
    const f = retainedFixture(), { storage, entries } = storageFixture();
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "  Kept  ", f.current);
    entries.set(profileMetadataKey(f.scope, metadataWriter()), JSON.stringify(draft));
    for (const changed of [
      { ...f.removed, id: metadataWriter() }, { ...f.removed, generation: metadataWriter() },
      { ...f.removed, active_branch: metadataWriter() }, f.project,
    ]) expect(retainedProfileMetadataCopies(changed, storage).copies).toEqual([]);
    expect(retainedProfileMetadataCopies(f.removed, storage).copies).toHaveLength(1);
    expect(storage.setItem).not.toHaveBeenCalled(); expect(storage.removeItem).not.toHaveBeenCalled();
  });
  it("keeps a newer window draft separate from a corrupt durable copy", () => {
    const f = retainedFixture(), { storage, entries } = storageFixture();
    const key = profileMetadataKey(f.scope, metadataWriter());
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "width", "1e", f.current);
    persistProfileMetadata(key, draft, storage);
    const corrupt = "{original bytes do not parse";
    entries.set(key, corrupt);
    const later = editProfileMetadata(draft, "name", "Later window input", f.current);
    persistProfileMetadata(key, later, storage);
    storage.setItem.mockClear(); storage.removeItem.mockClear();
    const copies = retainedProfileMetadataCopies(f.removed, storage).copies;
    expect(copies).toHaveLength(2);
    expect(copies.find(copy => copy.source === "storage")).toMatchObject({ key, raw: corrupt, draft: null });
    expect(copies.find(copy => copy.source === "window")).toMatchObject({ key, raw: JSON.stringify(later), draft: later });
    expect(entries.get(key)).toBe(corrupt);
    expect(storage.setItem).not.toHaveBeenCalled(); expect(storage.removeItem).not.toHaveBeenCalled();
  });
  it("retains quota-failed raw input in memory and explains that it is not a durable copy", () => {
    const f = retainedFixture(), { storage, entries } = storageFixture();
    const key = profileMetadataKey(f.scope, metadataWriter());
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "height", "", f.current);
    storage.setItem.mockImplementation(() => { throw new Error("quota exceeded"); });
    persistProfileMetadata(key, draft, storage);
    const copies = retainedProfileMetadataCopies(f.removed, storage).copies;
    expect(copies).toHaveLength(1);
    expect(copies[0]).toMatchObject({ source: "window", raw: JSON.stringify(draft), draft });
    expect(copies[0].warning).toContain("quota exceeded");
    expect(entries.has(key)).toBe(false);
  });
  it("surfaces failed storage observation while preserving its in-memory snapshot", () => {
    const f = retainedFixture(), { storage } = storageFixture();
    const key = profileMetadataKey(f.scope, metadataWriter());
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "language", " es ", f.current);
    persistProfileMetadata(key, draft, storage);
    Object.defineProperty(storage, "length", { get() { throw new Error("storage blocked"); } });
    const collected = retainedProfileMetadataCopies(f.removed, storage);
    expect(collected.warning).toContain("storage blocked");
    expect(collected.copies).toHaveLength(1);
    expect(collected.copies[0]).toMatchObject({ source: "window", raw: JSON.stringify(draft), draft });
  });
  it("does not present changing storage bytes as a stable recovery copy", () => {
    const f = retainedFixture(), { storage, entries } = storageFixture();
    const key = profileMetadataKey(f.scope, metadataWriter());
    const raw = JSON.stringify(editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "width", "1280", f.current));
    entries.set(key, raw);
    let reads = 0;
    storage.getItem.mockImplementation(() => ++reads === 1 ? raw : raw + " changed");
    const collected = retainedProfileMetadataCopies(f.removed, storage);
    expect(collected.copies).toEqual([]);
    expect(collected.warning).toContain("changed while it was read");
    expect(entries.get(key)).toBe(raw);
    expect(storage.setItem).not.toHaveBeenCalled(); expect(storage.removeItem).not.toHaveBeenCalled();
  });
  it("keeps submitted input explicitly unconfirmed and does not create a retry", () => {
    const f = retainedFixture(), { storage, entries } = storageFixture();
    const key = profileMetadataKey(f.scope, metadataWriter());
    const draft = submitProfileMetadata(editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "width", "1280", f.current), f.profile, 20);
    entries.set(key, JSON.stringify(draft));
    const copy = retainedProfileMetadataCopies(f.removed, storage).copies[0];
    expect(copy.draft?.awaiting).toEqual(draft.awaiting);
    expect(copy.raw).toBe(JSON.stringify(draft));
    expect(invokeFixture).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled(); expect(storage.removeItem).not.toHaveBeenCalled();
  });
  it("bounds the visible recovery inventory without evicting original copies", () => {
    const f = retainedFixture(), { storage, entries } = storageFixture();
    for (let index = 0; index < 65; index++) {
      const scope = profileMetadataScope(f.project, metadataWriter());
      const draft = editProfileMetadata(newProfileMetadataDraft(scope, 20), "name", "Copy " + index, f.current);
      entries.set(profileMetadataKey(scope, metadataWriter()), JSON.stringify(draft));
    }
    const before = [...entries];
    const collected = retainedProfileMetadataCopies(f.removed, storage);
    expect(collected.copies).toHaveLength(64);
    expect(collected.warning).toContain("64 retained profile snapshots");
    expect([...entries]).toEqual(before);
    expect(storage.setItem).not.toHaveBeenCalled(); expect(storage.removeItem).not.toHaveBeenCalled();
  });
});

describe("original profile authority causal regressions", () => {
  it("causal: matching incoming values cannot acknowledge the original attempt", () => {
    const f = metadataFixture();
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Original capture", f.current);
    const sent = submitProfileMetadata(draft, f.profile, 20);
    const observed = reconcileProfileMetadata(sent, { ...f.current, name: '"Original capture"' }, 21, f.scope);
    expect(observed.awaiting).toEqual(sent.awaiting);
    expect(observed.edits.name).toEqual(sent.edits.name);
  });
  it("causal: explicit field resolution cannot consume an UNKNOWN attempt", () => {
    const f = metadataFixture();
    const sent = submitProfileMetadata(editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Original capture", f.current), f.profile, 20);
    expect(() => resolveProfileMetadata(sent, "name", f.current, "draft")).toThrow(/original|UNKNOWN/i);
    expect(sent.awaiting?.fields.name.raw).toBe("Original capture");
  });
});

describe("original profile reply authority", () => {
  function captured() {
    const f = metadataFixture(), id = metadataWriter();
    let draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Original saved name", f.current);
    draft = editProfileMetadata(draft, "width", "001280", f.current);
    const sent = submitProfileMetadata(draft, f.profile, 20, id);
    return { f, id, sent, receipt: originalReceipt(f, sent) };
  }
  for (const fault of ["project", "generation", "branch", "old-revision", "late-revision", "missing-profile", "unsent-field"] as const) {
    it("retains the complete UNKNOWN capture after an original reply with " + fault, () => {
      const { f, id, sent, receipt } = captured(), before = JSON.stringify(sent);
      if (fault === "project") receipt.id = metadataWriter();
      if (fault === "generation") receipt.generation = metadataWriter();
      if (fault === "branch") receipt.active_branch = metadataWriter();
      if (fault === "old-revision") receipt.revision = 20;
      if (fault === "late-revision") receipt.revision = 22;
      if (fault === "missing-profile") receipt.deliverables = receipt.deliverables.filter(profile => profile.id !== f.profile.id);
      if (fault === "unsent-field") receipt.deliverables.find(profile => profile.id === f.profile.id)!.language = "different";
      expect(() => acknowledgeProfileMetadata(sent, id, receipt)).toThrow("exact profile upsert");
      expect(JSON.stringify(sent)).toBe(before);
    });
  }
  it("cannot acknowledge another original capture ID even when scope, base and profile match", () => {
    const { id, sent, receipt } = captured(), other = metadataWriter();
    expect(other).not.toBe(id); expect(acknowledgeProfileMetadata(sent, other, receipt)).toEqual(sent);
  });
  it("retains an A-B-A spelling edit and unrelated incomplete fields on the original acknowledgement", () => {
    const { f, id, sent, receipt } = captured();
    let later = editProfileMetadata(sent, "name", "Intervening spelling", f.current);
    later = editProfileMetadata(later, "name", "Original saved name", f.current);
    later = editProfileMetadata(later, "height", "1e", f.current);
    const result = acknowledgeProfileMetadata(later, id, receipt);
    expect(result.edits.name.value).toBe("Original saved name");
    expect(result.edits.height.value).toBe("1e");
    expect(result.edits.width).toBeUndefined(); expect(result.awaiting).toBeUndefined();
  });
  it("keeps an original receipt UNKNOWN when the retained comparison source is already newer", () => {
    const { f, id, sent, receipt } = captured();
    const advanced = reconcileProfileMetadata(sent, f.current, 22, f.scope);
    expect(() => acknowledgeProfileMetadata(advanced, id, receipt)).toThrow("exact profile upsert");
    expect(advanced.awaiting).toEqual(sent.awaiting);
  });
  for (const fault of ["capture-id", "capture-profile", "capture-version", "unknown-member"] as const) {
    it("rejects a modified persisted original capture: " + fault, () => {
      const { f, sent } = captured(), changed = structuredClone(sent);
      if (fault === "capture-id") changed.awaiting!.original!.id = "not-an-attempt-id";
      if (fault === "capture-profile") changed.awaiting!.original!.profile.width = 1920;
      if (fault === "capture-version") changed.awaiting!.original!.versions.name = Number.MAX_SAFE_INTEGER;
      if (fault === "unknown-member") Object.assign(changed.awaiting!.original!, { acknowledged: true });
      expect(() => decodeProfileMetadata(JSON.stringify(changed), f.scope)).toThrow("original profile capture");
      expect(decodeProfileMetadata(JSON.stringify(sent), f.scope)).toEqual(sent);
    });
  }
});

describe("verified metadata pointer and UNKNOWN separation custody", () => {
  function retained() {
    const f = metadataFixture(), { storage, entries } = storageFixture(), tab = storageFixture(), writerId = metadataWriter();
    const writer = readProfileWriter(f.scope, writerId, tab.storage), key = profileMetadataKey(f.scope, writerId), id = metadataWriter();
    const sent = submitProfileMetadata(editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Original", f.current), f.profile, 20, id);
    expect(profileCopyVerified(persistProfileMetadata(key, sent, storage))).toBe(true);
    verifyProfileWriter(f.scope, writer, tab.storage);
    return { f, storage, entries, tab, writer, key, sent, saved: originalReceipt(f, sent) };
  }
  it("refuses a silently dropped own copy and does not claim a durable write", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture(), key = profileMetadataKey(f.scope, metadataWriter());
    storage.setItem.mockImplementation(() => undefined);
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "width", "1e", f.current);
    const result = persistProfileMetadata(key, draft, storage);
    expect(profileCopyVerified(result)).toBe(false); expect(result.draft.edits.width.value).toBe("1e");
    expect(result.blocked).toContain("could not be verified"); expect(entries.size).toBe(0);
  });
  it("requires explicit pointer rereading after an unreadable original pointer", () => {
    const f = metadataFixture(), tab = storageFixture(), fallback = metadataWriter();
    tab.storage.getItem.mockImplementationOnce(() => { throw new Error("read denied"); });
    const writer = readProfileWriter(f.scope, fallback, tab.storage);
    expect(writer.readUnverified).toBe(true);
    expect(() => verifyProfileWriter(f.scope, writer, tab.storage)).toThrow("Read the profile writer pointer again");
    expect(tab.entries.size).toBe(0);
    verifyProfileWriter(f.scope, writer, tab.storage, true);
    expect(tab.entries.get(PROFILE_WRITER_PREFIX + f.scope)).toBe(fallback);
  });
  for (const fault of ["silent", "throw", "foreign"] as const) it("refuses unverified writer publication: " + fault, () => {
    const f = metadataFixture(), tab = storageFixture(), id = metadataWriter();
    const writer = readProfileWriter(f.scope, id, tab.storage), key = PROFILE_WRITER_PREFIX + f.scope;
    if (fault === "silent") tab.storage.setItem.mockImplementation(() => undefined);
    if (fault === "throw") tab.storage.setItem.mockImplementation(() => { throw new Error("quota"); });
    if (fault === "foreign") tab.entries.set(key, metadataWriter());
    const prior = tab.entries.get(key);
    expect(() => verifyProfileWriter(f.scope, writer, tab.storage)).toThrow();
    expect(writer.error).not.toBeNull(); expect(tab.entries.get(key)).toBe(prior);
  });
  it("blocks a 65th profile copy without evicting or modifying any original copy", () => {
    const f = metadataFixture(), { storage, entries } = storageFixture();
    for (let index = 0; index < 64; index++) {
      const scope = profileMetadataScope(f.project, metadataWriter()), key = profileMetadataKey(scope, metadataWriter());
      entries.set(key, JSON.stringify(editProfileMetadata(newProfileMetadataDraft(scope, 20), "width", "1e", f.current)));
    }
    const original = [...entries], key = profileMetadataKey(f.scope, metadataWriter());
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "No eviction", f.current);
    const result = persistProfileMetadata(key, draft, storage);
    expect(profileCopyVerified(result)).toBe(false); expect(result.warning).toContain("64-copy");
    expect([...entries]).toEqual(original); expect(result.draft.edits.name.value).toBe("No eviction");
  });
  it("separates inspected later input without rewriting the original UNKNOWN bytes after an observation", async () => {
    const c = retained();
    const later = editProfileMetadata(c.sent, "name", "  Separate later input  ", c.f.current);
    persistProfileMetadata(c.key, later, c.storage); const raw = c.entries.get(c.key)!;
    const observed = reconcileProfileMetadata(later, profileCanonicalFields(c.saved.deliverables[0]), 21, c.f.scope);
    const history: ProjectEvent[] = [{ revision: 21, created_at: "2026-01-01T00:00:00Z",
      change: { type: "upsert_deliverable", profile: c.sent.awaiting!.original!.profile } }];
    const outcome = await inspectProfileOutcome(observed, c.saved, { readProject: async () => c.saved, readHistory: async () => history });
    const next = separateProfileIntent(observed, c.saved, outcome), nextId = metadataWriter();
    const result = retainSeparateProfileIntent(c.key, observed, next, c.writer, nextId, c.storage, c.tab.storage);
    expect(result.writerId).toBe(nextId); expect(c.entries.get(c.key)).toBe(raw);
    const copy = decodeProfileMetadata(c.entries.get(profileMetadataKey(c.f.scope, nextId))!, c.f.scope);
    expect(copy.awaiting).toBeUndefined(); expect(copy.edits.name.value).toBe("  Separate later input  ");
    expect(outcome.matchingValueRevisions).toEqual([21]);
    expect(JSON.parse(raw).awaiting.original.id).toBe(c.sent.awaiting!.original!.id);
  });
  for (const fault of ["original-changed", "new-copy-quota", "pointer-silent"] as const) {
    it("keeps the original UNKNOWN capture and pointer when separation fails: " + fault, async () => {
      const c = retained(), raw = c.entries.get(c.key)!, nextId = metadataWriter();
      const later = editProfileMetadata(c.sent, "name", "Distinct later intent", c.f.current);
      persistProfileMetadata(c.key, later, c.storage);
      const retainedRaw = c.entries.get(c.key)!;
      const outcome = await inspectProfileOutcome(later, c.saved, {
        readProject: async () => c.saved,
        readHistory: async () => [{ revision: 21, created_at: "2026-01-01T00:00:00Z",
          change: { type: "upsert_deliverable", profile: c.sent.awaiting!.original!.profile } }],
      });
      const next = separateProfileIntent(later, c.saved, outcome);
      if (fault === "original-changed") c.entries.set(c.key, raw);
      if (fault === "new-copy-quota") c.storage.setItem.mockImplementation((key, value) => {
        if (key !== c.key) throw new Error("quota"); c.entries.set(key, value);
      });
      if (fault === "pointer-silent") c.tab.storage.setItem.mockImplementation(() => undefined);
      expect(() => retainSeparateProfileIntent(c.key, later, next, c.writer, nextId, c.storage, c.tab.storage)).toThrow();
      expect(c.entries.get(c.key)).toBe(fault === "original-changed" ? raw : retainedRaw);
      expect(c.tab.entries.get(PROFILE_WRITER_PREFIX + c.f.scope)).toBe(c.writer.writerId);
      expect(JSON.parse(c.entries.get(c.key)!).awaiting.original.id).toBe(c.sent.awaiting!.original!.id);
    });
  }
});

describe("explicit saved profile and journal inspection", () => {
  function inspectable() {
    const f = metadataFixture(), id = metadataWriter();
    const draft = editProfileMetadata(newProfileMetadataDraft(f.scope, 20), "name", "Uncertain", f.current);
    const sent = submitProfileMetadata(draft, f.profile, 20, id), saved = originalReceipt(f, sent);
    const event: ProjectEvent = { revision: 21, created_at: "2026-01-01T00:00:00Z", change: { type: "upsert_deliverable", profile: sent.awaiting!.original!.profile } };
    const transport = { readProject: vi.fn(async () => structuredClone(saved)), readHistory: vi.fn(async () => [structuredClone(event)]) };
    return { f, sent, saved, event, transport };
  }
  it("reports a matching saved value without acknowledging or mutating the retained original", async () => {
    const c = inspectable(), raw = JSON.stringify(c.sent);
    const result = await inspectProfileOutcome(c.sent, c.saved, c.transport);
    expect(result.matchingValueRevisions).toEqual([21]); expect(JSON.stringify(c.sent)).toBe(raw);
    const next = separateProfileIntent(c.sent, c.saved, result);
    expect(next.edits).toEqual({}); expect(next.awaiting).toBeUndefined(); expect(JSON.stringify(c.sent)).toBe(raw);
  });
  for (const fault of ["other-owner", "changed-initial", "changed-final", "gap", "empty-history", "oversized-history", "invalid-profile"] as const) {
    it("does not admit a separate intent from unverified source or history: " + fault, async () => {
      const c = inspectable();
      if (fault === "other-owner") c.saved.generation = metadataWriter();
      if (fault === "changed-initial") c.transport.readProject.mockResolvedValue({ ...c.saved, title: "Different saved source" });
      if (fault === "changed-final") c.transport.readProject.mockResolvedValueOnce(c.saved).mockResolvedValue({ ...c.saved, title: "Concurrent change" });
      if (fault === "gap") c.transport.readHistory.mockResolvedValue([{ ...c.event, revision: 22 }]);
      if (fault === "empty-history") c.transport.readHistory.mockResolvedValue([]);
      if (fault === "oversized-history") c.saved.revision = 2070;
      if (fault === "invalid-profile") c.transport.readHistory.mockResolvedValue([{ ...c.event,
        change: { type: "upsert_deliverable", profile: { ...c.sent.awaiting!.original!.profile, width: -1 } } }]);
      const raw = JSON.stringify(c.sent);
      await expect(inspectProfileOutcome(c.sent, c.saved, c.transport)).rejects.toThrow();
      expect(JSON.stringify(c.sent)).toBe(raw);
    });
  }
  it("rejects a previously inspected capture after its scope or pending identity changes", async () => {
    const c = inspectable(), outcome = await inspectProfileOutcome(c.sent, c.saved, c.transport);
    const changed = structuredClone(c.sent); changed.awaiting!.original!.id = metadataWriter();
    expect(() => separateProfileIntent(changed, c.saved, outcome)).toThrow("complete history");
    expect(() => separateProfileIntent(c.sent, { ...c.saved, revision: 22 }, outcome)).toThrow("complete history");
  });
  it("legacy same-spelling causal: retains later A-B-A input when the historical capture has no original edit versions", async () => {
    const c = inspectable(), legacy = structuredClone(c.sent); delete legacy.awaiting!.original;
    delete legacy.sequence; delete legacy.versions;
    let later = editProfileMetadata(legacy, "name", "Intervening author edit", c.f.current);
    later = editProfileMetadata(later, "name", legacy.awaiting!.fields.name.raw, c.f.current);
    const raw = JSON.stringify(later), outcome = await inspectProfileOutcome(later, c.saved, c.transport);
    const separate = separateProfileIntent(later, c.saved, outcome);
    expect(separate.edits.name?.value).toBe(legacy.awaiting!.fields.name.raw);
    expect(separate.awaiting).toBeUndefined(); expect(JSON.stringify(later)).toBe(raw);
  });
  it("permits legacy UNKNOWN inspection while preserving its old raw and later unsent spelling", async () => {
    const c = inspectable(), legacy = structuredClone(c.sent); delete legacy.awaiting!.original;
    legacy.edits.name.value = "Later legacy input"; const raw = JSON.stringify(legacy);
    const outcome = await inspectProfileOutcome(legacy, c.saved, c.transport);
    expect(outcome.matchingValueRevisions).toEqual([21]);
    const next = separateProfileIntent(legacy, c.saved, outcome);
    expect(next.edits.name.value).toBe("Later legacy input"); expect(next.awaiting).toBeUndefined();
    expect(JSON.stringify(legacy)).toBe(raw);
  });
});


/** Source-only saved-font admission. These fixtures observe IPC arguments and
 * blocked calls; they establish no font rights, provider support or native pixels.
 */
describe("saved Film font source selection at the existing API boundary", () => {
  const windowRequestId = "abcdef00-0000-4000-8000-000000000555";
  function fontSource() {
    const sample = source();
    sample.project.schema_version = 2;
    sample.project.branches.find(branch => branch.id === sample.project.active_branch)!.head_revision = sample.project.revision;
    const scene = sample.project.scenes[0];
    sample.project.deliverables[0].included_scene_ids = [scene.id];
    sample.options.scene_intents = sample.options.scene_intents.filter(intent => intent.scene_id === scene.id);
    return sample;
  }
  function firstText(sample: Sample) {
    return sample.project.scenes[0].nodes.find(node => node.kind === "text")!;
  }
  function twoTextFamilies(sample: Sample, left: string | null, right: string | null) {
    const scene = sample.project.scenes[0], text = firstText(sample);
    text.style = { ...text.style, font_family: left, font_weight: 400 };
    const otherId = scene.nodes.find(node => node.id !== text.id)!.id;
    scene.nodes = [text, { ...structuredClone(text), id: otherId,
      style: { ...text.style, font_family: right } }];
  }
  async function windowIntent(sample: Sample, options = sample.options) {
    const { createNativeWindowIntent } = await import("./nativeWindowPreview");
    return createNativeWindowIntent(sample.project, sample.project.scenes[0], sample.project.deliverables[0],
      { start: 0, end: 2 }, options, windowRequestId);
  }

  it("forwards an explicitly saved IBM 400 style to preflight, Full and Window without changing the source or grant barriers", async () => {
    const sample = fontSource(), original = JSON.stringify(sample.project);
    const scene = sample.project.scenes[0], text = firstText(sample);
    const savedStyle = { ...text.style, font_family: "IBM Plex Mono", font_weight: 400 };
    // Existing browser simulator applies the real authoring change shape. This
    // is a local source fixture, not a StudioService/SQLite save receipt.
    vi.stubGlobal("window", {});
    const saved = await api.applyChange(sample.project, {
      type: "update_canvas_style", scene_id: scene.id, node_id: text.id, style: savedStyle,
    });
    vi.stubGlobal("window", { __TAURI_INTERNALS__: {} });
    expect(JSON.stringify(sample.project)).toBe(original);
    expect(saved.revision).toBe(sample.project.revision + 1);
    expect(saved.branches.find(branch => branch.id === saved.active_branch)!.head_revision).toBe(saved.revision);
    expect(saved.scenes[0].nodes.find(node => node.id === text.id)!.style).toEqual(savedStyle);
    const updated = { ...sample, project: saved, options: { ...sample.options, font_family: "IBM Plex Mono" } };
    const savedRaw = JSON.stringify(saved);
    const projection = { fixture: "preflight forwarding only, no native projection verdict" };
    const full = { fixture: "Full forwarding only, no native render receipt" };
    const windowStop = new Error("Synthetic transport stopped after observing the Window request; no native response.");
    invokeFixture.mockImplementation((command: string) => {
      if (command === "motion_canvas_preflight") return Promise.resolve(projection);
      if (command === "issue_effect_grant") return Promise.resolve({ token: grantToken });
      if (command === "render_motion_canvas") return Promise.resolve(full);
      if (command === "render_motion_canvas_window") return Promise.reject(windowStop);
      throw new Error("Unexpected font fixture command.");
    });
    const fullBarrier = vi.fn(), windowBarrier = vi.fn(), dispatched = vi.fn();
    await expect(api.preflightMotionCanvas(saved, sample.profileId, updated.options)).resolves.toBe(projection);
    await expect(api.renderMotionCanvas(saved, sample.profileId, updated.options, fullBarrier)).resolves.toBe(full);
    const intent = await windowIntent(updated);
    await expect(api.renderMotionCanvasWindow(saved, intent, windowBarrier, dispatched)).rejects.toBe(windowStop);
    expect(invokeFixture.mock.calls.map(call => call[0])).toEqual([
      "motion_canvas_preflight", "issue_effect_grant", "render_motion_canvas",
      "issue_effect_grant", "render_motion_canvas_window",
    ]);
    for (const index of [0, 2]) expect(invokeFixture.mock.calls[index][1].request.options).toEqual(updated.options);
    expect(invokeFixture.mock.calls[4][1].request.window.options).toEqual(updated.options);
    for (const index of [1, 3]) expect(invokeFixture.mock.calls[index][1].request).toEqual({
      effect: "render_local", project_id: saved.id, generation: saved.generation,
      revision: saved.revision, subject: index === 1 ? requestId : windowRequestId,
    });
    expect(fullBarrier).toHaveBeenCalledTimes(2);
    expect(windowBarrier).toHaveBeenCalledTimes(2);
    expect(dispatched).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(saved)).toBe(savedRaw);
  });

  it("uses only included Motion Canvas text and ignores excluded text, another renderer and non-text font metadata", async () => {
    const sample = fontSource(), text = firstText(sample), scenes = sample.project.scenes;
    text.style = { ...text.style, font_family: "IBM Plex Mono", font_weight: 400 };
    scenes[0].nodes.find(node => node.kind !== "text")!.style.font_family = "Unrelated shape metadata";
    // The second scene is excluded even though it is Motion Canvas text.
    scenes[1].nodes[0] = { ...scenes[1].nodes[0], kind: "text", text: "Excluded source",
      style: { ...text.style, font_family: "Excluded Family" } };
    // This included scene belongs to another renderer, not this Film font.
    scenes[2].renderer = "blender";
    scenes[2].nodes[0] = { ...scenes[2].nodes[0], kind: "text", text: "Other renderer source",
      style: { ...text.style, font_family: "Other Renderer Family" } };
    sample.project.deliverables[0].included_scene_ids = [scenes[0].id, scenes[2].id];
    const options = { ...sample.options, font_family: "IBM Plex Mono" };
    const raw = JSON.stringify(sample.project), observed = { fixture: "read-only forwarding" };
    invokeFixture.mockResolvedValue(observed);
    await expect(api.preflightMotionCanvas(sample.project, sample.profileId, options)).resolves.toBe(observed);
    expect(invokeFixture.mock.calls).toHaveLength(1);
    expect(invokeFixture.mock.calls[0][0]).toBe("motion_canvas_preflight");
    expect(invokeFixture.mock.calls[0][1].request.options).toEqual(options);
    expect(JSON.stringify(sample.project)).toBe(raw);
  });

  it.each([
    { name: "two explicit families", left: "Instrument Sans Variable", right: "IBM Plex Mono" },
    { name: "null following an explicit nondefault family", left: "IBM Plex Mono", right: null },
    { name: "null preceding an explicit nondefault family", left: null, right: "IBM Plex Mono" },
    { name: "case-distinct family names", left: "IBM Plex Mono", right: "ibm plex mono" },
    { name: "an empty explicit family", left: "", right: "Instrument Sans Variable" },
    { name: "a whitespace-only explicit family", left: " \t ", right: null },
  ])("rejects $name before any preflight invoke, render grant or Window custody callback", async ({ left, right }) => {
    const sample = fontSource();
    twoTextFamilies(sample, left, right);
    const raw = JSON.stringify(sample.project), barrier = vi.fn(), dispatched = vi.fn();
    const intent = await windowIntent(sample);
    await expect(api.preflightMotionCanvas(sample.project, sample.profileId, sample.options)).rejects.toThrow(/saved (primary )?font/);
    await expect(api.renderMotionCanvas(sample.project, sample.profileId, sample.options, barrier)).rejects.toThrow(/saved (primary )?font/);
    await expect(api.renderMotionCanvasWindow(sample.project, intent, barrier, dispatched)).rejects.toThrow(/saved (primary )?font/);
    expect(invokeFixture).not.toHaveBeenCalled();
    expect(barrier).not.toHaveBeenCalled();
    expect(dispatched).not.toHaveBeenCalled();
    expect(JSON.stringify(sample.project)).toBe(raw);
  });

  it.each(["no-text", "null-only", "null-with-explicit-default"] as const)(
    "retains the existing default for %s without inheriting a non-text node's font", async (mode) => {
      const sample = fontSource(), scene = sample.project.scenes[0];
      if (mode === "no-text") {
        for (const node of scene.nodes) { node.kind = "shape"; node.style.font_family = "IBM Plex Mono"; }
      } else if (mode === "null-only") {
        firstText(sample).style.font_family = null;
        scene.nodes.find(node => node.kind !== "text")!.style.font_family = "IBM Plex Mono";
      } else {
        twoTextFamilies(sample, null, "Instrument Sans Variable");
      }
      const raw = JSON.stringify(sample.project), observed = { fixture: "default forwarding only" };
      invokeFixture.mockResolvedValue(observed);
      await expect(api.preflightMotionCanvas(sample.project, sample.profileId, sample.options)).resolves.toBe(observed);
      expect(invokeFixture.mock.calls).toHaveLength(1);
      expect(invokeFixture.mock.calls[0][1].request.options).toEqual(sample.options);
      expect(JSON.stringify(sample.project)).toBe(raw);
    },
  );

  it("forwards a uniform exact saved family spelling without trimming or claiming native rights or weight support", async () => {
    const sample = fontSource();
    firstText(sample).style.font_family = "  Brand Exact  ";
    // A request-shape control only: backend font rights/face/weight checks remain required.
    expect(firstText(sample).style.font_weight).toBe(700);
    const options = { ...sample.options, font_family: "  Brand Exact  " };
    const raw = JSON.stringify(sample.project), observed = { fixture: "exact string forwarding only" };
    invokeFixture.mockResolvedValue(observed);
    await expect(api.preflightMotionCanvas(sample.project, sample.profileId, options)).resolves.toBe(observed);
    expect(invokeFixture.mock.calls[0][1].request.options.font_family).toBe("  Brand Exact  ");
    expect(invokeFixture.mock.calls[0][1].request.options.mono_font_family).toBe("IBM Plex Mono");
    expect(JSON.stringify(sample.project)).toBe(raw);
  });

  it("rejects stale proposed fonts before any call even when the saved uniform text now names another family", async () => {
    const sample = fontSource();
    firstText(sample).style.font_family = "IBM Plex Mono";
    const raw = JSON.stringify(sample.project), barrier = vi.fn(), dispatched = vi.fn();
    const originalOptions = structuredClone(sample.options), intent = await windowIntent(sample, originalOptions);
    await expect(api.preflightMotionCanvas(sample.project, sample.profileId, originalOptions)).rejects.toThrow("Film options differ");
    await expect(api.renderMotionCanvas(sample.project, sample.profileId, originalOptions, barrier)).rejects.toThrow("Film options differ");
    await expect(api.renderMotionCanvasWindow(sample.project, intent, barrier, dispatched)).rejects.toThrow("Film options differ");
    expect(invokeFixture).not.toHaveBeenCalled();
    expect(barrier).not.toHaveBeenCalled();
    expect(dispatched).not.toHaveBeenCalled();
    expect(JSON.stringify(sample.project)).toBe(raw);
    expect(sample.options).toEqual(originalOptions);
  });
});
