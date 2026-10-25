import { afterEach, describe, expect, it, vi } from "vitest";
import { fixtureProject } from "./fixture";
import type { Project } from "./types";
import {
  FILE_IMPORT_PREFIX, assertNoFileImport, fileImportDownload, fileImportKey, fileImportProjectPrefix, importFileWithRetention,
  parseFileImportReceipt, readFileImport, readFileImports, recoverFileImportWithRetention,
  removeFileImport, requireFileImport, retainFileImport,
  fileImportIntentName, fileImportIntentMediaType, parseNativeCapsuleSourceSpec, sameNativeCapsuleSourceSpec,
  type FileImportCopy, type FileImportInput, type FileImportRead, type FileImportReceipt,
  type FileImportStorage, type FileImportTransport, type PreparedFileImport, type RetainedFileImport, type NativeCapsuleSourceSpec7,
} from "./fileImport";

// Synthetic transport/storage boundaries only. Service/CAS/reopen coverage is
// recorded separately by file-import-service.spec.ts against the Rust process.
const ID = "12345678-1234-4567-89ab-1234567890ab";
const GEN = "12345678-1234-4567-89ab-1234567890ac";
const NEXT_GEN = "12345678-1234-4567-89ab-1234567890ad";
const REQUEST = "12345678-1234-4567-89ab-1234567890ae";
const TOKEN = "12345678-1234-4567-89ab-1234567890af";
const NIL = "00000000-0000-0000-0000-000000000000";
const source = (): Project => ({ ...structuredClone(fixtureProject), id: ID, generation: GEN, revision: 4 });
const input = () => ({ kind: "voice", path: "  E:\\Media\\original voice.wav  ", label: "  Editorial take  " } satisfies FileImportInput);
const preparation = (): PreparedFileImport => ({
  schema: "motionwright.file-import-preparation/1", project_id: ID, generation: GEN, revision: 4,
  path: "E:\\Media\\original voice.wav", intent: { kind: "voice", name: "original voice.wav", media_type: "audio/wav", label: "Editorial take" },
});

class MemoryStorage implements FileImportStorage {
  readonly data = new Map<string, string>();
  writes = 0;
  removals = 0;
  failGet = false;
  failSet = false;
  failRemove = false;
  failLength = false;
  failKey = false;
  beforeSet: ((key: string, value: string) => void) | null = null;
  afterSet: ((key: string, value: string) => void) | null = null;
  keyOverride: ((index: number) => string | null) | null = null;
  get length() { if (this.failLength) throw new Error("Synthetic index unavailable"); return this.data.size; }
  key(index: number) {
    if (this.failKey) throw new Error("Synthetic key enumeration unavailable");
    return this.keyOverride ? this.keyOverride(index) : Array.from(this.data.keys())[index] ?? null;
  }
  getItem(key: string) { if (this.failGet) throw new Error("Synthetic storage unavailable"); return this.data.get(key) ?? null; }
  setItem(key: string, value: string) {
    if (this.failSet) throw new Error("Synthetic quota exceeded");
    this.beforeSet?.(key, value);
    this.writes++; this.data.set(key, value); this.afterSet?.(key, value);
  }
  removeItem(key: string) {
    if (this.failRemove) throw new Error("Synthetic removal denied");
    this.removals++; this.data.delete(key);
  }
}

function retained(state: FileImportRead): RetainedFileImport {
  expect(state.kind).toBe("retained");
  if (state.kind !== "retained") throw new Error("Expected a retained test copy");
  return state.retained;
}
function save(storage: MemoryStorage): RetainedFileImport { return retainFileImport(preparation(), input(), storage, REQUEST, 1_790_000_000_000); }
function receipt(copy: FileImportCopy, replayed = false): FileImportReceipt {
  return { schema: "motionwright.file-import-receipt/1", request_id: copy.request_id,
    intent: structuredClone(copy.preparation.intent), project: { ...source(), revision: 5 }, replayed };
}
function transport(storage: MemoryStorage) {
  const port = {
    prepare: vi.fn<FileImportTransport["prepare"]>(async () => preparation()),
    grant: vi.fn<FileImportTransport["grant"]>(async () => ({ token: TOKEN, effect: "import_local", project_id: ID,
      generation: GEN, revision: 4, one_time: true, expires_in_seconds: 45 })),
    dispatch: vi.fn<FileImportTransport["dispatch"]>(async () => receipt(retained(readFileImport(ID, storage)).copy)),
    recover: vi.fn<FileImportTransport["recover"]>(async () => receipt(retained(readFileImport(ID, storage)).copy, true)),
    current: vi.fn<FileImportTransport["current"]>(async () => ({ ...source(), revision: 15, title: "Ten later editorial revisions" })),
  } satisfies FileImportTransport;
  return port;
}

afterEach(() => vi.unstubAllGlobals());

