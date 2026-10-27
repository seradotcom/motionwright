/*
 * CR-AUDIO-04#/statement: real, bounded units of complete frozen Studio modules.
 * This is the preserved audio-editor-cue-identity unit profile using node:test.
 * It does not execute Vitest, Vite, a browser, service, SQLite, SDK or native audio.
 * Project/ACK snapshots below are explicitly synthetic unit inputs. No persistence
 * or measured voice is inferred from the fixture fields.
 */
import test, { after } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, lstatSync, realpathSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { registerHooks } from "node:module";

const SOURCE = "016fb46f51ca0d8eef26f34c89cbba5eecc40943";
const TREE = "de5fd1ddfcfc91a0ba7ac090b49dbcc2fa584058";
const SDK = "aa918f5b82eace7bc37ad5979f8d9eefff6a9aac";
const FIELD = "CR-AUDIO-04#/statement";
const CLAUSE = "94fbc9a59a93618c1b318687f2d3239c0022f45c01c74f9db1a8df6975aa9426";
const Q = dirname(fileURLToPath(import.meta.url));
const RUN = join(Q, "audio04-016f-nodeunit-original-r2");
const STUDIO = join(RUN, "source", "apps", "studio");
const PRODUCT_PINS = {
  "src/audioDraft.ts": {
    "bytes": 33035,
    "sha256": "930745580fc323887a364917d5d89c9188eeae92583b40daa75b4d802fba8511",
    "git_blob": "870d8b37e8cd2b0453100639889ff24c5ac6a0e0"
  },
  "src/types.ts": {
    "bytes": 40539,
    "sha256": "83f2d7992d14744e6b05b53c548bedd3a8f18913fcfaf8864742f0b4308794e6",
    "git_blob": "79815ad599c4eda2b404fddee285350412a13220"
  },
  "src/fixture.ts": {
    "bytes": 12129,
    "sha256": "3be709fc03d738931d099fd8123e5e03f22afa93b5ea4eb7182cb1c68147c074",
    "git_blob": "edc7d4517b0c403dea468daf3dd72e475b0b8f32"
  },
  "package.json": {
    "bytes": 919,
    "sha256": "428526cf98577d3c960a1f13731cf89a1c1c19cb7a4e653a420d1e832c75c9b4",
    "git_blob": "2be45e9bde032f9e2c197789a8350dba017b72f0"
  }
};
const started = new Date().toISOString();
assert.equal(process.platform, "win32");
assert.equal(process.version, "v24.19.0");
for (const name of ["NODE_OPTIONS", "NODE_PATH", "NODE_V8_COVERAGE", "NODE_COMPILE_CACHE"]) {
  assert.equal(process.env[name], undefined, "Unexpected external Node configuration: " + name);
}
assert.equal(process.env.NODE_DISABLE_COMPILE_CACHE, "1");

