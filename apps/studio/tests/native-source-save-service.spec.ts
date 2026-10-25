/**
 * Actual Studio UI -> shared Desktop native-source commands -> StudioService,
 * SQLite and CAS. Sources are small JSON documents authored by this test.
 * Only acknowledgement loss/delay and localStorage faults are injected. Every
 * Project, preparation, grant, receipt and journal comes from the real process.
 * Source retention does not certify native pixels, installed Tauri or a person.
 */
import { createHash, randomUUID } from "node:crypto";
import { readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { expect, test as base, type Page, type TestInfo } from "@playwright/test";
import type { NativeCapsule } from "../src/creativeProduction";
import type { EffectGrantReceipt, Project } from "../src/types";
import {
  FILE_IMPORT_PREFIX, fileImportKey, type FileImportCopy, type FileImportReceipt,
  type NativeCapsuleSourceSpec7, type PreparedFileImport,
} from "../src/fileImport";
import {
  NATIVE_SOURCE_COPY_PREFIX, NATIVE_SOURCE_POINTER_PREFIX,
  type NativeSourceDraft, type NativeSourceFields,
} from "../src/nativeSourceDraft";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

const PREPARE = "prepare_native_capsule_source_file";
const IMPORT = "import_native_capsule_source_file";
const RECOVER = "recover_native_capsule_source_file";

interface SourceCase {
  service: ScopedChangeService;
  peers: ScopedChangeService[];
  project: Project;
  cleanup: Array<() => Promise<unknown>>;
  observations: Record<string, unknown>;
}

const test = base.extend<{ sources: SourceCase }>({
  sources: async ({ page }, use, info) => {
    const service = await ScopedChangeService.create({
      databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true,
    });
    const peers: ScopedChangeService[] = [], cleanup: Array<() => Promise<unknown>> = [];
    const observations: Record<string, unknown> = { behaviorAssertionsCompleted: false };
    const failures: string[] = [];
    let project: Project | undefined, final: Project | undefined;
    let finalization: StoreFinalization | null = null;
    try {
      const boot = await service.fixtureBootstrap();
      expect(boot.test_transport).toBe("service-test-jsonl");
      expect(boot.desktop_invoke_fixture).toBe(true);
      project = boot.project;
      observations.initialProject = project;
      await service.attachPage(page, project.id);
      await use({ service, peers, project, cleanup, observations });
    } finally {
      const settled = await Promise.allSettled(cleanup.map(action => Promise.resolve().then(action)));
      observations.cleanup = settled.map(result => result.status === "fulfilled"
        ? { status: result.status } : { status: result.status, error: String(result.reason) });
      failures.push(...settled.flatMap(result => result.status === "rejected" ? [String(result.reason)] : []));
      try {
        final = project ? await service.project(project.id) : undefined;
        finalization = await service.finalizeClosedStore(info.outputPath("closed-store"), final, peers, [() => page.close()]);
      } catch (reason) {
        failures.push(String(reason));
        observations.fallbackClose = await Promise.allSettled([service, ...peers].map(peer => peer.close()));
      } finally {
        const outcomesPath = info.outputPath("closed-store-outcomes.json");
        try {
          const outcomes = await readFile(outcomesPath);
          await info.attach("closed-store-outcomes", { body: outcomes, contentType: "application/json" });
          observations.closedStoreOutcomesAttachment = { status: "attached",
            sha256: createHash("sha256").update(outcomes).digest("hex") };
        } catch (reason) {
          const absent = (reason as NodeJS.ErrnoException).code === "ENOENT";
          observations.closedStoreOutcomesAttachment = { status: absent ? "absent" : "failed", error: String(reason) };
          if (!absent || finalization) failures.push("Closed-store outcomes attachment: " + String(reason));
        }
        const path = info.outputPath("native-source-save-service-evidence.json");
        await writeFile(path, JSON.stringify({
          scope: "real_native_source_save_ui_shared_desktop_module_service_sqlite_cas",
          inputs: "owned_authored_json_sources; explicit_transport_or_storage_faults_only",
          exclusions: ["native_renderer_execution", "native_pixel_or_fidelity_acceptance", "installed_tauri", "human_acceptance"],
          statusAtTeardown: info.status, expectedStatus: info.expectedStatus,
          primaryFailure: info.error?.message ?? null, failures, observations, final,
          closedStoreOracle: finalization?.oracle ?? null, finalization,
          services: [service, ...peers].map(peer => ({ binaryPath: peer.binaryPath,
            databasePath: peer.databasePath, transcript: peer.transcript, processExits: peer.processExits,
            lifecycleTaints: peer.lifecycleTaints, stderr: peer.stderr })),
        }, null, 2));
        await info.attach("native-source-save-service-evidence", { path, contentType: "application/json" });
      }
      if (failures.length) throw new AggregateError(failures.map(message => new Error(message)), "Native source test finalization failed; original outcomes retained");
    }
  },
});

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Build the exact reviewed studio_review_session with the three shared Desktop native-source routes; no fallback service.");
test.describe.configure({ timeout: 120_000 });

const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const editor = (page: Page) => page.locator(".native-source-editor");
const field = (page: Page, name: string) => editor(page).getByRole("textbox", { name, exact: true });
const recovery = (page: Page) => page.locator(".file-import-recovery");
const revision = (page: Page, value: number) => expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + value);