describe("retained file import", () => {
  it("retains the submitted path/label and normalized intent before the one-time grant and dispatch", async () => {
    const storage = new MemoryStorage();
    const port = transport(storage);
    port.prepare.mockImplementation(async request => {
      expect(readFileImport(ID, storage).kind).toBe("empty");
      expect(request.path).toBe(input().path);
      request.path = "Synthetic transport mutation";
      return preparation();
    });
    port.grant.mockImplementation(async prepared => {
      const original = retained(readFileImport(ID, storage)).copy;
      expect(original.input.path).toBe(input().path);
      if (original.input.kind !== "voice") throw new Error("Expected the original voice input");
      expect(original.input.label).toBe(input().label);
      expect(original.preparation).toEqual(preparation());
      if (prepared.intent.kind !== "voice") throw new Error("Expected the prepared voice intent");
      prepared.intent.name = "Synthetic mutable grant argument";
      return { token: TOKEN, effect: "import_local", project_id: ID, generation: GEN, revision: 4, one_time: true };
    });
    port.dispatch.mockImplementation(async (kind, request) => {
      const copy = retained(readFileImport(ID, storage)).copy;
      expect(kind).toBe("voice");
      expect(request).toEqual({ project_id: ID, generation: GEN, revision: 4, request_id: copy.request_id,
        path: preparation().path, intent: preparation().intent, effect_grant: TOKEN });
      if (request.intent.kind !== "voice") throw new Error("Expected the dispatched voice intent");
      request.intent.name = "Synthetic mutable dispatch argument";
      return receipt(copy);
    });
    const completed = await importFileWithRetention(source(), input(), port, storage);
    expect(completed.project.revision).toBe(15);
    expect(completed.project.title).toBe("Ten later editorial revisions");
    expect(port.prepare).toHaveBeenCalledOnce(); expect(port.grant).toHaveBeenCalledOnce();
    expect(port.dispatch).toHaveBeenCalledOnce(); expect(port.recover).not.toHaveBeenCalled();
    expect(storage.writes).toBe(1); expect(storage.removals).toBe(0);
    expect(completed.retained.raw).not.toContain(TOKEN);
    expect(completed.retained.raw).not.toContain("Synthetic mutable");
    removeFileImport(ID, completed.retained.key, completed.retained.raw, storage);
    expect(readFileImport(ID, storage).kind).toBe("empty");
  });

  it("keeps one identity after a lost acknowledgement, then reads the original receipt and latest document", async () => {
    const storage = new MemoryStorage(); const port = transport(storage);
    port.dispatch.mockRejectedValueOnce(new Error("Synthetic acknowledgement lost after commit"));
    await expect(importFileWithRetention(source(), input(), port, storage)).rejects.toThrow("acknowledgement lost");
    const copy = retained(readFileImport(ID, storage));
    const raw = copy.raw;
    await expect(importFileWithRetention(source(), input(), port, storage)).rejects.toThrow("retained import");
    const latest = await recoverFileImportWithRetention({ ...source(), revision: 12 }, copy, port, storage);
    expect(latest?.revision).toBe(15);
    expect(port.recover).toHaveBeenCalledExactlyOnceWith({ project_id: ID, generation: GEN, revision: 4,
      request_id: copy.copy.request_id, intent: preparation().intent });
    expect(port.grant).toHaveBeenCalledOnce(); expect(port.dispatch).toHaveBeenCalledOnce();
    expect(retained(readFileImport(ID, storage)).raw).toBe(raw);
  });

  it("a missing recovery receipt does not retry, consume a grant, read a file, or discard the local request", async () => {
    const storage = new MemoryStorage(); const original = save(storage); const port = transport(storage);
    port.recover.mockResolvedValue(null);
    expect(await recoverFileImportWithRetention(source(), original, port, storage)).toBeNull();
    expect(port.prepare).not.toHaveBeenCalled(); expect(port.grant).not.toHaveBeenCalled();
    expect(port.dispatch).not.toHaveBeenCalled(); expect(port.current).not.toHaveBeenCalled();
    expect(retained(readFileImport(ID, storage)).raw).toBe(original.raw);
  });

  it.each(["unavailable", "quota", "drift"])("blocks effects when retention is %s", async mode => {
    const storage = new MemoryStorage(); const port = transport(storage);
    if (mode === "unavailable") storage.failGet = true;
    if (mode === "quota") storage.failSet = true;
    if (mode === "drift") storage.afterSet = key => storage.data.set(key, "peer changed the copy");
    await expect(importFileWithRetention(source(), input(), port, storage)).rejects.toThrow();
    expect(port.grant).not.toHaveBeenCalled(); expect(port.dispatch).not.toHaveBeenCalled();
    if (mode === "drift") expect(Array.from(storage.data.values())).toEqual(["peer changed the copy"]);
  });

  it.each(["removed", "replaced", "unreadable"])("rechecks the local copy after the asynchronous grant: %s", async mode => {
    const storage = new MemoryStorage(); const port = transport(storage);
    port.grant.mockImplementation(async () => {
      const key = retained(readFileImport(ID, storage)).key;
      if (mode === "removed") storage.data.delete(key);
      if (mode === "replaced") storage.data.set(key, "another original");
      if (mode === "unreadable") storage.failGet = true;
      return { token: TOKEN, effect: "import_local", project_id: ID, generation: GEN, revision: 4, one_time: true };
    });
    await expect(importFileWithRetention(source(), input(), port, storage)).rejects.toThrow();
    expect(port.dispatch).not.toHaveBeenCalled(); expect(storage.removals).toBe(0);
  });

  it.each(["project", "generation", "revision", "path", "label", "unknown"])("rejects mismatched preparation before retention/grants: %s", async field => {
    const storage = new MemoryStorage(); const port = transport(storage);
    const invalid = preparation();
    if (field === "project") invalid.project_id = NEXT_GEN;
    if (field === "generation") invalid.generation = NEXT_GEN;
    if (field === "revision") invalid.revision++;
    if (field === "path") invalid.path += ".different";
    if (field === "label" && invalid.intent.kind === "voice") invalid.intent.label = "Different take";
    port.prepare.mockResolvedValue(field === "unknown" ? { ...invalid, effect_grant: TOKEN } : invalid);
    await expect(importFileWithRetention(source(), input(), port, storage)).rejects.toThrow();
    expect(storage.writes).toBe(0); expect(port.grant).not.toHaveBeenCalled(); expect(port.dispatch).not.toHaveBeenCalled();
  });

  it.each(["generation", "revision", "kind", "nil", "reusable"])("rejects an invalid grant and preserves the copy: %s", async field => {
    const storage = new MemoryStorage(); const port = transport(storage);
    const grant = { token: TOKEN, effect: "import_local", project_id: ID, generation: GEN, revision: 4, one_time: true };
    if (field === "generation") grant.generation = NEXT_GEN;
    if (field === "revision") grant.revision++;
    if (field === "kind") grant.effect = "project_create";
    if (field === "nil") grant.token = NIL;
    if (field === "reusable") grant.one_time = false;
    port.grant.mockResolvedValue(grant);
    await expect(importFileWithRetention(source(), input(), port, storage)).rejects.toThrow();
    expect(port.dispatch).not.toHaveBeenCalled(); expect(readFileImport(ID, storage).kind).toBe("retained");
  });

  it.each(["request", "intent", "project", "generation", "revision", "unknown"])("rejects a mismatched receipt: %s", field => {
    const storage = new MemoryStorage(); const copy = save(storage).copy; const invalid = receipt(copy);
    if (field === "request") invalid.request_id = TOKEN;
    if (field === "intent" && invalid.intent.kind === "voice") invalid.intent.name += ".changed";
    if (field === "project") invalid.project.id = TOKEN;
    if (field === "generation") invalid.project.generation = NEXT_GEN;
    if (field === "revision") invalid.project.revision = 15;
    expect(() => parseFileImportReceipt(field === "unknown" ? { ...invalid, path: "unexpected" } : invalid, copy)).toThrow();
  });

  it.each(["older", "before-visible", "generation", "other-project", "unavailable"])("never replaces current edits with an unsuitable project: %s", async mode => {
    const storage = new MemoryStorage(); const original = save(storage); const port = transport(storage);
    const next = { ...source(), revision: 15 };
    if (mode === "older") next.revision = 4;
    if (mode === "before-visible") next.revision = 10;
    if (mode === "generation") next.generation = NEXT_GEN;
    if (mode === "other-project") next.id = TOKEN;
    port.current.mockResolvedValue(next);
    if (mode === "unavailable") port.current.mockRejectedValue(new Error("Synthetic read unavailable"));
    await expect(recoverFileImportWithRetention({ ...source(), revision: 12 }, original, port, storage)).rejects.toThrow();
    expect(storage.removals).toBe(0); expect(retained(readFileImport(ID, storage)).raw).toBe(original.raw);
  });

  it("preserves an old generation visibly under the same project key and prevents its recovery or replacement", async () => {
    const storage = new MemoryStorage(); const original = save(storage); const port = transport(storage);
    expect(readFileImport(ID, storage).kind).toBe("retained");
    await expect(recoverFileImportWithRetention({ ...source(), generation: NEXT_GEN }, original, port, storage)).rejects.toThrow("authority");
    await expect(importFileWithRetention({ ...source(), generation: NEXT_GEN }, input(), port, storage)).rejects.toThrow("retained import");
    expect(port.recover).not.toHaveBeenCalled(); expect(port.prepare).not.toHaveBeenCalled();
    expect(() => assertNoFileImport(TOKEN, storage)).not.toThrow();
  });

  it.each(["{invalid", "\ud800 broken UTF-16", "x".repeat(65_537)])("keeps a malformed or oversized local copy until exact explicit removal", raw => {
    const storage = new MemoryStorage(); const key = fileImportKey(ID, REQUEST); storage.data.set(key, raw);
    const state = readFileImport(ID, storage);
    expect(state.kind).toBe("invalid");
    if (state.kind !== "invalid") throw new Error("Expected invalid test copy");
    expect(state.raw).toBe(raw);
    expect(JSON.parse(fileImportDownload(ID, raw)).raw).toBe(raw);
    expect(() => assertNoFileImport(ID, storage)).toThrow("retained import");
    removeFileImport(ID, key, raw, storage);
    expect(storage.removals).toBe(1);
  });

  it("will not remove another view's changed original, and reports a removal failure", () => {
    const storage = new MemoryStorage(); const original = save(storage);
    storage.data.set(original.key, "peer retained work");
    expect(() => removeFileImport(ID, original.key, original.raw, storage)).toThrow("changed");
    expect(storage.data.get(original.key)).toBe("peer retained work");
    storage.failRemove = true;
    expect(() => removeFileImport(ID, original.key, "peer retained work", storage)).toThrow("removal denied");
    expect(storage.data.get(original.key)).toBe("peer retained work");
  });

  it("rejects in-memory mutation even when the saved raw string is unchanged", async () => {
    const storage = new MemoryStorage(); const original = save(storage); const port = transport(storage);
    if (original.copy.preparation.intent.kind !== "voice") throw new Error("Expected the retained voice intent");
    original.copy.preparation.intent.name = "Changed in this view";
    expect(() => requireFileImport(original, storage)).toThrow("retained original");
    await expect(recoverFileImportWithRetention(source(), original, port, storage)).rejects.toThrow();
    expect(port.recover).not.toHaveBeenCalled();
  });

  it("does not silently normalize retained identities, unknown fields, or grant material", () => {
    const storage = new MemoryStorage(); const original = save(storage);
    for (const value of [ { ...original.copy, request_id: NIL }, { ...original.copy, request_id: REQUEST.toUpperCase() },
      { ...original.copy, effect_grant: TOKEN }, { ...original.copy, preparation: { ...preparation(), revision: Number.MAX_SAFE_INTEGER } } ]) {
      const raw = JSON.stringify(value); storage.data.set(original.key, raw);
      expect(readFileImport(ID, storage).kind).toBe("invalid");
      expect(storage.data.get(original.key)).toBe(raw);
    }
  });

  it("reports a denied localStorage getter without dropping any pending state into an empty result", () => {
    vi.stubGlobal("window", Object.defineProperty({}, "localStorage", { get() { throw new Error("Synthetic getter denied"); } }));
    expect(readFileImport(ID).kind).toBe("unreadable");
    expect(() => assertNoFileImport(ID)).toThrow("cannot be retained");
  });

  it("preserves source basename/stem spaces supplied by desktop, without a second metadata inference", () => {
    const storage = new MemoryStorage(); const prepared = preparation();
    prepared.path = "E:\\Media\\ name .wav";
    prepared.intent = { kind: "voice", name: " name .wav", media_type: "audio/wav", label: " name " };
    const copy = retainFileImport(prepared, { kind: "voice", path: prepared.path }, storage, REQUEST);
    expect(copy.copy.preparation.intent).toEqual(prepared.intent);
  });

  it.each(["name", "label"] as const)("rejects trim-empty prepared %s before retaining a request", field => {
    const storage = new MemoryStorage(); const prepared = preparation();
    if (prepared.intent.kind !== "voice") throw new Error("Expected voice metadata");
    prepared.intent[field] = " \u00a0 ";
    expect(() => retainFileImport(prepared, { kind: "voice", path: prepared.path }, storage, REQUEST)).toThrow("empty");
    expect(storage.writes).toBe(0); expect(storage.data.size).toBe(0);
  });

  it("preserves both original requests after interleaved retention by two windows", () => {
    const storage = new MemoryStorage(); let peer: RetainedFileImport | undefined;
    // A has passed its advisory precheck; B also passes and writes before A.
    // This controls only local retention. It does not assert backend CAS results.
    storage.beforeSet = () => {
      storage.beforeSet = null;
      peer = retainFileImport(preparation(), input(), storage, TOKEN, 1_790_000_000_001);
    };
    const first = save(storage);
    if (!peer) throw new Error("Expected the peer retention interleaving");
    expect(first.key).not.toBe(peer.key);
    expect(storage.getItem(first.key)).toBe(first.raw);
    expect(storage.getItem(peer.key)).toBe(peer.raw);
    expect(requireFileImport(first, storage)).toEqual(first.copy);
    expect(requireFileImport(peer, storage)).toEqual(peer.copy);
    expect(storage.writes).toBe(2); expect(storage.removals).toBe(0);
  });
});

