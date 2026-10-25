import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign, type NativeCapsule } from "./creativeProduction";
import { assertNoFileImport, fileImportKey } from "./fileImport";
import type { Project } from "./types";
import {
  NativeSourceDraftSession, createNativeSourceDraft, decodeNativeSourceCopy, encodeNativeSourceCopy,
  nativeSourceCopyKey, nativeSourcePointerKey, nativeSourceScope, prepareNativeSourceSpec,
  type NativeSourceDraft, type NativeSourceStorage,
} from "./nativeSourceDraft";

// Synthetic storage/custody boundaries only. The App/service ingress owns and
// separately proves real grants, FileImportRetention, dispatch and SQLite.
const id = (value: number) => "12345678-1234-4567-89ab-" + String(value).padStart(12, "0");
const WRITER = id(80), CAPSULE = id(81), ASSET = id(82);
const project = (): Project => ({ ...structuredClone(fixtureProject), revision: 4, production_design: emptyProductionDesign() });
const sceneId = (value: Project) => value.scenes[0].id;
const ids = (start: number) => () => id(++start);
class Storage implements NativeSourceStorage {
  values = new Map<string, string>(); writes = 0; failRead = false; failWrite = false; silent = false;
  beforeRead: ((key: string) => void) | null = null;
  afterWrite: ((key: string, raw: string) => void) | null = null;
  getItem(key: string) { this.beforeRead?.(key); if (this.failRead) throw new Error("Synthetic read unavailable"); return this.values.get(key) ?? null; }
  setItem(key: string, raw: string) {
    if (this.failWrite) throw new Error("Synthetic quota failure");
    this.writes++; if (!this.silent) this.values.set(key, raw); this.afterWrite?.(key, raw);
  }
}
function state(value = project(), start = 100, storage = new Storage(), pointers = new Storage(), target = "new") {
  return { value, storage, pointers, session: new NativeSourceDraftSession(value, sceneId(value), target, storage, pointers, ids(start)) };
}
function rawDraft(value: Project): NativeSourceDraft {
  const draft = createNativeSourceDraft(value, sceneId(value), "new", CAPSULE);
  draft.fields = { path: "  E:\\Sources\\author.blend  ", name: "  Original source  ", mediaType: "application/octet-stream",
    label: "  Original capsule  ", parameters: '["camera.position", "  title text  "]', editorHint: "Open the trusted authoring application\nKeep source bytes" };
  draft.sequence = 12; draft.dirty = true;
  return draft;
}
function retained(value = project(), draft = rawDraft(value)) {
  const storage = new Storage(), pointers = new Storage(), key = nativeSourceCopyKey(draft.scope, WRITER);
  const raw = encodeNativeSourceCopy(draft, WRITER);
  storage.values.set(key, raw); pointers.values.set(nativeSourcePointerKey(draft.scope), WRITER);
  return { value, draft, storage, pointers, key, raw };
}
function author(session: NativeSourceDraftSession) {
  const input = rawDraft(project()).fields;
  for (const key of Object.keys(input) as Array<keyof typeof input>) session.setField(key, input[key]);
}
function committed(session: NativeSourceDraftSession, before: Project): Project {
  const spec = prepareNativeSourceSpec(session.draft.fields, session.draft.capsuleId, session.draft.sceneId);
  const capsule: NativeCapsule = { id: spec.capsule_id, scene_id: spec.scene_id, source_asset_id: ASSET, source_sha256: "a".repeat(64),
    label: spec.label, editable_parameters: spec.editable_parameters, native_editor_hint: spec.native_editor_hint,
    fidelity: { renderer: "opaque", renderer_version: "unverified", visual: "unavailable", temporal: "unavailable", structural: "unavailable", editable: "unavailable", losses: [], evidence_sha256: null } };
  return { ...structuredClone(before), revision: before.revision + 1,
    assets: [...before.assets.filter(asset => asset.id !== ASSET), { id: ASSET, name: spec.name, media_type: spec.media_type, content_sha256: capsule.source_sha256, source_revision: null }],
    production_design: { ...(before.production_design ?? emptyProductionDesign()), capsules: [...(before.production_design?.capsules ?? []).filter(value => value.id !== capsule.id), capsule] } };
}
function acknowledge(s: ReturnType<typeof state>) {
  author(s.session); expect(s.session.assertReady()).toBe(true);
  const next = committed(s.session, s.value);
  s.session.acknowledge(s.session.draft.sequence, next);
  return next;
}