async function sourcesUi(page: Page, sceneId: string) {
  await button(page, "Production").click();
  await page.getByRole("combobox", { name: "Production working scene", exact: true }).selectOption(sceneId);
  await button(page, "Native sources").click();
  await expect(editor(page)).toBeVisible();
}

async function parameterEditor(page: Page) {
  const input = field(page, "Editable parameters (JSON array)");
  if (!await input.isVisible()) await editor(page).getByText("Declared editable parameters", { exact: true }).click();
  return input;
}

async function fillSource(page: Page, value: NativeSourceFields) {
  await field(page, "Source file path").fill(value.path);
  await field(page, "Source file name").fill(value.name);
  await field(page, "Source media type").fill(value.mediaType);
  await field(page, "Capsule label").fill(value.label);
  await field(page, "Original editor").fill(value.editorHint);
  await (await parameterEditor(page)).fill(value.parameters);
}

async function expectFields(page: Page, value: NativeSourceFields) {
  await expect(field(page, "Source file path")).toHaveValue(value.path);
  await expect(field(page, "Source file name")).toHaveValue(value.name);
  await expect(field(page, "Source media type")).toHaveValue(value.mediaType);
  await expect(field(page, "Capsule label")).toHaveValue(value.label);
  await expect(field(page, "Original editor")).toHaveValue(value.editorHint);
  await expect(await parameterEditor(page)).toHaveValue(value.parameters);
}

interface OwnedSource { path: string; bytes: Buffer; sha256: string; }
async function ownSource(info: TestInfo, filename: string, version: number): Promise<OwnedSource> {
  const path = info.outputPath(filename);
  const bytes = Buffer.from(JSON.stringify({ schema: "motionwright.test.owned-native-source/1",
    author: "Test-authored source", version, title: "Opening frame " + version,
    camera: { focalLength: 35 + version, x: version * 12 }, accent: version === 1 ? "#f0c36a" : "#6ed0dd" }, null, 2) + "\n");
  await writeFile(path, bytes);
  return { path, bytes, sha256: createHash("sha256").update(bytes).digest("hex") };
}
const sourceEvidence = (source: OwnedSource) => ({ path: source.path, sha256: source.sha256,
  sizeBytes: source.bytes.length, ownedUtf8Input: source.bytes.toString("utf8") });
const authoredFields = (source: OwnedSource, label = "Authored opening capsule"): NativeSourceFields => ({
  path: "  " + source.path + "  ", name: "  Owned opening source.json  ", mediaType: "application/json", label,
  parameters: '[\n  "camera.focalLength",\n  "headline",\n  "camera.focalLength",\n  "accent"\n]',
  editorHint: "Open this owned JSON source in a trusted local editor",
});
const authoredSpec = (fields: NativeSourceFields, capsuleId: string, sceneId: string): NativeCapsuleSourceSpec7 => ({
  name: fields.name, media_type: fields.mediaType, capsule_id: capsuleId, scene_id: sceneId, label: fields.label,
  editable_parameters: JSON.parse(fields.parameters), native_editor_hint: fields.editorHint,
});

interface ImportCopy { key: string; raw: string; copy: FileImportCopy; }
async function importCopies(page: Page, projectId: string): Promise<ImportCopy[]> {
  return page.evaluate(({ prefix, projectId }) => Object.keys(localStorage).filter(key =>
    key === prefix + projectId || key.startsWith(prefix + projectId + ":")).sort().map(key => {
    const raw = localStorage.getItem(key)!;
    return { key, raw, copy: JSON.parse(raw) };
  }), { prefix: FILE_IMPORT_PREFIX, projectId });
}

interface NativeDraftCopy { key: string; raw: string; writerId: string; draft: NativeSourceDraft; }
async function nativeDraft(page: Page, project: Project, sceneId: string, target = "new"): Promise<NativeDraftCopy> {
  return page.evaluate(({ copyPrefix, pointerPrefix, scope }) => {
    const writerId = sessionStorage.getItem(pointerPrefix + encodeURIComponent(scope));
    if (!writerId) throw new Error("The native source writer pointer was not retained");
    const key = copyPrefix + encodeURIComponent(scope) + ":" + writerId;
    const raw = localStorage.getItem(key);
    if (raw === null) throw new Error("The native source authored input was not retained");
    const value = JSON.parse(raw) as { writerId: string; draft: NativeSourceDraft };
    return { key, raw, writerId, draft: value.draft };
  }, { copyPrefix: NATIVE_SOURCE_COPY_PREFIX, pointerPrefix: NATIVE_SOURCE_POINTER_PREFIX,
    scope: JSON.stringify([project.id, project.generation, project.active_branch, sceneId, target]) });
}

async function expectRetainedNativeCopy(page: Page, copy: NativeDraftCopy) {
  expect(await page.evaluate(key => localStorage.getItem(key), copy.key)).toBe(copy.raw);
}

/** Submission adds an immutable copy under another writer. It does not alter
 * authored fields, identity or base; its raw parameters are not reserialized. */