describe("append-only file import list", () => {
  function independentlySaved(requestId: string, generation = GEN, createdAt = 1_790_000_000_000): RetainedFileImport {
    return retainFileImport({ ...preparation(), generation }, input(), new MemoryStorage(), requestId, createdAt);
  }
  function entries(storage: MemoryStorage): RetainedFileImport[] {
    const result = readFileImports(ID, storage);
    expect(result.limited).toBe(false); expect(result.error).toBeNull();
    return result.copies.map(entry => {
      if (entry.kind !== "retained") throw new Error("Expected valid retained entries");
      return entry.retained;
    });
  }

  it("keeps exact legacy snapshots beside newer requests and older project generations", () => {
    const storage = new MemoryStorage();
    const legacy = independentlySaved(REQUEST);
    const olderGeneration = independentlySaved(TOKEN, NEXT_GEN, legacy.copy.created_at + 1);
    const newest = independentlySaved(GEN, GEN, legacy.copy.created_at + 2);
    storage.data.set(FILE_IMPORT_PREFIX + ID, legacy.raw);
    storage.data.set(olderGeneration.key, olderGeneration.raw);
    storage.data.set(newest.key, newest.raw);
    const found = entries(storage);
    expect(found.map(item => item.key)).toEqual([newest.key, olderGeneration.key, FILE_IMPORT_PREFIX + ID]);
    expect(found.map(item => item.raw)).toEqual([newest.raw, olderGeneration.raw, legacy.raw]);
    expect(found[2].copy).toEqual(legacy.copy);
    expect(Object.keys(found[2].copy)).toEqual(["schema", "request_id", "created_at", "preparation", "input"]);
    expect(readFileImport(ID, storage).kind).toBe("unreadable");
    expect(() => assertNoFileImport(ID, storage)).toThrow("retained import");
    expect(storage.writes).toBe(0); expect(storage.removals).toBe(0);
  });

  it.each(["legacy", "current", "older-generation", "invalid"])("blocks a new preparation while any %s copy exists", async mode => {
    const storage = new MemoryStorage(); const copy = independentlySaved(REQUEST, mode === "older-generation" ? NEXT_GEN : GEN);
    storage.data.set(mode === "legacy" ? FILE_IMPORT_PREFIX + ID : copy.key, mode === "invalid" ? "{unfinished" : copy.raw);
    const port = transport(storage);
    await expect(importFileWithRetention(source(), input(), port, storage)).rejects.toThrow("retained import");
    expect(port.prepare).not.toHaveBeenCalled(); expect(port.grant).not.toHaveBeenCalled();
    expect(port.dispatch).not.toHaveBeenCalled(); expect(storage.writes).toBe(0); expect(storage.removals).toBe(0);
  });

  it("recovers only the selected original receipt, reads later edits, and removes only that request after publication", async () => {
    const storage = new MemoryStorage(); const first = independentlySaved(REQUEST); const second = independentlySaved(TOKEN);
    storage.data.set(first.key, first.raw); storage.data.set(second.key, second.raw);
    const port = transport(storage);
    port.recover.mockResolvedValue(receipt(second.copy, true));
    const current = await recoverFileImportWithRetention({ ...source(), revision: 12 }, second, port, storage);
    expect(current?.revision).toBe(15); expect(current?.title).toBe("Ten later editorial revisions");
    expect(port.recover).toHaveBeenCalledExactlyOnceWith({ project_id: ID, generation: GEN, revision: 4,
      request_id: TOKEN, intent: preparation().intent });
    expect(port.current).toHaveBeenCalledExactlyOnceWith(ID);
    expect(port.prepare).not.toHaveBeenCalled(); expect(port.grant).not.toHaveBeenCalled(); expect(port.dispatch).not.toHaveBeenCalled();
    expect(storage.removals).toBe(0);
    // Models explicit caller cleanup after its coordinator publishes current.
    removeFileImport(ID, second.key, second.raw, storage);
    expect(entries(storage).map(item => item.key)).toEqual([first.key]);
    expect(storage.getItem(first.key)).toBe(first.raw); expect(storage.getItem(second.key)).toBeNull();
  });

  it("keeps every local identity when the selected request has no receipt", async () => {
    const storage = new MemoryStorage(); const first = independentlySaved(REQUEST); const second = independentlySaved(TOKEN);
    storage.data.set(first.key, first.raw); storage.data.set(second.key, second.raw);
    const port = transport(storage); port.recover.mockResolvedValue(null);
    expect(await recoverFileImportWithRetention(source(), first, port, storage)).toBeNull();
    expect(entries(storage).map(item => item.raw)).toEqual([first.raw, second.raw]);
    expect(port.current).not.toHaveBeenCalled(); expect(port.grant).not.toHaveBeenCalled();
    expect(port.dispatch).not.toHaveBeenCalled(); expect(storage.writes).toBe(0); expect(storage.removals).toBe(0);
  });

  it("preserves malformed own keys and bytes while excluding other project namespaces", () => {
    const storage = new MemoryStorage(); const valid = independentlySaved(REQUEST);
    const own = [FILE_IMPORT_PREFIX + ID, fileImportProjectPrefix(ID) + "broken-request", fileImportKey(ID, TOKEN)];
    const raws = ["\ud800 broken legacy", valid.raw, valid.raw];
    own.forEach((key, index) => storage.data.set(key, raws[index]));
    storage.data.set(fileImportKey(TOKEN, REQUEST), valid.raw);
    storage.data.set(FILE_IMPORT_PREFIX + ID + "-foreign:" + REQUEST, valid.raw);
    storage.data.set("unrelated-key", "unrelated-data");
    const found = readFileImports(ID, storage);
    expect(found.limited).toBe(false); expect(found.error).toBeNull();
    expect(found.copies).toHaveLength(3);
    for (const entry of found.copies) {
      if (entry.kind !== "invalid") throw new Error("Malformed identities must remain invalid");
      expect(entry.raw).toBe(raws[own.indexOf(entry.key)]);
      expect(JSON.parse(fileImportDownload(ID, entry.raw)).raw).toBe(entry.raw);
      removeFileImport(ID, entry.key, entry.raw, storage);
    }
    expect(readFileImports(ID, storage)).toEqual({ copies: [], limited: false, error: null });
    expect(storage.data.size).toBe(3); expect(storage.writes).toBe(0); expect(storage.removals).toBe(3);
  });

  it.each(["foreign", "prefix-lookalike", "unscoped"])("rejects removal outside the exact project namespace: %s", mode => {
    const storage = new MemoryStorage(); const copy = independentlySaved(REQUEST);
    const key = mode === "foreign" ? fileImportKey(TOKEN, REQUEST)
      : mode === "prefix-lookalike" ? FILE_IMPORT_PREFIX + ID + "-foreign:" + REQUEST : "unrelated-key";
    storage.data.set(key, copy.raw);
    expect(() => removeFileImport(ID, key, copy.raw, storage)).toThrow("another project");
    expect(storage.getItem(key)).toBe(copy.raw); expect(storage.removals).toBe(0);
  });

  it("blocks imports if the storage-key bound can hide a request even when the visible list is empty", async () => {
    const storage = new MemoryStorage(); const copy = independentlySaved(REQUEST);
    for (let index = 0; index < 4_096; index++) storage.data.set(`unrelated:${index}`, "value");
    storage.data.set(copy.key, copy.raw);
    expect(readFileImports(ID, storage)).toEqual({ copies: [], limited: true, error: null });
    const port = transport(storage);
    await expect(importFileWithRetention(source(), input(), port, storage)).rejects.toThrow("incomplete");
    expect(port.prepare).not.toHaveBeenCalled(); expect(port.grant).not.toHaveBeenCalled();
    expect(port.dispatch).not.toHaveBeenCalled(); expect(storage.getItem(copy.key)).toBe(copy.raw);
  });

  it("still exposes the known legacy key when it lies beyond the storage scan bound", () => {
    const storage = new MemoryStorage(); const copy = independentlySaved(REQUEST); const key = FILE_IMPORT_PREFIX + ID;
    for (let index = 0; index < 4_096; index++) storage.data.set(`unrelated:${index}`, "value");
    storage.data.set(key, copy.raw);
    const result = readFileImports(ID, storage);
    expect(result.limited).toBe(true); expect(result.error).toBeNull();
    expect(result.copies).toEqual([{ kind: "retained", retained: { ...copy, key } }]);
    expect(storage.getItem(key)).toBe(copy.raw); expect(storage.removals).toBe(0);
  });

  it("marks a list of more than 128 project copies as incomplete without discarding the hidden originals", () => {
    const storage = new MemoryStorage();
    for (let index = 0; index < 129; index++) storage.data.set(fileImportProjectPrefix(ID) + `invalid-${index}`, `original-${index}`);
    const found = readFileImports(ID, storage);
    expect(found.copies).toHaveLength(128); expect(found.limited).toBe(true); expect(found.error).toBeNull();
    expect(() => assertNoFileImport(ID, storage)).toThrow("incomplete");
    expect(storage.data.size).toBe(129); expect(storage.writes).toBe(0); expect(storage.removals).toBe(0);
  });

  it.each(["missing-key", "duplicate-key", "length-drift"])("treats observable enumeration drift as incomplete: %s", mode => {
    const storage = new MemoryStorage(); storage.data.set("first", "a"); storage.data.set("second", "b");
    storage.keyOverride = index => {
      if (mode === "missing-key") return null;
      if (mode === "duplicate-key") return "first";
      storage.data.set("new-peer-key", "c");
      return index === 0 ? "first" : "second";
    };
    const result = readFileImports(ID, storage);
    expect(result.copies).toEqual([]); expect(result.limited).toBe(true); expect(result.error).toBeNull();
    expect(() => assertNoFileImport(ID, storage)).toThrow("incomplete");
    expect(storage.writes).toBe(0); expect(storage.removals).toBe(0);
  });

  it.each(["length", "key"])("retains already-read legacy bytes when the %s index becomes unavailable", field => {
    const storage = new MemoryStorage(); const copy = independentlySaved(REQUEST); const key = FILE_IMPORT_PREFIX + ID;
    storage.data.set(key, copy.raw); storage.failLength = field === "length"; storage.failKey = field === "key";
    const result = readFileImports(ID, storage);
    expect(result.error).toContain("unavailable");
    expect(result.copies).toEqual([{ kind: "retained", retained: { ...copy, key } }]);
    expect(readFileImport(ID, storage).kind).toBe("unreadable");
    expect(() => assertNoFileImport(ID, storage)).toThrow("unavailable");
    expect(storage.getItem(key)).toBe(copy.raw); expect(storage.writes).toBe(0); expect(storage.removals).toBe(0);
  });

  it.each(["key", "raw", "request"])("rejects tampering with the selected %s before recovery or cleanup", async field => {
    const storage = new MemoryStorage(); const original = save(storage); const selected = structuredClone(original);
    if (field === "key") selected.key = fileImportKey(ID, TOKEN);
    if (field === "raw") selected.raw += " ";
    if (field === "request") selected.copy.request_id = TOKEN;
    const port = transport(storage);
    await expect(recoverFileImportWithRetention(source(), selected, port, storage)).rejects.toThrow();
    expect(port.recover).not.toHaveBeenCalled(); expect(port.current).not.toHaveBeenCalled();
    expect(storage.getItem(original.key)).toBe(original.raw); expect(storage.removals).toBe(0);
  });

  it("will not overwrite an already retained request identity", () => {
    const storage = new MemoryStorage(); const first = save(storage);
    expect(() => retainFileImport(preparation(), input(), storage, REQUEST, first.copy.created_at + 1)).toThrow("retained import");
    expect(storage.getItem(first.key)).toBe(first.raw); expect(storage.writes).toBe(1); expect(storage.removals).toBe(0);
  });
});