function sha(bytes) { return createHash("sha256").update(bytes).digest("hex"); }
function plain(path) {
  const absolute = resolve(path), rel = relative(Q, absolute);
  assert(rel && rel !== ".." && !rel.startsWith(".." + sep) && !rel.includes(":"));
  for (let current = absolute; current !== Q; current = dirname(current)) {
    assert.notEqual(current, dirname(current));
    assert.equal(lstatSync(current).isSymbolicLink(), false);
  }
  assert.equal(lstatSync(Q).isSymbolicLink(), false);
  assert.equal(realpathSync(absolute).toLowerCase(), absolute.toLowerCase());
  return absolute;
}
function exact(rel) {
  const expected = PRODUCT_PINS[rel]; assert(expected, "Unlisted product input");
  const path = plain(join(STUDIO, rel));
  assert(lstatSync(path).isFile() && lstatSync(path).size <= 65536);
  const bytes = readFileSync(path);
  const observed = { bytes: bytes.length, sha256: sha(bytes),
    git_blob: createHash("sha1").update(`blob ${bytes.length}\0`).update(bytes).digest("hex") };
  assert.deepEqual(observed, expected, "Changed frozen input: " + rel);
  return { bytes, observed };
}
function inventory() {
  return Object.fromEntries(Object.keys(PRODUCT_PINS).sort().map(rel => [rel, exact(rel).observed]));
}
function writeOriginal(name, value) {
  assert(/^[a-z0-9.-]+$/.test(name)); plain(RUN);
  const bytes = Buffer.from(JSON.stringify(value) + "\n");
  assert(bytes.length <= 512 * 1024, "Unit input/output artifact exceeded bound");
  const path = join(RUN, name);
  writeFileSync(path, bytes, { flag: "wx" });
  assert.deepEqual(readFileSync(plain(path)), bytes);
  return { path: name, bytes: bytes.length, sha256: sha(bytes) };
}
function freeze(value) {
  if (value && typeof value === "object") {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}
const before = inventory();
assert.equal(JSON.parse(exact("package.json").bytes).type, "module");
const urls = new Map(Object.keys(PRODUCT_PINS).filter(rel => rel.endsWith(".ts"))
  .map(rel => [pathToFileURL(join(STUDIO, rel)).href, rel]));
const loaded = new Set(), edges = new Set();
const harnessURL = import.meta.url;
const audioURL = pathToFileURL(join(STUDIO, "src/audioDraft.ts")).href;
const fixtureURL = pathToFileURL(join(STUDIO, "src/fixture.ts")).href;
const hooks = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (urls.has(context.parentURL)) {
      assert.equal(urls.get(context.parentURL), "src/audioDraft.ts", "Unexpected runtime importer");
      assert.equal(specifier, "./types", "Unexpected runtime import");
      const answer = nextResolve("./types.ts", context);
      assert.equal(answer.url, pathToFileURL(join(STUDIO, "src/types.ts")).href);
      edges.add("src/audioDraft.ts -> src/types.ts");
      return answer;
    }
    if (context.parentURL === harnessURL) {
      assert([audioURL, fixtureURL].includes(specifier), "Unexpected harness dynamic import");
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.startsWith("file:")) {
      assert(urls.has(url), "Unlisted runtime file load");
      exact(urls.get(url)); loaded.add(urls.get(url));
    }
    return nextLoad(url, context);
  },
});
const audio = await import(audioURL);
const { fixtureBootstrap } = await import(fixtureURL);
const { createAudioDraft, editAudioField, prepareAudioSubmission, retainAudioSubmission,
  observeAudioDraft, reconcileAudioDraft, audioConflicts } = audio;
for (const fn of [createAudioDraft, editAudioField, prepareAudioSubmission, retainAudioSubmission,
  observeAudioDraft, reconcileAudioDraft, audioConflicts]) assert.equal(typeof fn, "function");
const harnessSha = sha(readFileSync(plain(fileURLToPath(import.meta.url))));
process.stdout.write("# MW_AUDIO04_SOURCE " + JSON.stringify({
  pid: process.pid, executable: process.execPath, node: process.version,
  source: SOURCE, tree: TREE, sdk: SDK, criterion_id: FIELD, source_clause_sha256: CLAUSE,
  started_at_utc: started, harness_sha256: harnessSha, inputs_before: before,
  execution_kind: "unit", execution_profile: "audio-editor-cue-identity",
  scope: "Actual frozen Studio functions on explicit unit snapshots; not Vitest, persistence, authority or native audio.",
  historical_execution_relabelled: false,
}) + "\n");