async function expectSubmittedCheckpoint(page: Page, before: NativeDraftCopy, after: NativeDraftCopy) {
  expect(after.writerId).toBe(before.writerId);
  const previousWriters = new Set(before.draft.previousCopies.map(origin => origin.writerId));
  const added = after.draft.previousCopies.filter(origin => !previousWriters.has(origin.writerId));
  expect(added).toHaveLength(1);
  expect(after.draft.previousCopies).toEqual([...before.draft.previousCopies, added[0]]);
  const origin = added[0];
  expect(origin.writerId).not.toBe(before.writerId);
  const key = NATIVE_SOURCE_COPY_PREFIX + encodeURIComponent(before.draft.scope) + ":" + origin.writerId;
  const raw = await page.evaluate(key => localStorage.getItem(key), key);
  expect(raw).not.toBeNull();
  const bytes = Buffer.from(raw!, "utf8");
  expect(bytes.length).toBe(origin.byteLength);
  expect(createHash("sha256").update(bytes).digest("hex")).toBe(origin.sha256);
  expect(JSON.parse(raw!)).toEqual({ schema: "motionwright.native-source-copy/1",
    writerId: origin.writerId, draft: before.draft });
  return { key, raw: raw!, writerId: origin.writerId, draft: before.draft, origin };
}

function expectSameAuthoredDraft(before: NativeDraftCopy, after: NativeDraftCopy) {
  // The independently checked previousCopies list is the only submission edit.
  expect(after.draft).toEqual({ ...before.draft, previousCopies: after.draft.previousCopies });
}

async function expectForkedDraft(page: Page, before: NativeDraftCopy, after: NativeDraftCopy) {
  expect(after.writerId).not.toBe(before.writerId);
  expect(after.key).not.toBe(before.key);
  expect(after.draft).toEqual(before.draft);
  await expectRetainedNativeCopy(page, before);
}

function requests(service: ScopedChangeService, command?: string) {
  return service.transcript.flatMap(row => row.direction === "request" && "command" in row.payload
    && (!command || row.payload.command === command) ? [row.payload] : []);
}
const importGrants = (service: ScopedChangeService) => requests(service, "issue_effect_grant")
  .filter(row => (row.args?.request as { effect?: string })?.effect === "import_local");
const capsules = (project: Project) => project.production_design?.capsules ?? [];
function capsule(project: Project, id: string): NativeCapsule {
  const found = capsules(project).filter(value => value.id === id);
  expect(found).toHaveLength(1); return found[0];
}
function expectUnverified(value: NativeCapsule) {
  expect(value.fidelity).toEqual({ renderer: "source-import", renderer_version: "unverified",
    visual: "unavailable", temporal: "unavailable", structural: "unavailable", editable: "unavailable",
    losses: ["Imported source has no verified native realization or fidelity evidence."], evidence_sha256: null });
  expect(value.property_fidelity ?? []).toEqual([]);
}
async function expectCas(service: ScopedChangeService, source: OwnedSource) {
  const bytes = await readFile(join(service.directory, "blobs", "sha256", source.sha256.slice(0, 2), source.sha256));
  expect(bytes).toEqual(source.bytes);
}

/** The operation is real; only acknowledgement delivery is lost. */
function loseNativeReply(state: SourceCase) {
  const original = state.service.invoke.bind(state.service);
  let armed = true, receipt: FileImportReceipt | undefined;
  state.service.invoke = async <T>(name: string, args: Record<string, unknown> = {}): Promise<T> => {
    const value = await original<T>(name, args);
    if (name === IMPORT && armed) {
      armed = false; receipt = structuredClone(value as FileImportReceipt);
      throw new Error("Injected test fault: native source acknowledgement lost after commit");
    }
    return value;
  };
  state.cleanup.push(async () => { state.service.invoke = original; });
  return () => receipt;
}

/** A genuine response is observed before its delivery to the page is delayed. */
function holdRealReply<T>(state: SourceCase, command: typeof IMPORT | "issue_effect_grant") {
  const original = state.service.invoke.bind(state.service);
  let release!: () => void, observe!: (value: T) => void;
  const waiting = new Promise<void>(resolve => { release = resolve; });
  const observed = new Promise<T>(resolve => { observe = resolve; });
  let armed = true;
  state.service.invoke = async <R>(name: string, args: Record<string, unknown> = {}): Promise<R> => {
    const value = await original<R>(name, args);
    if (name === command && armed && (command !== "issue_effect_grant"
        || (args.request as { effect?: string } | undefined)?.effect === "import_local")) {
      armed = false; observe(structuredClone(value) as T); await waiting;
    }
    return value;
  };
  state.cleanup.push(async () => { release(); state.service.invoke = original; });
  return { observed, release };
}