const nativeSpec = (): NativeCapsuleSourceSpec7 => ({
  name: "  Opening source.blend  ", media_type: "application/octet-stream", capsule_id: NEXT_GEN, scene_id: GEN,
  label: "  Opening capsule\nEditorial version  ",
  editable_parameters: ["Title = \"Opening\"", "Camera focal length", "Title = \"Opening\"", "Accent colour"],
  native_editor_hint: "  Open the source in Blender\nKeep the authored camera  ",
});
const nativeInput = (spec = nativeSpec()) => ({
  kind: "native_capsule_source", path: "  E:\\Media\\opening source.blend  ", spec,
} satisfies FileImportInput);
function nativePreparation(submitted = nativeInput()): PreparedFileImport & { intent: { kind: "native_capsule_source"; spec: NativeCapsuleSourceSpec7 } } {
  return { schema: "motionwright.file-import-preparation/1", project_id: ID, generation: GEN, revision: 4,
    path: submitted.path.trim(), intent: { kind: "native_capsule_source", spec: structuredClone(submitted.spec) } };
}
function saveNative(storage: MemoryStorage, submitted = nativeInput(), request = REQUEST): RetainedFileImport {
  return retainFileImport(nativePreparation(submitted), submitted, storage, request, 1_790_000_000_000);
}
function nativeTransport(storage: MemoryStorage, submitted = nativeInput()) {
  const port = transport(storage);
  port.prepare.mockResolvedValue(nativePreparation(submitted));
  return port;
}