const VOICE = "11111111-1111-4111-8111-111111111111";
const SEGMENT = "22222222-2222-4222-8222-222222222222";
const CUE = "33333333-3333-4333-8333-333333333333";
const NEW = "44444444-4444-4444-8444-444444444444";
const NEXT = "55555555-5555-4555-8555-555555555555";
const OTHER = "66666666-6666-4666-8666-666666666666";
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const caseInputs = [], observations = [];
function project() {
  const p = structuredClone(fixtureBootstrap.project); p.locks = [];
  p.audio.voice_tracks = [{ id: VOICE, asset_id: p.assets[0]?.id ?? VOICE,
    label: "Synthetic unit fixture; no measured voice execution", sample_rate_hz: 48000,
    channels: 1, measured_duration: { num: "2", den: "1" }, source_sha256: "a".repeat(64),
    loudness_lufs: null, true_peak_dbfs: null }];
  p.audio.active_voice_track_id = VOICE;
  p.audio.transcript = [{ id: SEGMENT, voice_track_id: VOICE, start: { num: "1", den: "3" },
    end: { num: "5", den: "3" }, text: "Original unit text", speaker: "Narrator", alignment: { kind: "unknown" } }];
  p.audio.cues = [{ id: CUE, at: { num: "1", den: "3" }, label: "Original cue",
    source_segment_id: SEGMENT, evidence: "transcript_aligned" }];
  return p;
}
function input(name, value) {
  const copy = structuredClone(value);
  caseInputs.push({ name, input: copy, json_stringify_sha256: sha(Buffer.from(JSON.stringify(copy))) });
}
function done(name, value) { observations.push({ name, assertions_completed: true, ...value }); }
function newCue(p, id = NEW) {
  return editAudioField(editAudioField(createAudioDraft(p, { kind: "new-cue" }, id), "at", "1"), "label", "Submitted cue");
}
function acknowledgedSnapshot(p, attempt) {
  // Explicit unit fixture supplied to the product acknowledgement comparator;
  // this helper is not a store/service implementation or a real acknowledgement.
  assert.equal(attempt.change.type, "upsert_audio_cue");
  const snapshot = structuredClone(p); snapshot.revision++;
  snapshot.audio.cues.push(structuredClone(attempt.change.cue));
  return snapshot;
}

test("AUDIO04 cue identity and anchor survive text changes and collection reordering", () => {
  const name = "AUDIO04 cue identity and anchor survive text changes and collection reordering";
  const p = project();
  const draft = editAudioField(createAudioDraft(p, { kind: "cue", id: CUE }, NEW), "label", "Changed cue words");
  const changed = structuredClone(p); changed.revision++;
  changed.audio.transcript[0].text = "Text changed independently of the stable cue identifier";
  changed.audio.transcript.unshift({ ...structuredClone(changed.audio.transcript[0]), id: OTHER,
    start: { num: "0", den: "1" }, end: { num: "1", den: "6" }, text: "Inserted leading text" });
  changed.audio.cues.unshift({ id: OTHER, at: { num: "0", den: "1" }, label: "Other cue",
    source_segment_id: OTHER, evidence: "manual" });
  input(name, { project: changed, draft }); freeze(changed); freeze(draft);
  const result = prepareAudioSubmission(draft, changed);
  assert.equal(result.change.type, "upsert_audio_cue");
  assert.equal(result.change.cue.id, CUE);
  assert.equal(result.change.cue.source_segment_id, SEGMENT);
  assert.deepEqual(result.change.cue.at, { num: "1", den: "3" });
  assert.equal(result.change.cue.label, "Changed cue words");
  assert.equal(result.change.cue.evidence, "manual");
  assert.equal(result.revision, changed.revision);
  assert.equal(changed.audio.cues[1].label, "Original cue");
  done(name, { result, input_project_unchanged: true });
});