test("authored native source saves its asset and full capsule in one canonical revision and preserves CAS bytes", async ({ page, sources }, info) => {
  const { service, project, observations } = sources; const scene = project.scenes[0];
  const source = await ownSource(info, "owned-native-source-create.json", 1); const fields = authoredFields(source);
  await page.goto("/"); await sourcesUi(page, scene.id); await fillSource(page, fields);
  const draft = await nativeDraft(page, project, scene.id);
  expect(draft.draft.fields).toEqual(fields);
  const spec = authoredSpec(fields, draft.draft.capsuleId, scene.id);
  await button(page, "Save source and capsule").click(); await revision(page, project.revision + 1);
  const saved = await service.project(project.id); const value = capsule(saved, spec.capsule_id);
  expect(saved.revision).toBe(project.revision + 1); expect(saved.assets).toHaveLength(project.assets.length + 1);
  expect(capsules(saved)).toHaveLength(capsules(project).length + 1);
  const asset = saved.assets.find(item => item.id === value.source_asset_id)!;
  expect(asset).toMatchObject({ name: spec.name, media_type: spec.media_type, content_sha256: source.sha256,
    source_revision: "native-capsule-source-import" });
  expect(value).toMatchObject({ id: spec.capsule_id, scene_id: spec.scene_id, source_sha256: source.sha256,
    label: spec.label, editable_parameters: spec.editable_parameters, native_editor_hint: spec.native_editor_hint });
  expectUnverified(value); await expectCas(service, source);
  const journal = await service.history(project.id, project.revision);
  expect(journal).toHaveLength(1); expect(journal[0].change.type).toBe("import_native_capsule_source");
  expect(journal[0].change).toMatchObject({ asset, capsule: value });
  expect(requests(service, PREPARE)).toHaveLength(1); expect(requests(service, IMPORT)).toHaveLength(1);
  expect(importGrants(service)).toHaveLength(1); expect(requests(service, RECOVER)).toEqual([]);
  expect((requests(service, PREPARE)[0].args?.request as { spec: unknown }).spec).toEqual(spec);
  expect(await importCopies(page, project.id)).toEqual([]); await expectFields(page, fields);
  const acknowledged = await nativeDraft(page, project, scene.id);
  const submittedCheckpoint = await expectSubmittedCheckpoint(page, draft, acknowledged);
  await service.close(); await service.start(); await page.reload(); await sourcesUi(page, scene.id);
  expect(await service.project(project.id)).toEqual(saved); await expectFields(page, fields);
  const reloaded = await nativeDraft(page, project, scene.id);
  await expectForkedDraft(page, acknowledged, reloaded);
  await expectRetainedNativeCopy(page, submittedCheckpoint);
  await page.screenshot({ path: info.outputPath("native-source-one-revision-preserved.png"), fullPage: true });
  observations.sourceInput = sourceEvidence(source); observations.spec = spec; observations.savedProject = saved;
  observations.submittedCheckpoint = submittedCheckpoint; observations.reloadedDraft = reloaded;
  observations.behaviorAssertionsCompleted = true;
});

test("replacement keeps capsule identity and the previous asset while resetting prior unverified fidelity metadata", async ({ page, sources }, info) => {
  const { service, project, observations } = sources; const scene = project.scenes[0];
  const sourceA = await ownSource(info, "owned-native-source-version-a.json", 1); const fieldsA = authoredFields(sourceA);
  await page.goto("/"); await sourcesUi(page, scene.id); await fillSource(page, fieldsA);
  const id = (await nativeDraft(page, project, scene.id)).draft.capsuleId;
  await button(page, "Save source and capsule").click(); await revision(page, project.revision + 1);
  const first = await service.project(project.id), firstCapsule = capsule(first, id);
  const firstAsset = first.assets.find(item => item.id === firstCapsule.source_asset_id)!;
  // A real authored change arranges old metadata. It makes no verified render
  // claim and creates no pretend evidence digest or provider receipt.
  const declared: NativeCapsule = { ...firstCapsule,
    fidelity: { ...firstCapsule.fidelity, renderer: "prior-authored-source-declaration", renderer_version: "historical-unverified",
      losses: ["Test-only prior source declaration; no native realization was run."] },
    property_fidelity: [{ path: "camera.focalLength", fidelity: "unavailable", loss_note: "Test-only earlier declaration to verify reset." }] };
  const beforeReplacement = await service.apply(first, { type: "upsert_native_capsule", capsule: declared });
  await page.reload(); await sourcesUi(page, scene.id);
  await page.getByRole("combobox", { name: "Source capsule to save", exact: true }).selectOption(id);
  const sourceB = await ownSource(info, "owned-native-source-version-b.json", 2);
  const fieldsB = { ...authoredFields(sourceB, "Revised opening capsule"), name: "Owned revised source.json",
    parameters: '["accent", "camera.focalLength", "headline"]' };
  await fillSource(page, fieldsB);
  const authoredReplacement = await nativeDraft(page, project, scene.id, id);
  await button(page, "Save source and capsule").click();
  await revision(page, beforeReplacement.revision + 1);
  const saved = await service.project(project.id), next = capsule(saved, id);
  expect(capsules(saved)).toHaveLength(capsules(first).length);
  expect(saved.assets).toHaveLength(beforeReplacement.assets.length + 1);
  expect(saved.assets.find(item => item.id === firstAsset.id)).toEqual(firstAsset);
  expect(next.source_asset_id).not.toBe(firstCapsule.source_asset_id);
  expect(next.source_sha256).toBe(sourceB.sha256); expect(next.source_sha256).not.toBe(firstCapsule.source_sha256);
  expect(next).toMatchObject({ id, scene_id: scene.id, label: fieldsB.label,
    editable_parameters: JSON.parse(fieldsB.parameters), native_editor_hint: fieldsB.editorHint });
  expectUnverified(next); await expectCas(service, sourceA); await expectCas(service, sourceB);
  const delta = await service.history(project.id, beforeReplacement.revision);
  expect(delta).toHaveLength(1); expect(delta[0].change.type).toBe("import_native_capsule_source");
  expect(requests(service, IMPORT)).toHaveLength(2); expect(importGrants(service)).toHaveLength(2);
  const acknowledged = await nativeDraft(page, project, scene.id, id);
  const submittedCheckpoint = await expectSubmittedCheckpoint(page, authoredReplacement, acknowledged);
  await service.close(); await service.start(); await page.reload(); await sourcesUi(page, scene.id);
  await expect(page.getByRole("combobox", { name: "Source capsule to save", exact: true })).toHaveValue(id);
  expect(await service.project(project.id)).toEqual(saved); await expectFields(page, fieldsB);
  const reloaded = await nativeDraft(page, project, scene.id, id);
  await expectForkedDraft(page, acknowledged, reloaded);
  await expectRetainedNativeCopy(page, submittedCheckpoint);
  await page.screenshot({ path: info.outputPath("native-source-stable-identity-new-version.png"), fullPage: true });
  observations.sources = [sourceEvidence(sourceA), sourceEvidence(sourceB)]; observations.firstProject = first;
  observations.beforeReplacement = beforeReplacement; observations.savedProject = saved;
  observations.submittedCheckpoint = submittedCheckpoint; observations.reloadedDraft = reloaded;
  observations.behaviorAssertionsCompleted = true;
});

