import { canonicalRevision, projectionUuid } from "./projectMutation";
import { parseBeatTime } from "./sceneBeatTiming";
import { parseEditorSeekTime, type TimecodeMode } from "./timecode";
import { timelineSelectionScope } from "./timelineSelection";
import type { Marker, MarkerChange, Project, RationalTime } from "./types";

export interface MarkerAttempt {
  requestId: string;
  revision: number;
  change: MarkerChange;
}

const root = "motionwright.marker-attempt.v1:";
const pendingPrefix = (project: Project) => root + "pending:" + timelineSelectionScope(project) + ":";
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const sameTime = (left: RationalTime, right: RationalTime) => left.num === right.num && left.den === right.den;
const encodeLength = (text: string) => new TextEncoder().encode(text).length;

export const sameMarker = (left: Marker, right: Marker) =>
  left.id === right.id && left.label === right.label && sameTime(left.at, right.at);

export function buildMarkerEdit(
  expected: Marker, rawLabel: string, position: string, rate: RationalTime, mode: TimecodeMode,
): MarkerChange {
  const label = rawLabel === expected.label ? expected.label : rawLabel.trim();
  if (label !== expected.label && (!label || encodeLength(label) > 160))
    throw new Error("Enter a marker name of 1 to 160 UTF-8 bytes.");
  const at = position.includes("/") ? parseBeatTime(position) : parseEditorSeekTime(position, rate, mode);
  if (BigInt(at.num) < 0n) throw new Error("Enter a marker position at or after the start of the timeline.");
  if (label === expected.label && sameTime(at, expected.at))
    throw new Error("Change the marker name or position before saving.");
  return { type: "edit_marker", expected, at, label };
}

function validTime(value: unknown): value is RationalTime {
  if (!record(value) || Object.keys(value).length !== 2 || !canonicalRevision(value.num)
      || !canonicalRevision(value.den)) return false;
  try {
    const canonical = parseBeatTime(value.num + "/" + value.den);
    return canonical.num === value.num && canonical.den === value.den;
  } catch {
    return false;
  }
}

function decodeAttempt(raw: string, requestId: string): MarkerAttempt {
  if (raw.length > 4096) throw new Error("The retained marker attempt exceeds its storage limit.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || Object.keys(value).length !== 3 || !projectionUuid(requestId) || value.requestId !== requestId
      || !Number.isSafeInteger(value.revision) || Number(value.revision) < 0 || !record(value.change)) {
    throw new Error("The retained marker request has an invalid identity.");
  }
  const { change } = value;
  const marker = change.expected;
  if (!record(marker) || Object.keys(marker).length !== 3 || !projectionUuid(marker.id)
      || typeof marker.label !== "string" || encodeLength(marker.label) > 160 || !validTime(marker.at)) {
    throw new Error("The retained marker preimage is invalid.");
  }
  if (change.type === "remove_marker" && Object.keys(change).length === 2) return value as unknown as MarkerAttempt;
  if (change.type === "edit_marker" && Object.keys(change).length === 4 && typeof change.label === "string"
      && encodeLength(change.label) <= 160 && validTime(change.at)) return value as unknown as MarkerAttempt;
  throw new Error("The retained marker change is invalid.");
}

export function readMarkerAttempts(storage: Storage, project: Project): MarkerAttempt[] {
  if (storage.length > 4096) throw new Error("Marker recovery storage exceeds its lookup limit.");
  const prefix = pendingPrefix(project);
  const attempts: MarkerAttempt[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (!key?.startsWith(prefix)) continue;
    const raw = storage.getItem(key);
    if (raw === null) continue;
    if (attempts.length === 32) throw new Error("Resolve a retained marker attempt before starting another.");
    attempts.push(decodeAttempt(raw, key.slice(prefix.length)));
  }
  return attempts.sort((a, b) => a.revision - b.revision || a.requestId.localeCompare(b.requestId));
}

export function retainMarkerAttempt(storage: Storage, project: Project, attempt: MarkerAttempt): void {
  const raw = JSON.stringify(attempt);
  decodeAttempt(raw, attempt.requestId);
  const key = pendingPrefix(project) + attempt.requestId;
  if (storage.getItem(key) !== null) throw new Error("The marker request identity already exists.");
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The marker request could not be retained.");
}

export function finishMarkerAttempt(storage: Storage, project: Project, attempt: MarkerAttempt, keepOriginal = false): void {
  const key = pendingPrefix(project) + attempt.requestId;
  const raw = JSON.stringify(attempt);
  const stored = storage.getItem(key);
  if (stored === null && !keepOriginal) return;
  if (stored !== raw) throw new Error("The retained marker record changed. Review its original before continuing.");
  if (keepOriginal) {
    const archived = root + "retained:" + timelineSelectionScope(project) + ":" + attempt.requestId;
    const existing = storage.getItem(archived);
    if (existing !== null && existing !== raw) throw new Error("The retained marker archive changed.");
    storage.setItem(archived, raw);
    if (storage.getItem(archived) !== raw) throw new Error("The original marker request could not be kept.");
  }
  storage.removeItem(key);
}