test("AUDIO04 manual retiming preserves the existing cue identity and source anchor", () => {
  const name = "AUDIO04 manual retiming preserves the existing cue identity and source anchor";
  const p = project();
  const draft = editAudioField(createAudioDraft(p, { kind: "cue", id: CUE }, NEW), "at", "0.75");
  input(name, { project: p, draft }); freeze(p); freeze(draft);
  const result = prepareAudioSubmission(draft, p);
  assert.equal(result.change.type, "upsert_audio_cue");
  assert.equal(result.change.cue.id, CUE); assert.equal(result.change.cue.source_segment_id, SEGMENT);
  assert.deepEqual(result.change.cue.at, { num: "3", den: "4" });
  assert.equal(result.change.cue.label, "Original cue");
  assert.deepEqual(p.audio.cues[0].at, { num: "1", den: "3" });
  done(name, { result, input_project_unchanged: true });
});

test("AUDIO04 new cue allocation is explicit and stable before acknowledgement", () => {
  const name = "AUDIO04 new cue allocation is explicit and stable before acknowledgement";
  const p = project(); input(name, { project: p, group: { kind: "new-cue" }, at: "1", label: "Same cue words" }); freeze(p);
  const first = createAudioDraft(p, { kind: "new-cue" });
  const second = createAudioDraft(p, { kind: "new-cue" });
  assert.match(first.creationId, uuid); assert.match(second.creationId, uuid);
  assert.notEqual(first.creationId, second.creationId); assert.notEqual(first.creationId, CUE);
  const draft = editAudioField(editAudioField(first, "at", "1"), "label", "Same cue words");
  freeze(draft);
  const a = prepareAudioSubmission(draft, p), b = prepareAudioSubmission(draft, p);
  assert.equal(a.change.type, "upsert_audio_cue"); assert.equal(b.change.type, "upsert_audio_cue");
  assert.equal(a.change.cue.id, first.creationId); assert.equal(b.change.cue.id, first.creationId);
  assert.equal(a.change.cue.source_segment_id, null); assert.equal(a.change.cue.evidence, "manual");
  assert.equal(p.audio.cues.length, 1);
  done(name, { generated_ids: [first.creationId, second.creationId], result: a, input_project_unchanged: true });
});

test("AUDIO04 acknowledgement rotates only the next cue identity and retains newer typing", () => {
  const name = "AUDIO04 acknowledgement rotates only the next cue identity and retains newer typing";
  const p = project(), draft = newCue(p), attempt = prepareAudioSubmission(draft, p);
  const retained = editAudioField(retainAudioSubmission(draft, attempt), "label", "Newer cue words");
  const snapshot = acknowledgedSnapshot(p, attempt);
  input(name, { original_project: p, retained_draft: retained, acknowledged_snapshot_fixture: snapshot, next_creation_id: NEXT });
  freeze(p); freeze(retained); freeze(snapshot);
  const pending = observeAudioDraft(retained, p, NEXT);
  assert.equal(pending.acknowledged, false); assert.equal(pending.draft.creationId, NEW);
  const acknowledged = observeAudioDraft(retained, snapshot, NEXT);
  assert.equal(acknowledged.acknowledged, true); assert.equal(acknowledged.draft.awaiting, null);
  assert.equal(acknowledged.draft.creationId, NEXT); assert.equal(acknowledged.draft.fields.at.raw, "");
  assert.equal(acknowledged.draft.fields.label.raw, "Newer cue words");
  const next = prepareAudioSubmission(editAudioField(acknowledged.draft, "at", "2"), snapshot);
  assert.equal(next.change.type, "upsert_audio_cue"); assert.equal(next.change.cue.id, NEXT);
  assert.equal(snapshot.audio.cues[1].id, NEW); assert.equal(snapshot.audio.cues[1].label, "Submitted cue");
  done(name, { original_submission: attempt, next_submission: next, canonical_acknowledgement_executed: false });
});