test("lost committed reply retains cleared and incomplete input through navigation and reload while recovery reads the original result only", async ({ page, sources }, info) => {
  const { service, project, peers, observations } = sources; const scene = project.scenes[0];
  const source = await ownSource(info, "owned-native-unknown.json", 1); const fields = authoredFields(source);
  const observed = loseNativeReply(sources);
  await page.goto("/"); await sourcesUi(page, scene.id); await fillSource(page, fields);
  const authored = await nativeDraft(page, project, scene.id);
  await button(page, "Save source and capsule").click();
  await expect(editor(page).getByRole("alert").filter({ hasText: "acknowledgement lost after commit" })).toBeVisible();
  const [original] = await importCopies(page, project.id); expect(original.copy.preparation.intent.kind).toBe("native_capsule_source");
  const committed = await service.project(project.id);
  expect(committed.revision).toBe(project.revision + 1); expect(observed()?.project).toEqual(committed);
  expect(original.copy.input).toMatchObject({ kind: "native_capsule_source", path: fields.path,
    spec: authoredSpec(fields, (await nativeDraft(page, project, scene.id)).draft.capsuleId, scene.id) });
  expect(original.raw).not.toContain("effect_grant");
  const submitted = await nativeDraft(page, project, scene.id);
  expectSameAuthoredDraft(authored, submitted);
  const submittedCheckpoint = await expectSubmittedCheckpoint(page, authored, submitted);
  const later = { ...fields, path: "", label: "", parameters: '["later unfinished", ', editorHint: "Later source instructions still being written" };
  await fillSource(page, later); const edited = await nativeDraft(page, project, scene.id);
  expect(edited.draft.fields).toEqual(later);
  await button(page, "Canvas").click(); await sourcesUi(page, scene.id); await expectFields(page, later);
  await rm(source.path);
  const peer = await service.peer(); peers.push(peer);
  const current = await peer.apply(committed, { type: "set_narrative_premise", premise: "A later peer decision must survive native recovery" });
  await peer.close(); await service.close(); await service.start(); await page.reload(); await sourcesUi(page, scene.id);
  await revision(page, current.revision); await expectFields(page, later);
  const reloaded = await nativeDraft(page, project, scene.id);
  await expectForkedDraft(page, edited, reloaded);
  await expectRetainedNativeCopy(page, submittedCheckpoint);
  expect((await importCopies(page, project.id))[0].raw).toBe(original.raw);
  const before = { grants: importGrants(service).length, imports: requests(service, IMPORT).length, prepares: requests(service, PREPARE).length };
  await button(page, "Check original import").click(); await expect(recovery(page)).toContainText("The latest saved project is open");
  expect(await service.project(project.id)).toEqual(current); expect(await importCopies(page, project.id)).toEqual([]);
  expect(importGrants(service)).toHaveLength(before.grants); expect(requests(service, IMPORT)).toHaveLength(before.imports);
  expect(requests(service, PREPARE)).toHaveLength(before.prepares);
  const request = requests(service, RECOVER).at(-1)?.args?.request as Record<string, unknown>;
  expect(request).toEqual({ project_id: project.id, generation: project.generation, revision: project.revision,
    request_id: original.copy.request_id, intent: original.copy.preparation.intent });
  expect(Object.keys(request).sort()).toEqual(["generation", "intent", "project_id", "request_id", "revision"]);
  await expectFields(page, later);
  expect(await nativeDraft(page, project, scene.id)).toEqual(reloaded);
  await expectRetainedNativeCopy(page, edited);
  await expectRetainedNativeCopy(page, submittedCheckpoint);
  const download = page.waitForEvent("download"); await button(page, "Download source input").click();
  const saved = info.outputPath("retained-native-input-download.json"); await (await download).saveAs(saved);
  const inputDownload = JSON.parse(await readFile(saved, "utf8"));
  expect(inputDownload.draft.fields).toEqual(later);
  await expectCas(service, source);
  await page.screenshot({ path: info.outputPath("native-unknown-retains-cleared-raw-input.png"), fullPage: true });
  observations.sourceInput = sourceEvidence(source); observations.originalReceipt = observed(); observations.originalCopy = original;
  observations.retainedDraft = edited; observations.reloadedDraft = reloaded;
  observations.submittedCheckpoint = submittedCheckpoint; observations.currentProject = current;
  observations.behaviorAssertionsCompleted = true;
});