describe("native source spec contract", () => {
  it("preserves all seven fields, spaces, line feeds, duplicate parameters and their order without aliasing", () => {
    const submitted = nativeSpec(); const expected = structuredClone(submitted);
    const parsed = parseNativeCapsuleSourceSpec(submitted);
    expect(parsed).toEqual(expected); expect(parsed).not.toBe(submitted);
    expect(parsed.editable_parameters).not.toBe(submitted.editable_parameters);
    submitted.editable_parameters.reverse(); submitted.label = "Changed after validation";
    expect(parsed).toEqual(expected); expect(sameNativeCapsuleSourceSpec(parsed, expected)).toBe(true);
    expect(sameNativeCapsuleSourceSpec(parsed, { ...expected, editable_parameters: [...expected.editable_parameters].reverse() })).toBe(false);
    expect(parseNativeCapsuleSourceSpec({ ...expected, editable_parameters: [] }).editable_parameters).toEqual([]);
    expect(fileImportIntentName({ kind: "native_capsule_source", spec: parsed })).toBe(expected.name);
    expect(fileImportIntentMediaType({ kind: "native_capsule_source", spec: parsed })).toBe(expected.media_type);
    expect(fileImportIntentName({ kind: "asset", name: "Existing image", media_type: "image/png" })).toBe("Existing image");
    expect(fileImportIntentMediaType(preparation().intent)).toBe("audio/wav");
  });

  it("matches Rust's Unicode scalar, White_Space and control rules without rewriting authored text", () => {
    const spec = { ...nativeSpec(), name: "\uFEFF", media_type: "application/\uFEFFcustom", label: "\uFEFF",
      native_editor_hint: "line one\nline two", editable_parameters: ["🙂".repeat(500)] };
    expect(parseNativeCapsuleSourceSpec(spec)).toEqual(spec);
    expect(() => parseNativeCapsuleSourceSpec({ ...spec, name: "\u0085" })).toThrow();
    expect(() => parseNativeCapsuleSourceSpec({ ...spec, media_type: "application/\u0085custom" })).toThrow();
    expect(() => parseNativeCapsuleSourceSpec({ ...spec, label: "line\u0085two" })).toThrow();
    expect(() => parseNativeCapsuleSourceSpec({ ...spec, label: "\n\n" })).toThrow();
  });

  it.each([
    ["name UTF-8 bound", { name: "é".repeat(257) }],
    ["media UTF-8 bound", { media_type: "é".repeat(128) }],
    ["label UTF-8 bound", { label: "é".repeat(129) }],
    ["hint UTF-8 bound", { native_editor_hint: "é".repeat(129) }],
    ["parameter UTF-8 bound", { editable_parameters: ["🙂".repeat(501)] }],
    ["parameter count", { editable_parameters: Array.from({ length: 65 }, () => "parameter") }],
    ["sparse parameter array", { editable_parameters: new Array(1) }],
    ["non-string parameter", { editable_parameters: [1] }],
    ["blank parameter", { editable_parameters: [" \n "] }],
    ["parameter carriage return", { editable_parameters: ["line\rtwo"] }],
    ["label tab", { label: "line\ttwo" }],
    ["hint DEL", { native_editor_hint: "line\u007ftwo" }],
    ["hint C1 control", { native_editor_hint: "line\u009ftwo" }],
    ["name lone high surrogate", { name: "name\ud800" }],
    ["name lone low surrogate", { name: "name\udfff" }],
    ["parameter lone surrogate", { editable_parameters: ["parameter\ud800"] }],
    ["uppercase capsule identity", { capsule_id: NEXT_GEN.toUpperCase() }],
    ["nil capsule identity", { capsule_id: NIL }],
    ["nil scene identity", { scene_id: NIL }],
    ["noncanonical scene identity", { scene_id: GEN.replaceAll("-", "") }],
    ["unknown source digest", { source_sha256: "a".repeat(64) }],
    ["unknown fidelity", { fidelity: "verified" }],
    ["unknown grant", { effect_grant: TOKEN }],
  ])("rejects %s before any transport or local write", async (_label, patch) => {
    const spec = { ...nativeSpec(), ...patch };
    expect(() => parseNativeCapsuleSourceSpec(spec)).toThrow();
    const storage = new MemoryStorage(); const port = nativeTransport(storage);
    await expect(importFileWithRetention(source(), { ...nativeInput(), spec } as FileImportInput, port, storage)).rejects.toThrow();
    expect(port.prepare).not.toHaveBeenCalled(); expect(port.grant).not.toHaveBeenCalled();
    expect(port.dispatch).not.toHaveBeenCalled(); expect(storage.writes).toBe(0);
  });

  it("rejects missing fields and non-record specs instead of inferring native metadata", () => {
    for (const field of Object.keys(nativeSpec())) {
      const spec: Record<string, unknown> = { ...nativeSpec() }; delete spec[field];
      expect(() => parseNativeCapsuleSourceSpec(spec)).toThrow();
    }
    for (const value of [null, undefined, [], "source", 1]) expect(() => parseNativeCapsuleSourceSpec(value)).toThrow();
  });
});