test("AUDIO04 a retained new-cue identity cannot silently overwrite an existing cue", () => {
  const name = "AUDIO04 a retained new-cue identity cannot silently overwrite an existing cue";
  const p = project(), draft = newCue(p), attempt = prepareAudioSubmission(draft, p);
  const snapshot = acknowledgedSnapshot(p, attempt); snapshot.revision++;
  const unresolved = { ...retainAudioSubmission(draft, attempt), awaiting: null };
  input(name, { saved_snapshot_fixture: snapshot, unresolved_draft: unresolved }); freeze(snapshot); freeze(unresolved);
  assert.throws(() => prepareAudioSubmission(unresolved, snapshot), /pending cue already exists/);
  assert.equal(snapshot.audio.cues.length, 2); assert.equal(snapshot.audio.cues[1].id, NEW);
  assert.equal(snapshot.audio.cues[1].label, "Submitted cue");
  done(name, { collision_rejected: true, input_project_unchanged: true });
});

test("AUDIO04 removed cues and foreign project scopes cannot be recreated or retargeted", () => {
  const name = "AUDIO04 removed cues and foreign project scopes cannot be recreated or retargeted";
  const p = project(), draft = editAudioField(createAudioDraft(p, { kind: "cue", id: CUE }, NEW), "label", "Keep this author input");
  const removed = structuredClone(p); removed.revision++; removed.audio.cues = [];
  const foreign = structuredClone(p); foreign.id = OTHER;
  input(name, { draft, removed_project: removed, foreign_project: foreign }); freeze(draft); freeze(removed); freeze(foreign);
  assert.deepEqual(reconcileAudioDraft(draft, removed), draft);
  assert.equal(audioConflicts(draft, removed)[0].current, "(removed)");
  assert.throws(() => prepareAudioSubmission(draft, removed), /removed/);
  assert.throws(() => prepareAudioSubmission(draft, foreign), /another project/);
  assert.equal(removed.audio.cues.length, 0); assert.equal(foreign.audio.cues[0].label, "Original cue");
  done(name, { removed_source_rejected: true, foreign_scope_rejected: true, input_projects_unchanged: true });
});

after(() => {
  const afterInputs = inventory(); assert.deepEqual(afterInputs, before);
  assert.deepEqual([...loaded].sort(), ["src/audioDraft.ts", "src/fixture.ts", "src/types.ts"]);
  assert.deepEqual([...edges], ["src/audioDraft.ts -> src/types.ts"]);
  const inputFragments = caseInputs.map((entry, index) => ({
    name: entry.name,
    artifact: writeOriginal(`inputs.case${index + 1}.original.private.json`, {
      schema: "motionwright.a5-audio04-actual-unit-input-fragment/1", pid: process.pid,
      source: SOURCE, case_index: index + 1, ...entry,
    }),
  }));
  const inputs = writeOriginal("inputs.original.private.json", {
    schema: "motionwright.a5-audio04-actual-unit-input-manifest/1", pid: process.pid,
    source: SOURCE, fixture_kind: "Synthetic unit project snapshots; no measured voice, SQLite write or service ACK",
    serialization: "Compact JSON; complete original inputs split per case without dropping any field", cases: inputFragments,
  });
  const outputs = writeOriginal("observations.original.private.json", {
    schema: "motionwright.a5-audio04-actual-unit-observations/1", pid: process.pid, source: SOURCE, cases: observations,
  });
  process.stdout.write("# MW_AUDIO04_CLOSURE " + JSON.stringify({
    pid: process.pid, ended_at_utc: new Date().toISOString(), source: SOURCE, tree: TREE, sdk: SDK,
    criterion_id: FIELD, execution_kind: "unit", execution_profile: "audio-editor-cue-identity",
    selected_case_count: 6, assertions_completed: observations.map(v => v.name),
    inputs_after: afterInputs, inputs_stable: true, runtime_modules_loaded: [...loaded].sort(),
    runtime_edges: [...edges], product_functions_extracted_or_replaced: false,
    type_stripping: "Node native erasable TypeScript", original_inputs: inputs, original_observations: outputs,
    service_executions: 0, native_audio_executions: 0, Vitest_executions: 0, source_policy_executions: 0,
    historical_execution_relabelled: false,
  }) + "\n");
  hooks.deregister();
});