test("later authored input survives an older successful source acknowledgement and App navigation", async ({ page, sources }, info) => {
  const { service, project, observations } = sources; const scene = project.scenes[0];
  const source = await ownSource(info, "owned-native-held-success.json", 1); const fields = authoredFields(source);
  const pending = holdRealReply<FileImportReceipt>(sources, IMPORT);
  await page.goto("/"); await sourcesUi(page, scene.id); await fillSource(page, fields);
  const authored = await nativeDraft(page, project, scene.id);
  await button(page, "Save source and capsule").click(); const receipt = await pending.observed;
  const submitted = await nativeDraft(page, project, scene.id);
  expectSameAuthoredDraft(authored, submitted);
  const submittedCheckpoint = await expectSubmittedCheckpoint(page, authored, submitted);
  const originalId = authored.draft.capsuleId;
  const later = { ...fields, name: "Later source name not yet saved.json", mediaType: "", label: "Later authored label",
    parameters: '["future camera", ', editorHint: "" };
  await fillSource(page, later);
  const beforeAcknowledgement = await nativeDraft(page, project, scene.id);
  await button(page, "Canvas").click(); await sourcesUi(page, scene.id); await expectFields(page, later);
  pending.release(); await revision(page, receipt.project.revision);
  expect(await service.project(project.id)).toEqual(receipt.project);
  expect(capsule(receipt.project, originalId).label).toBe(fields.label);
  await expectFields(page, later); expect(await importCopies(page, project.id)).toEqual([]);
  const acknowledged = await nativeDraft(page, project, scene.id);
  expect(acknowledged.draft.fields).toEqual(later); expect(acknowledged.draft.dirty).toBe(true);
  expect(acknowledged.draft.capsuleId).toBe(originalId); expect(acknowledged.draft.baseRevision).toBe(receipt.project.revision);
  expect(acknowledged.draft).toEqual({ ...beforeAcknowledgement.draft,
    baseRevision: receipt.project.revision, baseCapsule: capsule(receipt.project, originalId), dirty: true });
  await expectRetainedNativeCopy(page, submittedCheckpoint);
  await page.reload(); await sourcesUi(page, scene.id); await expectFields(page, later);
  const reloaded = await nativeDraft(page, project, scene.id);
  await expectForkedDraft(page, acknowledged, reloaded);
  await expectRetainedNativeCopy(page, submittedCheckpoint);
  expect(requests(service, IMPORT)).toHaveLength(1); expect(importGrants(service)).toHaveLength(1);
  expect(requests(service, RECOVER)).toEqual([]);
  await page.screenshot({ path: info.outputPath("native-later-input-survives-successful-old-reply.png"), fullPage: true });
  observations.sourceInput = sourceEvidence(source); observations.realReceipt = receipt;
  observations.beforeAcknowledgement = beforeAcknowledgement; observations.afterAcknowledgement = acknowledged;
  observations.submittedCheckpoint = submittedCheckpoint; observations.reloadedDraft = reloaded;
  observations.behaviorAssertionsCompleted = true;
});

test("a silent import-copy write failure stops every effect while the separate authored source backup remains intact", async ({ page, sources }, info) => {
  const { service, project, observations } = sources; const scene = project.scenes[0];
  expect(NATIVE_SOURCE_COPY_PREFIX.startsWith(FILE_IMPORT_PREFIX)).toBe(false);
  await page.addInitScript(prefix => {
    const set = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key: string, value: string) {
      if (key.startsWith(prefix)) return; // Explicit silent failure of this prefix only.
      return set.call(this, key, value);
    };
  }, FILE_IMPORT_PREFIX);
  const source = await ownSource(info, "owned-native-copy-failure.json", 1); const fields = authoredFields(source);
  await page.goto("/"); await sourcesUi(page, scene.id); await fillSource(page, fields);
  const before = await nativeDraft(page, project, scene.id);
  await button(page, "Save source and capsule").click();
  await expect(editor(page).getByRole("alert").filter({ hasText: "retained import changed or became unavailable" })).toBeVisible();
  expect(requests(service, PREPARE)).toHaveLength(1); expect(importGrants(service)).toEqual([]);
  expect(requests(service, IMPORT)).toEqual([]); expect(requests(service, RECOVER)).toEqual([]);
  expect(await service.project(project.id)).toEqual(project); expect(await service.history(project.id, project.revision)).toEqual([]);
  expect(await importCopies(page, project.id)).toEqual([]);
  const submitted = await nativeDraft(page, project, scene.id);
  expectSameAuthoredDraft(before, submitted);
  const submittedCheckpoint = await expectSubmittedCheckpoint(page, before, submitted);
  await page.reload(); await sourcesUi(page, scene.id); await expectFields(page, fields);
  const reloaded = await nativeDraft(page, project, scene.id);
  await expectForkedDraft(page, submitted, reloaded);
  await expectRetainedNativeCopy(page, submittedCheckpoint);
  await page.screenshot({ path: info.outputPath("native-copy-failure-keeps-authored-source.png"), fullPage: true });
  observations.sourceInput = sourceEvidence(source); observations.authoredBackup = before;
  observations.submittedCheckpoint = submittedCheckpoint; observations.reloadedDraft = reloaded;
  observations.behaviorAssertionsCompleted = true;
});