describe("native source authored input custody", () => {
  it("retains incomplete JSON and exact raw strings across a cold reload with one capsule identity", () => {
    const s = state(); author(s.session);
    s.session.setField("parameters", '["partial", 1e');
    s.session.setField("path", "  E:\\raw path ");
    const before = structuredClone(s.session.draft), oldPointer = s.pointers.getItem(nativeSourcePointerKey(before.scope));
    const oldRaw = s.storage.getItem(nativeSourceCopyKey(before.scope, oldPointer!));
    const restored = state(s.value, 200, s.storage, s.pointers).session;
    expect(restored.draft).toEqual(before);
    expect(restored.draft.capsuleId).toBe(before.capsuleId);
    expect(s.pointers.getItem(nativeSourcePointerKey(before.scope))).not.toBe(oldPointer);
    expect(s.storage.getItem(nativeSourceCopyKey(before.scope, oldPointer!))).toBe(oldRaw);
    expect(() => prepareNativeSourceSpec(restored.draft.fields, before.capsuleId, before.sceneId)).toThrow("Complete");
    expect(restored.downloadableInput).toContain('1e');
  });

  it("parses parameters only for save and preserves authored array order, whitespace and line breaks", () => {
    const value = rawDraft(project());
    value.fields.parameters = '["  z.position  ","line\\nvalue", "a.position"]';
    const spec = prepareNativeSourceSpec(value.fields, value.capsuleId, value.sceneId);
    expect(spec.editable_parameters).toEqual(["  z.position  ", "line\nvalue", "a.position"]);
    expect(spec.label).toBe(value.fields.label); expect(spec.name).toBe(value.fields.name);
    expect(value.fields.parameters).toBe('["  z.position  ","line\\nvalue", "a.position"]');
    for (const invalid of ['{"not":"an array"}', '[""]', '["control\\tvalue"]', '[12]']) {
      expect(() => prepareNativeSourceSpec({ ...value.fields, parameters: invalid }, value.capsuleId, value.sceneId)).toThrow();
    }
  });

  it("keeps a successful import's exact input and identity rather than resetting the new form", () => {
    const s = state(); author(s.session); const input = structuredClone(s.session.draft.fields), identity = s.session.draft.capsuleId;
    expect(s.session.assertReady()).toBe(true); const next = committed(s.session, s.value);
    s.session.acknowledge(s.session.draft.sequence, next);
    expect(s.session.draft.fields).toEqual(input); expect(s.session.draft.capsuleId).toBe(identity);
    expect(s.session.draft.dirty).toBe(false); expect(s.session.draft.baseRevision).toBe(next.revision);
    expect(state(next, 200, s.storage, s.pointers).session.draft).toEqual(s.session.draft);
  });

  it("preserves the pre-grant raw parameters text alongside later input through a lost response and cold reload", () => {
    const s = state(); author(s.session);
    const original = '[  "first.parameter" ,\n    " second parameter "  ]';
    s.session.setField("parameters", original); expect(s.session.assertReady()).toBe(true);
    // The callback has no result. FileImportRetention separately owns UNKNOWN;
    // this test observes only whether the exact authoring text remains local.
    s.session.setField("parameters", '["later incomplete",');
    const restored = state(s.value, 200, s.storage, s.pointers).session;
    expect(restored.draft.fields.parameters).toBe('["later incomplete",');
    expect(restored.downloadableOriginals.some(copy => {
      try { return copy.raw !== null && JSON.parse(copy.raw).draft?.fields?.parameters === original; }
      catch { return false; }
    })).toBe(true);
  });

  for (const later of ["", "  Original capsule  "]) {
    it(`an old acknowledgement preserves later ${later === "" ? "clearing" : "ABA gestures"} and leaves them dirty`, () => {
      const s = state(); author(s.session); expect(s.session.assertReady()).toBe(true);
      const sentSequence = s.session.draft.sequence, next = committed(s.session, s.value);
      s.session.setField("label", "Later authored choice"); s.session.setField("label", later);
      s.session.acknowledge(sentSequence, next);
      expect(s.session.draft.fields.label).toBe(later); expect(s.session.draft.sequence).toBe(sentSequence + 2);
      expect(s.session.draft.dirty).toBe(true); expect(s.session.draft.baseRevision).toBe(next.revision);
      expect(state(next, 200, s.storage, s.pointers).session.draft.fields.label).toBe(later);
    });
  }

  it("refuses to adopt a later revision that replaced source bytes while keeping the same capsule metadata", () => {
    const s = state(); author(s.session); expect(s.session.assertReady()).toBe(true);
    const before = structuredClone(s.session.draft), latest = committed(s.session, s.value);
    latest.revision++;
    latest.production_design!.capsules[0].source_asset_id = id(999);
    latest.production_design!.capsules[0].source_sha256 = "b".repeat(64);
    s.session.updateProject(latest); s.session.acknowledge(before.sequence, latest);
    expect(s.session.draft).toEqual(before); expect(s.session.conflict).toBe(true);
    expect(s.session.notice).toMatch(/superseded/);
  });

  for (const key of ["id", "generation", "active_branch"] as const) {
    it(`an acknowledgement from another ${key} cannot clear or rebase this input`, () => {
      const s = state(); author(s.session); expect(s.session.assertReady()).toBe(true);
      const before = structuredClone(s.session.draft), other = committed(s.session, s.value); other[key] = id(990);
      s.session.acknowledge(before.sequence, other);
      expect(s.session.draft).toEqual(before);
      expect(() => s.session.updateProject(other)).toThrow(/parked/);
    });
  }

  it("parks each scene/target separately and requires an explicit rebase without replacing raw fields", () => {
    const a = state(); author(a.session); a.session.setField("parameters", '["unfinished"');
    const b = new NativeSourceDraftSession(a.value, a.value.scenes[1].id, "new", a.storage, a.pointers, ids(200));
    b.setField("label", "Other scene input");
    expect(a.session.draft.fields.label).toBe("  Original capsule  ");
    const before = structuredClone(a.session.draft), next = { ...a.value, revision: 7 };
    a.session.updateProject(next); expect(a.session.assertReady()).toBe(false); expect(a.session.draft).toEqual(before);
    a.session.rebase(next);
    expect(a.session.draft.fields).toEqual(before.fields); expect(a.session.draft.capsuleId).toBe(before.capsuleId);
    expect(a.session.draft.baseRevision).toBe(7); expect(a.session.draft.sequence).toBe(before.sequence + 1);
    expect(b.draft.fields.label).toBe("Other scene input");
  });

  it("a missing replacement capsule or scene cannot silently become a new attachment on rebase", () => {
    const initial = state(), saved = acknowledge(initial), capsuleId = initial.session.draft.capsuleId;
    const replacing = state(saved, 200, new Storage(), new Storage(), capsuleId).session;
    replacing.setField("label", "Explicit replacement input"); const before = structuredClone(replacing.draft);
    const missing = structuredClone(saved); missing.revision++; missing.production_design!.capsules = [];
    replacing.rebase(missing); expect(replacing.draft).toEqual(before);
    const noScene = structuredClone(saved); noScene.revision++; noScene.scenes = [];
    replacing.rebase(noScene); expect(replacing.draft).toEqual(before);
  });

  it("a throwing first pointer read stays recoverable and merges later cleared input without losing other fields", () => {
    const s = retained(); s.pointers.failRead = true;
    const session = state(s.value, 100, s.storage, s.pointers).session;
    session.setField("label", "Later text"); session.setField("label", "");
    expect(session.recoveryProblem).toMatch(/unavailable/); expect(s.storage.getItem(s.key)).toBe(s.raw);
    s.pointers.failRead = false; expect(session.retryBackup()).toBe(true);
    expect(session.draft.fields).toEqual({ ...s.draft.fields, label: "" });
    expect(session.draft.capsuleId).toBe(CAPSULE); expect(session.draft.dirty).toBe(true);
    expect(s.storage.getItem(s.key)).toBe(s.raw);
  });

  it("a throwing first copy read can retry the unchanged original while preserving touched fields", () => {
    const s = retained(); s.storage.failRead = true;
    const session = state(s.value, 100, s.storage, s.pointers).session;
    session.setField("parameters", '["new partial",'); session.setField("name", "");
    s.storage.failRead = false; expect(session.retryBackup()).toBe(true);
    expect(session.draft.fields).toEqual({ ...s.draft.fields, parameters: '["new partial",', name: "" });
    expect(s.storage.getItem(s.key)).toBe(s.raw);
  });

  for (const original of ["{incomplete original", null]) {
    it(`pins a first ${original === null ? "missing" : "corrupt"} copy and refuses a valid replacement under the same writer`, () => {
      const s = retained(); if (original === null) s.storage.values.delete(s.key); else s.storage.values.set(s.key, original);
      const session = state(s.value, 100, s.storage, s.pointers).session;
      session.setField("label", "Later input remains downloadable");
      expect(session.assertReady()).toBe(false);
      s.storage.values.set(s.key, s.raw); const writes = s.storage.writes;
      expect(session.retryBackup()).toBe(false); expect(s.storage.writes).toBe(writes);
      expect(session.downloadableOriginals).toContainEqual({ key: s.key, raw: original });
      expect(session.downloadableInput).toContain("Later input remains downloadable");
      expect(s.storage.getItem(s.key)).toBe(s.raw); expect(session.backupError).toMatch(/first native source bytes changed/);
    });
  }

  it("pins an observed empty pointer even if a foreign writer appears during its confirming read", () => {
    const value = project(), storage = new Storage(), pointers = new Storage(), scope = nativeSourceScope(value, sceneId(value), "new");
    let reads = 0;
    pointers.beforeRead = key => { if (++reads === 2) pointers.values.set(key, WRITER); };
    const session = state(value, 100, storage, pointers).session;
    pointers.beforeRead = null;
    expect(session.recoveryProblem).toMatch(/pointer changed/); expect(session.retryBackup()).toBe(false);
    expect(storage.writes).toBe(0);
    expect(session.downloadableOriginals).toContainEqual({ key: nativeSourcePointerKey(scope), raw: null });
  });

  it("retains bytes seen before a later read error and rejects same-writer substitution on Retry", () => {
    const s = retained(); let pointerReads = 0;
    s.pointers.beforeRead = () => { if (++pointerReads === 2) throw new Error("Synthetic confirming read failed"); };
    const session = state(s.value, 100, s.storage, s.pointers).session;
    expect(session.recoveryProblem).toMatch(/confirming read/);
    s.pointers.beforeRead = null;
    const substitute = structuredClone(s.draft); substitute.fields.label = "Replacement from another window";
    const replacement = encodeNativeSourceCopy(substitute, WRITER); s.storage.values.set(s.key, replacement);
    session.setField("label", "Later own input"); expect(session.retryBackup()).toBe(false);
    expect(session.downloadableOriginals).toContainEqual({ key: s.key, raw: s.raw });
    expect(session.downloadableInput).toContain("Later own input"); expect(s.storage.getItem(s.key)).toBe(replacement);
  });

  it("cloned tab pointers fork writers and preserve each window's independent raw input", () => {
    const a = state(); author(a.session);
    const otherPointers = new Storage(); otherPointers.values = new Map(a.pointers.values);
    const b = state(a.value, 200, a.storage, otherPointers);
    const pointerKey = nativeSourcePointerKey(a.session.draft.scope), firstWriter = a.pointers.getItem(pointerKey), secondWriter = otherPointers.getItem(pointerKey);
    expect(firstWriter).not.toBe(secondWriter); expect(a.session.draft.capsuleId).toBe(b.session.draft.capsuleId);
    a.session.setField("label", "First window"); b.session.setField("label", "Second window");
    expect(decodeNativeSourceCopy(a.storage.getItem(nativeSourceCopyKey(a.session.draft.scope, firstWriter!))!, a.session.draft.scope, firstWriter!).fields.label).toBe("First window");
    // Window B notices that its observed origin changed; it never overwrites A.
    expect(b.session.backupError).toMatch(/first native source bytes changed/);
    expect(b.session.downloadableInput).toContain("Second window");
    expect(a.session.assertReady()).toBe(true);
  });

  for (const changed of ["copy", "pointer"] as const) {
    it(`does not overwrite another window's changed ${changed} during backup or Retry`, () => {
      const s = state(); author(s.session);
      const scope = s.session.draft.scope, pointerKey = nativeSourcePointerKey(scope), writer = s.pointers.getItem(pointerKey)!;
      const key = nativeSourceCopyKey(scope, writer), original = s.storage.getItem(key);
      if (changed === "copy") s.storage.values.set(key, "Foreign exact bytes"); else s.pointers.values.set(pointerKey, id(700));
      const writes = s.storage.writes; s.session.setField("label", "Later own input");
      expect(s.session.assertReady()).toBe(false); expect(s.session.retryBackup()).toBe(false); expect(s.storage.writes).toBe(writes);
      expect(s.session.downloadableOriginals).toContainEqual({ key, raw: original });
      expect(s.session.downloadableInput).toContain("Later own input");
      if (changed === "copy") expect(s.storage.getItem(key)).toBe("Foreign exact bytes"); else expect(s.pointers.getItem(pointerKey)).toBe(id(700));
    });
  }

  for (const failure of ["data", "pointer"] as const) {
    it(`a silent ${failure} write blocks readiness; explicit retry confirms the exact later input`, () => {
      const local = new Storage(), pointers = new Storage();
      // A real pointer-publication failure must occur while the pointer still
      // differs. Dropping a same-writer no-op cannot prove backup failure.
      if (failure === "pointer") pointers.silent = true;
      const s = state(project(), 100, local, pointers), storage = failure === "data" ? local : pointers;
      author(s.session);
      storage.silent = true; s.session.setField("label", "First retained attempt");
      expect(s.session.assertReady()).toBe(false); s.session.setField("label", "Later input");
      storage.silent = false; expect(s.session.retryBackup()).toBe(true); expect(s.session.assertReady()).toBe(true);
      expect(state(s.value, 200, s.storage, s.pointers).session.draft.fields.label).toBe("Later input");
    });
  }

  it("keeps a locally written checkpoint across a readback exception and then confirms later raw input", () => {
    const s = state(); s.storage.afterWrite = () => { s.storage.failRead = true; };
    s.session.setField("parameters", '["written but unconfirmed"');
    s.session.setField("parameters", '["later clear",'); expect(s.session.assertReady()).toBe(false);
    expect(s.session.downloadableOriginals.some(value => value.raw?.includes("written but unconfirmed"))).toBe(true);
    s.storage.afterWrite = null; s.storage.failRead = false;
    expect(s.session.retryBackup()).toBe(true);
    expect(state(s.value, 200, s.storage, s.pointers).session.draft.fields.parameters).toBe('["later clear",');
  });

  it("a foreign substitution after a partial backup is not adopted as an interrupted own checkpoint", () => {
    const s = state(); s.storage.afterWrite = key => s.storage.values.set(key, "Foreign during readback");
    s.session.setField("label", "Author's retained input"); s.storage.afterWrite = null;
    const before = new Map(s.storage.values), writes = s.storage.writes;
    expect(s.session.retryBackup()).toBe(false); expect(s.storage.values).toEqual(before); expect(s.storage.writes).toBe(writes);
    expect(s.session.downloadableInput).toContain("Author's retained input");
  });

  it("oversized raw input remains downloadable while its previous local checkpoint is left intact", () => {
    const s = state(), before = new Map(s.storage.values), raw = "x".repeat(1024 * 1024);
    s.session.setField("parameters", raw);
    expect(s.session.assertReady()).toBe(false); expect(s.storage.values).toEqual(before);
    expect(JSON.parse(s.session.downloadableInput).draft.fields.parameters).toBe(raw);
  });

  it("leaves an opaque retained import untouched and the App's global UNKNOWN guard blocks a new action", () => {
    const s = state(), request = id(900), key = fileImportKey(s.value.id, request), original = '{"outcome":"UNKNOWN","original":"do not interpret"}';
    s.storage.values.set(key, original); s.session.setField("parameters", '["later author input"');
    // The full storage adapter belongs to FileImportRetention, not the draft.
    const imports = { get length() { return s.storage.values.size; }, key: (index: number) => [...s.storage.values.keys()][index] ?? null,
      getItem: (name: string) => s.storage.getItem(name), setItem: (name: string, raw: string) => s.storage.setItem(name, raw), removeItem: (name: string) => s.storage.values.delete(name) };
    expect(() => assertNoFileImport(s.value.id, imports)).toThrow();
    s.session.retryBackup(); expect(s.storage.getItem(key)).toBe(original);
    expect(s.session.draft.fields.parameters).toBe('["later author input"');
  });

  it("starts another capsule only explicitly and preserves earlier exact copies through reload", () => {
    const s = state(), saved = acknowledge(s), before = structuredClone(s.session.draft);
    const pointerKey = nativeSourcePointerKey(before.scope), writer = s.pointers.getItem(pointerKey)!, key = nativeSourceCopyKey(before.scope, writer), raw = s.storage.getItem(key)!;
    s.session.startAnother(saved);
    expect(s.session.draft.capsuleId).not.toBe(before.capsuleId); expect(s.pointers.getItem(pointerKey)).not.toBe(writer);
    expect(s.session.draft.fields.path).toBe(""); expect(s.storage.getItem(key)).toBe(raw);
    expect(s.session.downloadableOriginals).toContainEqual({ key, raw });
    const restored = state(saved, 300, s.storage, s.pointers).session;
    expect(restored.draft.capsuleId).toBe(s.session.draft.capsuleId);
    expect(restored.downloadableOriginals).toContainEqual({ key, raw });
    expect(saved.production_design!.capsules[0].id).toBe(before.capsuleId);
  });

  for (const cause of ["dirty", "conflict", "replacement"] as const) {
    it(`refuses Start another for ${cause} without discarding the existing input`, () => {
      const s = state(), saved = acknowledge(s);
      const session = cause === "replacement" ? state(saved, 200, new Storage(), new Storage(), s.session.draft.capsuleId).session : s.session;
      if (cause === "dirty") session.setField("label", "Unfinished next input");
      const current = cause === "conflict" ? { ...saved, revision: saved.revision + 1 } : saved;
      const before = structuredClone(session.draft); session.startAnother(current);
      expect(session.draft).toEqual(before); expect(session.notice).toMatch(/Finish and review/);
    });
  }

  it("a failed new-capsule backup retains the old exact input and retries the same new identity", () => {
    const s = state(), saved = acknowledge(s), old = structuredClone(s.session.draft);
    const pointer = s.pointers.getItem(nativeSourcePointerKey(old.scope))!, key = nativeSourceCopyKey(old.scope, pointer), raw = s.storage.getItem(key)!;
    s.storage.silent = true; s.session.startAnother(saved); const nextId = s.session.draft.capsuleId;
    expect(nextId).not.toBe(old.capsuleId); expect(s.session.assertReady()).toBe(false);
    expect(s.session.downloadableOriginals).toContainEqual({ key, raw }); expect(s.storage.getItem(key)).toBe(raw);
    s.session.setField("label", "New input while backup is blocked"); s.storage.silent = false;
    expect(s.session.retryBackup()).toBe(true); expect(s.session.draft.capsuleId).toBe(nextId);
    expect(state(saved, 300, s.storage, s.pointers).session.draft.fields.label).toBe("New input while backup is blocked");
  });

  it("eight explicit new capsules retain exact originals with linear checkpoint metadata rather than nested raw copies", () => {
    const s = state(); let current = s.value;
    const originals: Array<{ key: string; raw: string }> = [], sizes: number[] = [];
    for (let index = 0; index < 8; index++) {
      author(s.session); s.session.setField("label", "Source capsule " + index);
      expect(s.session.assertReady()).toBe(true);
      current = committed(s.session, current); s.session.acknowledge(s.session.draft.sequence, current);
      const writer = s.pointers.getItem(nativeSourcePointerKey(s.session.draft.scope))!, key = nativeSourceCopyKey(s.session.draft.scope, writer), raw = s.storage.getItem(key)!;
      originals.push({ key, raw }); sizes.push(new TextEncoder().encode(raw).length);
      s.session.startAnother(current);
      expect(s.session.draft.previousCopies).toHaveLength((index + 1) * 2);
      expect(s.session.draft.previousCopies.every(origin => !Object.hasOwn(origin, "raw"))).toBe(true);
    }
    // Each source keeps its pre-grant raw input and confirmed input checkpoint.
    // Every stored anchor has a fixed writer, length and SHA-256; measured
    // growth stays under 200 bytes per added anchor, not exponential raw copies.
    expect(sizes[7] - sizes[0]).toBeLessThan(14 * 200);
    expect(sizes[7]).toBeLessThan(sizes[0] * 3);
    const restored = state(current, 500, s.storage, s.pointers).session;
    expect(restored.draft.previousCopies).toHaveLength(16);
    for (const original of originals) {
      expect(s.storage.getItem(original.key)).toBe(original.raw);
      expect(restored.downloadableOriginals).toContainEqual(original);
    }
  });

  it("a changed historical checkpoint blocks a new copy and keeps the original observation downloadable", () => {
    const s = state(), saved = acknowledge(s), pointerKey = nativeSourcePointerKey(s.session.draft.scope);
    const writer = s.pointers.getItem(pointerKey)!, key = nativeSourceCopyKey(s.session.draft.scope, writer), raw = s.storage.getItem(key)!;
    s.session.startAnother(saved); s.storage.values.set(key, "Foreign historical replacement");
    s.session.setField("label", "Later new input"); expect(s.session.assertReady()).toBe(false);
    expect(s.session.downloadableOriginals).toContainEqual({ key, raw });
    expect(s.session.downloadableInput).toContain("Later new input");
    expect(s.storage.getItem(key)).toBe("Foreign historical replacement");
  });

  it("refuses an unconfirmed author checkpoint before grant, then retains it and later fields on explicit retry", () => {
    const s = state(); author(s.session); const original = s.session.draft.fields.parameters;
    s.storage.silent = true; expect(s.session.assertReady()).toBe(false);
    s.session.setField("parameters", '["later partial",');
    expect(s.session.backupError).toMatch(/authored checkpoint/);
    s.storage.silent = false; expect(s.session.retryBackup()).toBe(true);
    const restored = state(s.value, 300, s.storage, s.pointers).session;
    expect(restored.draft.fields.parameters).toBe('["later partial",');
    expect(restored.downloadableOriginals.some(copy => copy.raw !== null && JSON.parse(copy.raw).draft?.fields?.parameters === original)).toBe(true);
  });

  it("rechecking the same save checkpoint does not allocate duplicate history or change capsule identity", () => {
    const s = state(); author(s.session); expect(s.session.assertReady()).toBe(true);
    const before = structuredClone(s.session.draft), keys = [...s.storage.values.keys()];
    expect(s.session.assertReady()).toBe(true); expect(s.session.assertReady()).toBe(true);
    expect(s.session.draft).toEqual(before); expect([...s.storage.values.keys()]).toEqual(keys);
  });
});