const nativeSubstitutions: Array<[string, (spec: NativeCapsuleSourceSpec7) => void]> = [
  ["name", spec => { spec.name = "Different source.blend"; }],
  ["media type", spec => { spec.media_type = "application/x-other"; }],
  ["capsule identity", spec => { spec.capsule_id = TOKEN; }],
  ["scene identity", spec => { spec.scene_id = TOKEN; }],
  ["label spacing", spec => { spec.label = spec.label.trim(); }],
  ["parameter order", spec => { spec.editable_parameters.reverse(); }],
  ["parameter contents", spec => { spec.editable_parameters[1] = "Different camera"; }],
  ["parameter count", spec => { spec.editable_parameters.pop(); }],
  ["editor hint", spec => { spec.native_editor_hint = "Different editor"; }],
];

describe("retained native source import", () => {
  it("round trips the full native input and intent through durable readback before grant and dispatch", async () => {
    const storage = new MemoryStorage(); const submitted = nativeInput(); const expected = structuredClone(submitted);
    const port = nativeTransport(storage, submitted);
    port.prepare.mockImplementation(async request => {
      expect(request).toEqual({ project_id: ID, generation: GEN, revision: 4, ...expected });
      expect(storage.writes).toBe(0);
      if (request.kind !== "native_capsule_source") throw new Error("Expected the native request");
      request.spec.editable_parameters.reverse(); request.spec.label = "Mutated prepare argument";
      return nativePreparation(expected);
    });
    port.grant.mockImplementation(async prepared => {
      const saved = retained(readFileImport(ID, storage));
      expect(saved.copy.input).toEqual(expected);
      expect(saved.copy.preparation).toEqual(nativePreparation(expected));
      expect(requireFileImport(saved, storage)).toEqual(saved.copy);
      if (prepared.intent.kind !== "native_capsule_source") throw new Error("Expected the native preparation");
      prepared.intent.spec.label = "Mutated grant argument";
      prepared.intent.spec.editable_parameters.reverse();
      return { token: TOKEN, effect: "import_local", project_id: ID, generation: GEN, revision: 4, one_time: true };
    });
    port.dispatch.mockImplementation(async (kind, request) => {
      const copy = retained(readFileImport(ID, storage)).copy;
      expect(kind).toBe("native_capsule_source");
      expect(request).toEqual({ project_id: ID, generation: GEN, revision: 4, request_id: copy.request_id,
        path: expected.path.trim(), intent: nativePreparation(expected).intent, effect_grant: TOKEN });
      if (request.intent.kind !== "native_capsule_source") throw new Error("Expected the native intent");
      request.intent.spec.editable_parameters[0] = "Mutated dispatch argument";
      return receipt(copy);
    });
    const completed = await importFileWithRetention(source(), submitted, port, storage);
    expect(completed.project.revision).toBe(15); expect(completed.retained.copy.input).toEqual(expected);
    expect(completed.retained.copy.preparation.intent).toEqual(nativePreparation(expected).intent);
    expect(Object.keys(completed.retained.copy.input)).toEqual(["kind", "path", "spec"]);
    expect(Object.keys(completed.retained.copy.preparation.intent)).toEqual(["kind", "spec"]);
    expect(JSON.parse(fileImportDownload(ID, completed.retained.raw)).raw).toBe(completed.retained.raw);
    expect(submitted).toEqual(expected); expect(completed.retained.raw).not.toContain(TOKEN);
    expect(completed.retained.raw).not.toContain("Mutated");
    expect(port.prepare).toHaveBeenCalledOnce(); expect(port.grant).toHaveBeenCalledOnce();
    expect(port.dispatch).toHaveBeenCalledOnce(); expect(port.recover).not.toHaveBeenCalled();
    expect(storage.writes).toBe(1); expect(storage.removals).toBe(0);
  });

  it("retains every authored native field across UNKNOWN and only reads the original receipt without replay", async () => {
    const storage = new MemoryStorage(); const submitted = nativeInput(); const expected = structuredClone(submitted);
    const port = nativeTransport(storage, submitted);
    port.dispatch.mockRejectedValueOnce(new Error("Synthetic native acknowledgement lost after commit"));
    await expect(importFileWithRetention(source(), submitted, port, storage)).rejects.toThrow("acknowledgement lost");
    const original = retained(readFileImport(ID, storage));
    submitted.path = "E:\\Media\\later source.blend"; submitted.spec.label = "Later creative input";
    submitted.spec.editable_parameters.reverse();
    expect(original.copy.input).toEqual(expected);
    await expect(importFileWithRetention(source(), submitted, port, storage)).rejects.toThrow("retained import");
    port.recover.mockResolvedValueOnce(null).mockResolvedValueOnce(receipt(original.copy, true));
    expect(await recoverFileImportWithRetention(source(), original, port, storage)).toBeNull();
    expect(port.current).not.toHaveBeenCalled();
    const latest = await recoverFileImportWithRetention({ ...source(), revision: 12 }, original, port, storage);
    expect(latest?.revision).toBe(15);
    expect(port.recover).toHaveBeenNthCalledWith(2, { project_id: ID, generation: GEN, revision: 4,
      request_id: original.copy.request_id, intent: nativePreparation(expected).intent });
    expect(port.prepare).toHaveBeenCalledOnce(); expect(port.grant).toHaveBeenCalledOnce();
    expect(port.dispatch).toHaveBeenCalledOnce(); expect(port.recover).toHaveBeenCalledTimes(2);
    expect(port.current).toHaveBeenCalledExactlyOnceWith(ID);
    expect(storage.getItem(original.key)).toBe(original.raw); expect(storage.removals).toBe(0);
  });

  it.each(nativeSubstitutions)("rejects preparation substitution of %s before durable retention or effects", async (_label, substitute) => {
    const storage = new MemoryStorage(); const port = nativeTransport(storage);
    const prepared = nativePreparation(); substitute(prepared.intent.spec); port.prepare.mockResolvedValue(prepared);
    await expect(importFileWithRetention(source(), nativeInput(), port, storage)).rejects.toThrow("differs from the authored input");
    expect(storage.writes).toBe(0); expect(port.grant).not.toHaveBeenCalled(); expect(port.dispatch).not.toHaveBeenCalled();
  });

  it.each(nativeSubstitutions)("rejects historical receipt substitution of %s and keeps the UNKNOWN original", async (_label, substitute) => {
    const storage = new MemoryStorage(); const original = saveNative(storage); const port = nativeTransport(storage);
    const changed = receipt(original.copy, true);
    if (changed.intent.kind !== "native_capsule_source") throw new Error("Expected the native receipt");
    substitute(changed.intent.spec); port.recover.mockResolvedValue(changed);
    await expect(recoverFileImportWithRetention(source(), original, port, storage)).rejects.toThrow("differs from the original request");
    expect(port.current).not.toHaveBeenCalled(); expect(port.prepare).not.toHaveBeenCalled();
    expect(port.grant).not.toHaveBeenCalled(); expect(port.dispatch).not.toHaveBeenCalled();
    expect(storage.getItem(original.key)).toBe(original.raw); expect(storage.removals).toBe(0);
  });

  it.each(["request", "project", "generation", "revision"])("rejects native receipt %s identity mismatches", async field => {
    const storage = new MemoryStorage(); const original = saveNative(storage); const port = nativeTransport(storage);
    const changed = receipt(original.copy, true);
    if (field === "request") changed.request_id = TOKEN;
    if (field === "project") changed.project.id = TOKEN;
    if (field === "generation") changed.project.generation = NEXT_GEN;
    if (field === "revision") changed.project.revision++;
    port.recover.mockResolvedValue(changed);
    await expect(recoverFileImportWithRetention(source(), original, port, storage)).rejects.toThrow();
    expect(port.current).not.toHaveBeenCalled(); expect(port.grant).not.toHaveBeenCalled(); expect(port.dispatch).not.toHaveBeenCalled();
    expect(storage.getItem(original.key)).toBe(original.raw);
  });

  it.each(["quota", "silent-drop", "other-window", "unreadable"])("blocks native effects when durable readback is %s", async mode => {
    const storage = new MemoryStorage(); const port = nativeTransport(storage);
    if (mode === "quota") storage.failSet = true;
    if (mode === "silent-drop") storage.afterSet = key => storage.data.delete(key);
    if (mode === "other-window") storage.afterSet = key => storage.data.set(key, "Another window's native input");
    if (mode === "unreadable") storage.afterSet = () => { storage.failGet = true; };
    await expect(importFileWithRetention(source(), nativeInput(), port, storage)).rejects.toThrow();
    expect(port.grant).not.toHaveBeenCalled(); expect(port.dispatch).not.toHaveBeenCalled(); expect(storage.removals).toBe(0);
    if (mode === "other-window") expect([...storage.data.values()]).toEqual(["Another window's native input"]);
  });

  it("refuses another window's valid native replacement after the grant without overwriting either authored spec", async () => {
    const storage = new MemoryStorage(); const port = nativeTransport(storage); let original: RetainedFileImport | undefined;
    let peer: RetainedFileImport | undefined;
    port.grant.mockImplementation(async () => {
      original = retained(readFileImport(ID, storage));
      const other = nativeInput({ ...nativeSpec(), label: "Another window's capsule", editable_parameters: ["Other parameter"] });
      peer = saveNative(new MemoryStorage(), other, original.copy.request_id);
      storage.data.set(original.key, peer.raw);
      return { token: TOKEN, effect: "import_local", project_id: ID, generation: GEN, revision: 4, one_time: true };
    });
    await expect(importFileWithRetention(source(), nativeInput(), port, storage)).rejects.toThrow("changed");
    if (!original || !peer) throw new Error("Expected both retained originals");
    expect(original.copy.input).toEqual(nativeInput());
    expect(storage.getItem(peer.key)).toBe(peer.raw); expect(port.dispatch).not.toHaveBeenCalled();
    await expect(recoverFileImportWithRetention(source(), original, port, storage)).rejects.toThrow("changed");
    expect(port.recover).not.toHaveBeenCalled(); expect(storage.removals).toBe(0);
  });

  it("preserves two interleaved native requests and permits only the explicitly selected read-only recovery", async () => {
    const storage = new MemoryStorage(); let peer: RetainedFileImport | undefined;
    const other = nativeInput({ ...nativeSpec(), capsule_id: TOKEN, label: "Other authored capsule" });
    storage.beforeSet = () => { storage.beforeSet = null; peer = saveNative(storage, other, TOKEN); };
    const original = saveNative(storage);
    if (!peer) throw new Error("Expected the interleaved native request");
    expect(readFileImports(ID, storage).copies).toHaveLength(2);
    expect(requireFileImport(original, storage).input).toEqual(nativeInput());
    expect(requireFileImport(peer, storage).input).toEqual(other);
    const port = nativeTransport(storage); port.recover.mockResolvedValue(receipt(peer.copy, true));
    expect((await recoverFileImportWithRetention(source(), peer, port, storage))?.revision).toBe(15);
    expect(port.recover).toHaveBeenCalledExactlyOnceWith({ project_id: ID, generation: GEN, revision: 4,
      request_id: TOKEN, intent: nativePreparation(other).intent });
    expect(port.grant).not.toHaveBeenCalled(); expect(port.dispatch).not.toHaveBeenCalled();
    expect(storage.getItem(original.key)).toBe(original.raw); expect(storage.getItem(peer.key)).toBe(peer.raw);
    expect(storage.removals).toBe(0);
  });

  it.each(["input-extra", "intent-extra", "spec-extra", "input-order", "key-identity"])("keeps malformed native %s bytes available for explicit recovery", mode => {
    const storage = new MemoryStorage(); const original = saveNative(storage); const value = JSON.parse(original.raw);
    if (mode === "input-extra") value.input.label = null;
    if (mode === "intent-extra") value.preparation.intent.name = "Flattened legacy field";
    if (mode === "spec-extra") value.preparation.intent.spec.source_sha256 = "a".repeat(64);
    if (mode === "input-order") value.input.spec.editable_parameters.reverse();
    if (mode === "key-identity") value.request_id = TOKEN;
    const raw = JSON.stringify(value); storage.data.set(original.key, raw);
    const state = readFileImport(ID, storage); expect(state.kind).toBe("invalid");
    if (state.kind !== "invalid") throw new Error("Expected preserved malformed native bytes");
    expect(state.raw).toBe(raw); expect(JSON.parse(fileImportDownload(ID, raw)).raw).toBe(raw);
    expect(() => assertNoFileImport(ID, storage)).toThrow("retained import");
    expect(storage.getItem(original.key)).toBe(raw); expect(storage.removals).toBe(0);
  });

  it("retains the maximum native spec twice beyond the legacy bound without truncating authored data", () => {
    const maximum: NativeCapsuleSourceSpec7 = { ...nativeSpec(), name: "\"".repeat(512), media_type: "x".repeat(255),
      label: "\"".repeat(256), native_editor_hint: "\\".repeat(256), editable_parameters: Array.from({ length: 64 }, () => "\\".repeat(2_000)) };
    const submitted = { ...nativeInput(maximum), path: "E:\\Media\\" + "x".repeat(65_000) };
    const storage = new MemoryStorage(); const saved = saveNative(storage, submitted);
    expect(new TextEncoder().encode(saved.raw).length).toBeGreaterThan(65_536);
    expect(saved.copy.input).toEqual(submitted); expect(saved.copy.preparation.intent).toEqual(nativePreparation(submitted).intent);
    expect(retained(readFileImport(ID, storage)).raw).toBe(saved.raw);
    expect(requireFileImport(saved, storage)).toEqual(saved.copy);
  });

  it("keeps the legacy wire schema and 64-KiB limit while rejecting native copies above their explicit bound", () => {
    const storage = new MemoryStorage(); const legacy = save(storage);
    expect(legacy.raw).toBe(JSON.stringify({ schema: "motionwright.file-import-intent/1", request_id: REQUEST,
      created_at: 1_790_000_000_000, preparation: preparation(), input: { kind: "voice", path: input().path,
        name: null, media_type: null, label: input().label } }));
    const oversizedLegacy = legacy.raw + " ".repeat(65_537 - legacy.raw.length);
    storage.data.set(legacy.key, oversizedLegacy);
    expect(readFileImport(ID, storage).kind).toBe("invalid"); expect(storage.getItem(legacy.key)).toBe(oversizedLegacy);
    const nativeStorage = new MemoryStorage(); const saved = saveNative(nativeStorage);
    const oversizedNative = saved.raw + " ".repeat(2_097_153 - saved.raw.length);
    nativeStorage.data.set(saved.key, oversizedNative);
    expect(readFileImport(ID, nativeStorage).kind).toBe("invalid"); expect(nativeStorage.getItem(saved.key)).toBe(oversizedNative);
  });
});