test("a stale grant-time revision refuses native dispatch atomically and missing-receipt recovery never replays it", async ({ page, sources }, info) => {
  const { service, project, peers, observations } = sources; const scene = project.scenes[0];
  const peer = await service.peer(); peers.push(peer);
  const source = await ownSource(info, "owned-native-stale.json", 1); const fields = authoredFields(source);
  const originalInvoke = service.invoke.bind(service); let armed = true, peerProject: Project | undefined;
  service.invoke = async <T>(name: string, args: Record<string, unknown> = {}): Promise<T> => {
    const result = await originalInvoke<T>(name, args);
    if (name === "issue_effect_grant" && armed && (args.request as { effect?: string } | undefined)?.effect === "import_local") {
      armed = false;
      peerProject = await peer.apply(await peer.project(project.id), { type: "set_narrative_premise", premise: "A real peer revision after the native grant" });
    }
    return result;
  };
  sources.cleanup.push(async () => { service.invoke = originalInvoke; });
  await page.goto("/"); await sourcesUi(page, scene.id); await fillSource(page, fields);
  const authored = await nativeDraft(page, project, scene.id);
  await button(page, "Save source and capsule").click();
  await expect(editor(page).getByRole("alert").filter({ hasText: /stale|revision|conflict|scope/i })).toBeVisible();
  expect(peerProject).toBeDefined(); const current = await service.project(project.id);
  expect(current).toEqual(peerProject); expect(current.assets).toEqual(project.assets);
  expect(capsules(current)).toEqual(capsules(project));
  const journal = await service.history(project.id, project.revision);
  expect(journal).toHaveLength(1); expect(journal[0].change.type).toBe("set_narrative_premise");
  expect(requests(service, PREPARE)).toHaveLength(1); expect(importGrants(service)).toHaveLength(1); expect(requests(service, IMPORT)).toHaveLength(1);
  const submitted = await nativeDraft(page, project, scene.id);
  expectSameAuthoredDraft(authored, submitted);
  const submittedCheckpoint = await expectSubmittedCheckpoint(page, authored, submitted);
  const [copy] = await importCopies(page, project.id); expect(copy.copy.input).toMatchObject({ path: fields.path, kind: "native_capsule_source" });
  await rm(source.path); await button(page, "Check original import").click();
  await expect(recovery(page)).toContainText("The original import was not found");
  expect(requests(service, RECOVER)).toHaveLength(1); expect(requests(service, IMPORT)).toHaveLength(1); expect(importGrants(service)).toHaveLength(1);
  expect((await importCopies(page, project.id))[0].raw).toBe(copy.raw); expect(await service.project(project.id)).toEqual(current);
  await expectFields(page, fields);
  expect(await nativeDraft(page, project, scene.id)).toEqual(submitted);
  await expectRetainedNativeCopy(page, submittedCheckpoint);
  observations.sourceInput = sourceEvidence(source); observations.peerProject = peerProject; observations.originalCopy = copy;
  observations.submittedCheckpoint = submittedCheckpoint;
  observations.behaviorAssertionsCompleted = true;
});

test("a real scene content lock rejects source save without adding an asset capsule or journal revision", async ({ page, sources }, info) => {
  const { service, project, observations } = sources; const scene = project.scenes[0];
  const locked = await service.apply(project, { type: "set_lock", resource: "scene:" + scene.id, kind: "content",
    note: "Preserve this authored source binding during the locked-source case" });
  const source = await ownSource(info, "owned-native-locked.json", 1); const fields = authoredFields(source);
  await page.goto("/"); await sourcesUi(page, scene.id); await fillSource(page, fields);
  const draft = await nativeDraft(page, locked, scene.id);
  await button(page, "Save source and capsule").click();
  await expect(editor(page).getByRole("alert").filter({ hasText: /lock/i })).toBeVisible();
  expect(await service.project(project.id)).toEqual(locked); expect(await service.history(project.id, locked.revision)).toEqual([]);
  expect(requests(service, PREPARE)).toHaveLength(1); expect(importGrants(service)).toHaveLength(1); expect(requests(service, IMPORT)).toHaveLength(1);
  const submitted = await nativeDraft(page, locked, scene.id);
  expectSameAuthoredDraft(draft, submitted);
  const submittedCheckpoint = await expectSubmittedCheckpoint(page, draft, submitted);
  await expectFields(page, fields);
  const [copy] = await importCopies(page, project.id); expect(copy.copy.preparation.revision).toBe(locked.revision);
  await button(page, "Check original import").click(); await expect(recovery(page)).toContainText("The original import was not found");
  expect((await importCopies(page, project.id))[0].raw).toBe(copy.raw);
  expect(requests(service, IMPORT)).toHaveLength(1); expect(importGrants(service)).toHaveLength(1);
  expect(await nativeDraft(page, locked, scene.id)).toEqual(submitted);
  await expectRetainedNativeCopy(page, submittedCheckpoint);
  observations.sourceInput = sourceEvidence(source); observations.lockedProject = locked; observations.originalCopy = copy;
  observations.submittedCheckpoint = submittedCheckpoint;
  observations.behaviorAssertionsCompleted = true;
});

