/** Local original-intent copies, never Native authority or a retry queue. */
import { sameValue } from "./creativeProduction";
import type { BlenderCycleIntent } from "./blenderCycleApi";
import type { BlenderLitIntent } from "./blenderLitApi";

export type BlenderOperation = {
  schema: "motionwright.blender.original-operation/1";
  kind: "cycle" | "lit";
  intent: BlenderCycleIntent | BlenderLitIntent;
  state: "prepared" | "sent" | "unconfirmed" | "completed" | "failed_known";
};
const PREFIX = "motionwright.blender-original.v1:";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HASH = /^[0-9a-f]{64}$/;
function object(value: unknown): value is Record<string, unknown> { return !!value && typeof value === "object" && !Array.isArray(value); }
function uuid(value: unknown): value is string { return typeof value === "string" && UUID.test(value) && value !== "00000000-0000-0000-0000-000000000000"; }
function parse(value: unknown): BlenderOperation {
  if (!object(value) || value.schema !== "motionwright.blender.original-operation/1"
      || typeof value.kind !== "string" || !["cycle", "lit"].includes(value.kind)
      || typeof value.state !== "string" || !["prepared", "sent", "unconfirmed", "completed", "failed_known"].includes(value.state)
      || Object.keys(value).length !== 4 || !object(value.intent) || !object(value.intent.source)) throw new Error("An original Blender operation copy is invalid.");
  const source = value.intent.source, intent = value.intent;
  if (![source.project_id, source.generation, source.scene_id].every(uuid)
      || !Number.isSafeInteger(source.revision) || Number(source.revision) < 0
      || typeof intent.source_sha256 !== "string" || !HASH.test(intent.source_sha256)
      || typeof intent.request_id !== "string") throw new Error("The original Blender source identity is invalid.");
  if (value.kind === "cycle") {
    if (Object.keys(source).length !== 5 || !uuid(source.capsule_id) || Object.keys(intent).length !== 4
        || !intent.request_id.startsWith("blender-ui-") || !uuid(intent.request_id.slice(11))
        || typeof intent.annotation_body !== "string" || !/^[\x20-\x7e]+(?:\n[\x20-\x7e]+)?$/.test(intent.annotation_body)
        || intent.annotation_body.split("\n").some(line => line.length > 32)) throw new Error("The original Blender cycle copy is invalid.");
  } else if (Object.keys(source).length !== 4 || Object.keys(intent).length !== 4
      || !intent.request_id.startsWith("blit-ui-") || !uuid(intent.request_id.slice(8))
      || typeof intent.fingerprint !== "string" || !HASH.test(intent.fingerprint)) throw new Error("The original Blender still copy is invalid.");
  return structuredClone(value) as unknown as BlenderOperation;
}
function storageKey(value: BlenderOperation) {
  return PREFIX + value.intent.source.project_id + ":" + value.kind + ":" + value.intent.request_id;
}
export function readBlenderOperations(projectId: string): BlenderOperation[] {
  if (!uuid(projectId)) throw new Error("The Blender operation project identity is invalid.");
  const storage = window.localStorage;
  if (storage.length > 8192) throw new Error("The local operation index exceeds its bounded reader.");
  const prefix = PREFIX + projectId + ":", rows: BlenderOperation[] = [];
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index);
    if (!key?.startsWith(prefix)) continue;
    if (rows.length >= 64) throw new Error("Read the retained Blender operation copies before adding more.");
    const raw = storage.getItem(key);
    if (!raw || raw.length > 8192) throw new Error("An original Blender operation copy cannot be read.");
    const row = parse(JSON.parse(raw));
    if (storageKey(row) !== key) throw new Error("A Blender operation copy belongs to another original identity.");
    rows.push(row);
  }
  return rows.sort((a, b) => a.intent.source.revision - b.intent.source.revision || a.intent.request_id.localeCompare(b.intent.request_id));
}
export function retainBlenderOperation(value: BlenderOperation): void {
  const row = parse(value), storage = window.localStorage, key = storageKey(row);
  const originals = readBlenderOperations(row.intent.source.project_id), before = storage.getItem(key);
  if (!before && originals.length >= 64) throw new Error("The local Blender operation index is full.");
  if (before) {
    const prior = parse(JSON.parse(before));
    if (prior.kind !== row.kind || !sameValue(prior.intent, row.intent)) throw new Error("An original Blender request cannot change its source.");
    if (prior.state !== row.state) {
      const allowed: Record<BlenderOperation["state"], BlenderOperation["state"][]> = {
        prepared: ["sent"], sent: ["unconfirmed", "completed", "failed_known"],
        unconfirmed: ["completed", "failed_known"], completed: [], failed_known: [],
      };
      if (!allowed[prior.state].includes(row.state)) throw new Error("The original Blender operation cannot return to a dispatchable state.");
    }
  } else if (row.state !== "prepared") throw new Error("Retain the original Blender intent before dispatch.");
  const encoded = JSON.stringify(row);
  if (encoded.length > 8192 || storage.getItem(key) !== before) throw new Error("The original Blender operation copy changed.");
  storage.setItem(key, encoded);
  if (storage.getItem(key) !== encoded) throw new Error("The original Blender operation could not be retained.");
}
