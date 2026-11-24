import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { readBlenderOperations, retainBlenderOperation, type BlenderOperation } from "./blenderOperationRetention";

const project = "11111111-1111-4111-8111-111111111111";
const generation = "22222222-2222-4222-8222-222222222222";
const scene = "33333333-3333-4333-8333-333333333333";
const capsule = "44444444-4444-4444-8444-444444444444";
const request = "55555555-5555-4555-8555-555555555555";
let entries: Map<string, string>;
function original(kind: "lit" | "cycle" = "cycle"): BlenderOperation {
  const source = { project_id: project, generation, scene_id: scene, revision: 7 };
  const base = { schema: "motionwright.blender.original-operation/1" as const, state: "prepared" as const };
  return kind === "cycle" ? { ...base, kind, intent: { source: { ...source, capsule_id: capsule },
    source_sha256: "a".repeat(64), request_id: "blender-ui-" + request, annotation_body: "EDITED" } }
    : { ...base, kind, intent: { source, source_sha256: "b".repeat(64),
      fingerprint: "c".repeat(64), request_id: "blit-ui-" + request } };
}
beforeEach(() => {
  entries = new Map();
  vi.stubGlobal("window", { localStorage: {
    get length() { return entries.size; }, key: (i: number) => [...entries.keys()][i] ?? null,
    getItem: (k: string) => entries.get(k) ?? null, setItem: (k: string, v: string) => entries.set(k,v),
  } });
});
afterEach(() => vi.unstubAllGlobals());

it.each(["cycle", "lit"] as const)("keeps original %s identity across loss and readback without replay", kind => {
  const value = original(kind);
  retainBlenderOperation(value);
  retainBlenderOperation({ ...value, state: "sent" });
  retainBlenderOperation({ ...value, state: "unconfirmed" });
  expect(readBlenderOperations(project)).toEqual([{ ...value, state: "unconfirmed" }]);
  expect(() => retainBlenderOperation({ ...value, state: "sent" })).toThrow();
  expect(() => retainBlenderOperation({ ...value, intent: { ...value.intent, source_sha256: "d".repeat(64) } })).toThrow();
  retainBlenderOperation({ ...value, state: "completed" });
  expect(() => retainBlenderOperation({ ...value, state: "unconfirmed" })).toThrow();
  expect(readBlenderOperations(project)).toEqual([{ ...value, state: "completed" }]);
});
it("requires a durable preparation before dispatch and refuses corrupt enum shapes", () => {
  const value = original();
  expect(() => retainBlenderOperation({ ...value, state: "sent" })).toThrow();
  retainBlenderOperation(value);
  const key = [...entries.keys()][0];
  entries.set(key, JSON.stringify({ ...value, state: ["prepared"] }));
  expect(() => readBlenderOperations(project)).toThrow();
  expect(entries.get(key)).toContain('["prepared"]');
});
it("does not inherit authority or accept an operation under another key", () => {
  const value = original();
  retainBlenderOperation(value);
  const key = [...entries.keys()][0];
  entries.set(key, JSON.stringify({ ...value, intent: { ...value.intent, effect_grant: request } }));
  expect(() => readBlenderOperations(project)).toThrow();
  entries.set(key, JSON.stringify({ ...value, intent: { ...value.intent, request_id: "blender-ui-" + capsule } }));
  expect(() => readBlenderOperations(project)).toThrow();
});