test("another window's changed import bytes and independent intent survive while dispatch is blocked and the original draft remains", async ({ page, sources }, info) => {
  const { service, project, peers, observations } = sources; const scene = project.scenes[0];
  const sourceA = await ownSource(info, "owned-native-window-a.json", 1); const fieldsA = authoredFields(sourceA);
  const pending = holdRealReply<EffectGrantReceipt>(sources, "issue_effect_grant");
  await page.goto("/"); await sourcesUi(page, scene.id); await fillSource(page, fieldsA);
  const draft = await nativeDraft(page, project, scene.id);
  await button(page, "Save source and capsule").click(); const grant = await pending.observed;
  expect(grant.effect).toBe("import_local"); const [original] = await importCopies(page, project.id);
  const submitted = await nativeDraft(page, project, scene.id);
  expectSameAuthoredDraft(draft, submitted);
  const submittedCheckpoint = await expectSubmittedCheckpoint(page, draft, submitted);
  const sourceB = await ownSource(info, "owned-native-window-b.json", 2); const fieldsB = authoredFields(sourceB, "Peer authored capsule");
  const specB = authoredSpec(fieldsB, randomUUID(), scene.id);
  const peer = await service.peer(); peers.push(peer);
  const preparedB = await peer.invoke<PreparedFileImport>(PREPARE, { request: { project_id: project.id,
    generation: project.generation, revision: project.revision, path: fieldsB.path, spec: specB } });
  expect(preparedB.intent).toEqual({ kind: "native_capsule_source", spec: specB });
  // This second local request and the byte substitution are explicit storage
  // fault arrangements. Its preparation is real; no receipt is invented and
  // neither request is dispatched by this arrangement.
  const requestB = randomUUID(); const keyB = fileImportKey(project.id, requestB);
  const copyB: FileImportCopy = { schema: "motionwright.file-import-intent/1", request_id: requestB,
    created_at: original.copy.created_at + 1, preparation: preparedB,
    input: { kind: "native_capsule_source", path: fieldsB.path, spec: specB } };
  const rawB = JSON.stringify(copyB), changedA = original.raw + "\n";
  const otherWindow = await page.context().newPage(); await service.attachPage(otherWindow, project.id);
  sources.cleanup.push(async () => { await otherWindow.close(); }); await otherWindow.goto("/");
  await otherWindow.evaluate(({ keyA, changedA, keyB, rawB }) => {
    localStorage.setItem(keyB, rawB); localStorage.setItem(keyA, changedA);
  }, { keyA: original.key, changedA, keyB, rawB });
  pending.release(); await expect(editor(page).getByRole("alert").filter({ hasText: "retained import changed" })).toBeVisible();
  const expected = [{ key: original.key, raw: changedA }, { key: keyB, raw: rawB }].sort((a, b) => a.key.localeCompare(b.key));
  expect((await importCopies(page, project.id)).map(({ key, raw }) => ({ key, raw }))).toEqual(expected);
  expect(await nativeDraft(page, project, scene.id)).toEqual(submitted);
  await expectRetainedNativeCopy(page, submittedCheckpoint);
  expect(requests(service, IMPORT)).toEqual([]); expect(requests(peer, IMPORT)).toEqual([]);
  expect(importGrants(service)).toHaveLength(1); expect(importGrants(peer)).toEqual([]);
  expect(await service.project(project.id)).toEqual(project); expect(await service.history(project.id, project.revision)).toEqual([]);
  await otherWindow.close(); await page.reload(); await sourcesUi(page, scene.id); await expectFields(page, fieldsA);
  const reloaded = await nativeDraft(page, project, scene.id);
  await expectForkedDraft(page, submitted, reloaded);
  await expectRetainedNativeCopy(page, submittedCheckpoint);
  await expect(button(page, "Save source and capsule")).toBeDisabled();
  await page.getByRole("combobox", { name: "Retained import request", exact: true }).selectOption(original.key);
  await button(page, "Check original import").click(); await expect(recovery(page)).toContainText("The original import was not found");
  expect(requests(service, RECOVER)).toHaveLength(1); expect(requests(service, IMPORT)).toEqual([]); expect(importGrants(service)).toHaveLength(1);
  expect((await importCopies(page, project.id)).map(({ key, raw }) => ({ key, raw }))).toEqual(expected);
  expect(await nativeDraft(page, project, scene.id)).toEqual(reloaded);
  await expectRetainedNativeCopy(page, submitted);
  await expectRetainedNativeCopy(page, submittedCheckpoint);
  await page.screenshot({ path: info.outputPath("native-peer-copies-and-original-draft-preserved.png"), fullPage: true });
  observations.sources = [sourceEvidence(sourceA), sourceEvidence(sourceB)]; observations.originalCopy = original;
  observations.changedOriginalBytes = changedA; observations.peerPreparation = preparedB; observations.peerCopy = { key: keyB, raw: rawB };
  observations.originalAuthoredDraft = draft; observations.submittedCheckpoint = submittedCheckpoint;
  observations.reloadedDraft = reloaded; observations.behaviorAssertionsCompleted = true;
});
