import type { Change, FontRight, MotionVerb, Project, VisualLanguage, VisualToken } from "./types";

// Local editing intent only. A recovered submission is neither permission nor
// proof of a commit, and this module never dispatches or retries a command.
export const VISUAL_DRAFT_FIELDS = ["name", "palette", "type_tokens", "motion_grammar", "anti_slop_rules", "fonts"] as const;
export type VisualDraftField = typeof VISUAL_DRAFT_FIELDS[number];
export const VISUAL_FIELD_LABELS: Record<VisualDraftField, string> = {
  name: "Visual language name", palette: "Palette", type_tokens: "Type tokens",
  motion_grammar: "Motion grammar", anti_slop_rules: "Anti-slop rules", fonts: "Fonts and rights",
};
export type VisualLanguageInput = Omit<VisualLanguage, "version" | "motion_grammar"> & {
  motion_grammar: (Omit<MotionVerb, "duration_ms"> & { duration_ms: string })[];
};
export interface VisualFieldDraft {
  raw: string; base: string; baseRevision: number; sequence: number; intent?: true;
}
export interface VisualSubmission {
  id: string; scope: string; revision: number; version: number;
  fields: Record<VisualDraftField, { raw: string; sequence: number; value: string }>;
}
export interface VisualLanguageDraft {
  schema: "motionwright.visual-draft/1"; scope: string;
  fields: Record<VisualDraftField, VisualFieldDraft>; awaiting: VisualSubmission | null;
}
export interface VisualDraftConflict {
  field: VisualDraftField; before: string; current: string; proposed: string; futureRevision: boolean;
}

const bytes = (value: string) => new TextEncoder().encode(value).length;
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const safeInteger = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const closed = (value: unknown, required: readonly string[], optional: readonly string[] = []): value is Record<string, unknown> =>
  record(value) && required.every(key => Object.hasOwn(value, key))
  && Object.keys(value).every(key => required.includes(key) || optional.includes(key));
const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
const reducedMotion = ["instant", "fade_only", "static_equivalent"] as const;
const rights = ["cleared", "restricted", "unknown"] as const;
function unicode(value: string): boolean {
  for (let index = 0; index < value.length; index++) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(++index);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return true;
}
function boundedText(value: string, limit: number, label: string, nonempty = true): void {
  if ((nonempty && !value.trim()) || bytes(value) > limit || !unicode(value)) {
    throw new Error(label + " must " + (nonempty ? "contain text and " : "") + "fit within " + limit.toLocaleString("en-US") + " UTF-8 bytes using valid Unicode.");
  }
}
function collection(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new Error("The visual-language collection is unreadable.");
  return value;
}
function textValue(value: unknown): string {
  if (typeof value !== "string") throw new Error("The visual-language input must be text.");
  return value;
}
function shapedField(field: VisualDraftField, value: unknown, raw: boolean): unknown {
  if (field === "name") return textValue(value);
  const items = collection(value);
  if (field === "anti_slop_rules") return items.map(textValue);
  if (field === "palette" || field === "type_tokens") return items.map(item => {
    if (!closed(item, ["name", "value"])) throw new Error("The visual token has unknown or missing fields.");
    return { name: textValue(item.name), value: textValue(item.value) };
  });
  if (field === "fonts") return items.map(item => {
    if (!closed(item, ["family", "source", "rights_status"]) || !rights.includes(item.rights_status as typeof rights[number])) {
      throw new Error("The font-rights input has unknown or missing fields.");
    }
    return { family: textValue(item.family), source: textValue(item.source), rights_status: item.rights_status };
  });
  return items.map(item => {
    if (!closed(item, ["name", "meaning", "duration_ms", "reduced_motion"])
      || !reducedMotion.includes(item.reduced_motion as typeof reducedMotion[number])
      || (raw ? typeof item.duration_ms !== "string" : !safeInteger(item.duration_ms))) {
      throw new Error("The motion-grammar input has invalid fields or duration.");
    }
    return { name: textValue(item.name), meaning: textValue(item.meaning),
      duration_ms: item.duration_ms, reduced_motion: item.reduced_motion };
  });
}
function validateSavedField(field: VisualDraftField, value: unknown, newInput = false): void {
  if (field === "name") { boundedText(value as string, 256, "Visual-language name"); return; }
  const items = value as unknown[];
  const limit = field === "palette" || field === "type_tokens" ? 256 : field === "anti_slop_rules" ? 512 : 128;
  if (items.length > limit) throw new Error(VISUAL_FIELD_LABELS[field] + " must contain at most " + limit + " entries.");
  if (field === "palette" || field === "type_tokens") for (const token of items as VisualToken[]) {
    boundedText(token.name, 128, "Token name"); boundedText(token.value, 1024, "Token value");
  }
  else if (field === "motion_grammar") for (const verb of items as MotionVerb[]) {
    boundedText(verb.name, 128, "Motion verb"); boundedText(verb.meaning, 1000, "Motion meaning");
    if (!safeInteger(verb.duration_ms) || verb.duration_ms > 120_000) throw new Error("Motion duration must be a whole number from 0 to 120,000 milliseconds.");
  }
  else if (field === "anti_slop_rules") for (const rule of items as string[]) {
    // Existing canonical/imported rules may be blank. New editor input follows
    // the current UI's nonblank rule contract, without rewriting clean groups.
    boundedText(rule, 1000, "Anti-slop rule", newInput);
  }
  else for (const font of items as FontRight[]) {
    boundedText(font.family, 256, "Font family"); boundedText(font.source, 2048, "Font source", false);
  }
}
function encode(field: VisualDraftField, value: unknown): string {
  return field === "name" ? value as string : JSON.stringify(value);
}
function parseExact(raw: string): unknown {
  const value: unknown = JSON.parse(raw);
  // These are records authored by this editor. In particular, parsing a
  // duplicate key must not silently turn an unconfirmed snapshot into a draft.
  if (JSON.stringify(value) !== raw) throw new Error("The visual recovery JSON is ambiguous or not an exact editor copy.");
  return value;
}
function inputField(field: VisualDraftField, raw: string): unknown {
  return shapedField(field, field === "name" ? raw : parseExact(raw), true);
}
function savedField(field: VisualDraftField, encoded: string): unknown {
  const value = shapedField(field, field === "name" ? encoded : parseExact(encoded), false);
  validateSavedField(field, value);
  return value;
}
function rawOf(field: VisualDraftField, value: unknown): string {
  return encode(field, field === "motion_grammar"
    ? (value as MotionVerb[]).map(verb => ({ ...verb, duration_ms: String(verb.duration_ms) })) : value);
}
function rawBase(field: VisualDraftField, base: string): string { return rawOf(field, savedField(field, base)); }
function canonicalField(language: VisualLanguage, field: VisualDraftField): string {
  const value = shapedField(field, language[field], false);
  validateSavedField(field, value);
  return encode(field, value);
}
function duration(raw: string): number {
  if (!decimal.test(raw.trim())) throw new Error("Enter a complete decimal motion duration; an empty input is not zero.");
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < 0 || value > 120_000) throw new Error("Motion duration must be a whole number from 0 to 120,000 milliseconds.");
  return value;
}
function targetField(field: VisualDraftField, raw: string): string {
  const input = inputField(field, raw);
  let value: unknown;
  if (field === "name") value = (input as string).trim();
  else if (field === "palette" || field === "type_tokens") value = (input as VisualToken[]).map(token => ({ name: token.name.trim(), value: token.value.trim() }));
  else if (field === "motion_grammar") value = (input as VisualLanguageInput["motion_grammar"]).map(verb => ({
    name: verb.name.trim(), meaning: verb.meaning.trim(), duration_ms: duration(verb.duration_ms), reduced_motion: verb.reduced_motion,
  }));
  else if (field === "anti_slop_rules") value = (input as string[]).map(rule => rule.trim());
  else value = (input as FontRight[]).map(font => ({ family: font.family.trim(), source: font.source.trim(), rights_status: font.rights_status }));
  validateSavedField(field, value, true);
  return encode(field, value);
}
function proposed(field: VisualDraftField, raw: string): string | null {
  try { return targetField(field, raw); } catch { return null; }
}
function parsedScope(scope: string): [string, string, string] | null {
  try {
    const value: unknown = JSON.parse(scope);
    return scope.length <= 2048 && Array.isArray(value) && value.length === 3
      && value.every(part => typeof part === "string" && part.length > 0 && part.length <= 256)
      && JSON.stringify(value) === scope ? value as [string, string, string] : null;
  } catch { return null; }
}
export const visualDraftScope = (project: Project): string => JSON.stringify([project.id, project.generation, project.active_branch]);
function currentLanguage(draft: VisualLanguageDraft, project: Project): VisualLanguage {
  if (!parsedScope(draft.scope) || draft.scope !== visualDraftScope(project)) throw new Error("This visual-language draft belongs to another project, generation or branch.");
  if (!safeInteger(project.revision) || !safeInteger(project.visual_language.version)
    || project.visual_language.version < 1 || project.visual_language.version > 1000) throw new Error("The saved visual-language revision or version is invalid.");
  return project.visual_language;
}
export function visualFieldHasInput(field: VisualDraftField, input: VisualFieldDraft): boolean {
  return input.intent === true || input.raw !== rawBase(field, input.base);
}
export const visualDraftHasWork = (draft: VisualLanguageDraft): boolean => draft.awaiting !== null
  || VISUAL_DRAFT_FIELDS.some(field => visualFieldHasInput(field, draft.fields[field]));
export function createVisualDraft(project: Project): VisualLanguageDraft {
  const draft: VisualLanguageDraft = { schema: "motionwright.visual-draft/1", scope: visualDraftScope(project),
    fields: {} as VisualLanguageDraft["fields"], awaiting: null };
  const language = currentLanguage(draft, project);
  for (const field of VISUAL_DRAFT_FIELDS) {
    const base = canonicalField(language, field);
    draft.fields[field] = { raw: rawBase(field, base), base, baseRevision: project.revision, sequence: 0 };
  }
  return draft;
}
export function visualDraftInput(draft: VisualLanguageDraft): VisualLanguageInput {
  return Object.fromEntries(VISUAL_DRAFT_FIELDS.map(field => [field, inputField(field, draft.fields[field].raw)])) as VisualLanguageInput;
}
function nextSequence(sequence: number): number {
  if (!safeInteger(sequence) || sequence === Number.MAX_SAFE_INTEGER) throw new Error("The visual input sequence is exhausted. Keep this recovery copy and start a new draft.");
  return sequence + 1;
}
export function editVisualField<K extends VisualDraftField>(draft: VisualLanguageDraft, field: K, input: VisualLanguageInput[K]): VisualLanguageDraft {
  if (!(VISUAL_DRAFT_FIELDS as readonly string[]).includes(field)) throw new Error("Unknown visual-language field.");
  const current = draft.fields[field], raw = encode(field, shapedField(field, input, true));
  return { ...draft, fields: { ...draft.fields, [field]: { ...current, raw, sequence: nextSequence(current.sequence),
    ...(draft.awaiting ? { intent: true as const } : {}) } } };
}
export function visualDraftConflicts(draft: VisualLanguageDraft, project: Project): VisualDraftConflict[] {
  const language = currentLanguage(draft, project);
  return VISUAL_DRAFT_FIELDS.flatMap(field => {
    const input = draft.fields[field], current = canonicalField(language, field), futureRevision = input.baseRevision > project.revision;
    return visualFieldHasInput(field, input) && (futureRevision || (current !== input.base && current !== proposed(field, input.raw)))
      ? [{ field, before: input.base, current, proposed: input.raw, futureRevision }] : [];
  });
}
export function visualSubmissionMatches(project: Project, draft: VisualLanguageDraft, submission: VisualSubmission): boolean {
  if (draft.scope !== submission.scope || project.revision !== submission.revision + 1
    || project.visual_language.version !== submission.version) return false;
  try {
    const language = currentLanguage(draft, project);
    return VISUAL_DRAFT_FIELDS.every(field => canonicalField(language, field) === submission.fields[field]?.value);
  } catch { return false; }
}
export function reconcileVisualDraft(draft: VisualLanguageDraft, project: Project): VisualLanguageDraft {
  const language = currentLanguage(draft, project), fields = { ...draft.fields };
  let awaiting = draft.awaiting;
  if (awaiting && visualSubmissionMatches(project, draft, awaiting)) {
    for (const field of VISUAL_DRAFT_FIELDS) {
      const input = fields[field], sent = awaiting.fields[field], base = canonicalField(language, field);
      const unchanged = input.sequence === sent.sequence && input.raw === sent.raw;
      fields[field] = { raw: unchanged ? rawBase(field, base) : input.raw, base, baseRevision: project.revision,
        sequence: input.sequence, ...(!unchanged ? { intent: true as const } : {}) };
    }
    awaiting = null;
  } else if (awaiting && project.revision > awaiting.revision) {
    // A different revision is not the acknowledgement of this snapshot. Keep
    // its input and original bases for explicit conflict review or a new save.
    awaiting = null;
  }
  for (const field of VISUAL_DRAFT_FIELDS) if (!visualFieldHasInput(field, fields[field])) {
    const base = canonicalField(language, field);
    fields[field] = { ...fields[field], raw: rawBase(field, base), base, baseRevision: project.revision };
  }
  return { ...draft, fields, awaiting };
}
export function resolveVisualField(draft: VisualLanguageDraft, project: Project, field: VisualDraftField, choice: "saved" | "draft"): VisualLanguageDraft {
  const base = canonicalField(currentLanguage(draft, project), field), input = draft.fields[field];
  return { ...draft, fields: { ...draft.fields, [field]: { raw: choice === "saved" ? rawBase(field, base) : input.raw,
    base, baseRevision: project.revision, sequence: nextSequence(input.sequence),
    ...(choice === "draft" && input.intent ? { intent: true as const } : {}) } } };
}
export function releaseVisualSubmission(draft: VisualLanguageDraft, id: string): VisualLanguageDraft {
  return draft.awaiting?.id === id ? { ...draft, awaiting: null } : draft;
}
export function visualDraftIssue(draft: VisualLanguageDraft, project: Project): string | null {
  const language = currentLanguage(draft, project);
  if (draft.awaiting) return "Review the unconfirmed visual-language submission before saving again.";
  if (visualDraftConflicts(draft, project).length) return "Resolve the overlapping visual-language groups before saving.";
  let changed = false;
  for (const field of VISUAL_DRAFT_FIELDS) if (visualFieldHasInput(field, draft.fields[field])) {
    try {
      const target = targetField(field, draft.fields[field].raw);
      changed = changed || target !== canonicalField(language, field);
    }
    catch (reason) { return VISUAL_FIELD_LABELS[field] + ": " + (reason instanceof Error ? reason.message : String(reason)); }
  }
  if (!changed) return "These values already match the saved visual language.";
  if (project.locks.some(lock => lock.resource === "project:" + project.id && lock.kind === "style")) return "A saved style lock protects the visual language. Your draft is kept.";
  if (language.version >= 1000) return "The visual language has reached version 1,000. Your draft is kept.";
  if (project.revision === Number.MAX_SAFE_INTEGER) return "The project revision cannot advance safely. Your draft is kept.";
  return null;
}
export function prepareVisualSubmission(draft: VisualLanguageDraft, project: Project, id: string): {
  draft: VisualLanguageDraft; submission: VisualSubmission; change: Extract<Change, { type: "set_visual_language" }>;
} {
  if (typeof id !== "string" || !id || id.length > 128) throw new Error("A visual-language submission requires a new intent identity.");
  const current = reconcileVisualDraft(draft, project), issue = visualDraftIssue(current, project);
  if (issue) throw new Error(issue);
  const language = currentLanguage(current, project), fields = { ...current.fields };
  const submission: VisualSubmission = { id, scope: current.scope, revision: project.revision,
    version: language.version + 1, fields: {} as VisualSubmission["fields"] };
  const values: Partial<Omit<VisualLanguage, "version">> = {};
  for (const field of VISUAL_DRAFT_FIELDS) {
    const input = fields[field], canonical = canonicalField(language, field);
    const value = visualFieldHasInput(field, input) ? targetField(field, input.raw) : canonical;
    submission.fields[field] = { raw: input.raw, sequence: input.sequence, value };
    Object.assign(values, { [field]: savedField(field, value) });
    if (value !== canonical) fields[field] = { ...input, intent: true };
  }
  return { draft: { ...current, fields, awaiting: structuredClone(submission) }, submission,
    change: { type: "set_visual_language", visual_language: { version: submission.version, ...values } as VisualLanguage } };
}

export const VISUAL_DRAFT_PREFIX = "motionwright.visual-draft.v1:";
export const VISUAL_SESSION_PREFIX = "motionwright.visual-session.v1:";
export const VISUAL_COPY_BYTES = 8 * 1024 * 1024;
export const VISUAL_TOTAL_BYTES = 16 * 1024 * 1024;
export const VISUAL_COPY_LIMIT = 128;
export const VISUAL_KEY_LIMIT = 4096;
export type VisualDraftStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
export interface VisualRecovery {
  schema: "motionwright.visual-recovery/1"; scope: string; writerId: string; updatedAt: number; draft: VisualLanguageDraft | null;
}
export interface VisualRecoveryEntry { key: string; raw: string; value: VisualRecovery }
export interface VisualUnreadableRecovery { key: string; raw: string | null; writerId: string }
export const visualRecoveryKey = (scope: string, writerId: string): string => VISUAL_DRAFT_PREFIX + JSON.stringify([scope, writerId]);
function identity(key: string): [string, string] | null {
  try {
    const pair: unknown = JSON.parse(key.slice(VISUAL_DRAFT_PREFIX.length));
    return Array.isArray(pair) && pair.length === 2 && typeof pair[0] === "string" && parsedScope(pair[0])
      && typeof pair[1] === "string" && pair[1].length > 0 && pair[1].length <= 128
      && visualRecoveryKey(pair[0], pair[1]) === key ? pair as [string, string] : null;
  } catch { return null; }
}
interface VisualStorageKeys { count: number; keys: string[]; limited: boolean }
function storageKeys(storage: VisualDraftStorage): VisualStorageKeys {
  const count = storage.length, keys: string[] = [], seen = new Set<string>();
  if (!safeInteger(count)) return { count, keys, limited: true };
  for (let index = 0; index < Math.min(count, VISUAL_KEY_LIMIT); index++) {
    const key = storage.key(index);
    if (typeof key !== "string" || seen.has(key)) return { count, keys: [], limited: true };
    keys.push(key); seen.add(key);
  }
  return { count, keys, limited: count > VISUAL_KEY_LIMIT || storage.length !== count };
}
function sameStorageSnapshot(storage: VisualDraftStorage, snapshot: VisualStorageKeys, observed: { key: string; raw: string | null }[]): boolean {
  if (snapshot.limited || storage.length !== snapshot.count) return false;
  for (const item of observed) if (storage.getItem(item.key) !== item.raw) return false;
  for (let index = 0; index < snapshot.keys.length; index++) if (storage.key(index) !== snapshot.keys[index]) return false;
  return storage.length === snapshot.count;
}
export function decodeVisualRecovery(raw: string, scope: string, writerId: string): VisualRecovery {
  if (bytes(raw) > VISUAL_COPY_BYTES) throw new Error("This visual-language recovery copy exceeds 8 MiB.");
  const value = parseExact(raw);
  if (!parsedScope(scope) || !closed(value, ["schema", "scope", "writerId", "updatedAt", "draft"])
    || value.schema !== "motionwright.visual-recovery/1" || value.scope !== scope || value.writerId !== writerId
    || !writerId || writerId.length > 128 || !safeInteger(value.updatedAt)) throw new Error("The visual recovery copy has an invalid identity or version.");
  let draft: VisualLanguageDraft | null = null;
  if (value.draft !== null) {
    const saved = value.draft;
    if (!closed(saved, ["schema", "scope", "fields", "awaiting"]) || saved.schema !== "motionwright.visual-draft/1"
      || saved.scope !== scope || !closed(saved.fields, VISUAL_DRAFT_FIELDS)) throw new Error("The visual draft has an invalid project scope or fields.");
    const fields = {} as VisualLanguageDraft["fields"];
    for (const field of VISUAL_DRAFT_FIELDS) {
      const input = saved.fields[field];
      if (!closed(input, ["raw", "base", "baseRevision", "sequence"], ["intent"])
        || typeof input.raw !== "string" || typeof input.base !== "string" || !safeInteger(input.baseRevision)
        || !safeInteger(input.sequence) || (input.intent !== undefined && input.intent !== true)) throw new Error("The visual recovery copy contains an invalid field.");
      fields[field] = { raw: encode(field, inputField(field, input.raw)), base: encode(field, savedField(field, input.base)),
        baseRevision: input.baseRevision, sequence: input.sequence, ...(input.intent ? { intent: true as const } : {}) };
    }
    let awaiting: VisualSubmission | null = null;
    if (saved.awaiting !== null) {
      const sent = saved.awaiting;
      if (!closed(sent, ["id", "scope", "revision", "version", "fields"]) || typeof sent.id !== "string" || !sent.id || sent.id.length > 128
        || sent.scope !== scope || !safeInteger(sent.revision) || sent.revision === Number.MAX_SAFE_INTEGER
        || !safeInteger(sent.version) || sent.version < 2 || sent.version > 1000 || !closed(sent.fields, VISUAL_DRAFT_FIELDS)) {
        throw new Error("The visual submission snapshot is unreadable.");
      }
      awaiting = { id: sent.id, scope, revision: sent.revision, version: sent.version, fields: {} as VisualSubmission["fields"] };
      for (const field of VISUAL_DRAFT_FIELDS) {
        const input = sent.fields[field];
        if (!closed(input, ["raw", "sequence", "value"]) || typeof input.raw !== "string" || typeof input.value !== "string"
          || !safeInteger(input.sequence) || input.sequence > fields[field].sequence) throw new Error("The visual submission contains an invalid field snapshot.");
        const rawInput = encode(field, inputField(field, input.raw)), target = encode(field, savedField(field, input.value));
        if (rawInput !== rawBase(field, target) && proposed(field, rawInput) !== target) throw new Error("The visual submission does not match its input snapshot.");
        awaiting.fields[field] = { raw: rawInput, value: target, sequence: input.sequence };
      }
    }
    draft = { schema: "motionwright.visual-draft/1", scope, fields, awaiting };
  }
  return { schema: "motionwright.visual-recovery/1", scope, writerId, updatedAt: value.updatedAt, draft };
}
export function readVisualRecoveries(storage: VisualDraftStorage, scope: string): {
  entries: VisualRecoveryEntry[]; unreadable: VisualUnreadableRecovery[]; limited: boolean;
} {
  if (!parsedScope(scope)) throw new Error("The visual recovery scope is invalid.");
  const entries: VisualRecoveryEntry[] = [], unreadable: VisualUnreadableRecovery[] = [];
  const snapshot = storageKeys(storage);
  if (snapshot.limited) return { entries, unreadable, limited: true };
  const observed: { key: string; raw: string | null }[] = [];
  let limited = false, total = 0;
  for (const [index, key] of snapshot.keys.entries()) {
    if (!key.startsWith(VISUAL_DRAFT_PREFIX)) continue;
    const pair = identity(key);
    if (pair && pair[0] !== scope) continue;
    if (entries.length + unreadable.length >= VISUAL_COPY_LIMIT) { limited = true; break; }
    let raw: string | null = null;
    try {
      raw = storage.getItem(key);
      if (raw === null) { limited = true; break; }
      total += bytes(key) + bytes(raw);
      if (total > VISUAL_TOTAL_BYTES) {
        unreadable.push({ key, raw: null, writerId: pair?.[1] ?? "unidentified-" + index }); limited = true; break;
      }
      observed.push({ key, raw });
      if (!pair) throw new Error("Unidentified recovery copy");
      entries.push({ key, raw, value: decodeVisualRecovery(raw, scope, pair[1]) });
    } catch { unreadable.push({ key, raw, writerId: pair?.[1] ?? "unidentified-" + index }); }
  }
  try { if (!limited && !sameStorageSnapshot(storage, snapshot, observed)) limited = true; }
  catch { limited = true; }
  // A partial or changing index cannot supply an automatically adopted writer.
  // The UI may show the read error and refresh; no stored bytes were changed.
  if (limited) entries.length = 0;
  entries.sort((a, b) => b.value.updatedAt - a.value.updatedAt || a.key.localeCompare(b.key));
  return { entries, unreadable, limited };
}
export function writeVisualRecovery(storage: VisualDraftStorage, value: VisualRecovery, expectedRaw: string | null): string {
  const key = visualRecoveryKey(value.scope, value.writerId), previous = storage.getItem(key);
  if (previous !== expectedRaw) throw new Error("This visual recovery copy changed in another window. Its contents were kept.");
  const validated = decodeVisualRecovery(JSON.stringify(value), value.scope, value.writerId);
  if (previous !== null && decodeVisualRecovery(previous, value.scope, value.writerId).draft === null) {
    if (validated.draft !== null) throw new Error("A clean visual checkpoint is immutable. Start a new copy for new input.");
    return previous;
  }
  const snapshot = storageKeys(storage);
  if (snapshot.limited) throw new Error("Visual recovery could not read a complete, unchanged storage index within 4,096 keys. No copies were removed.");
  const stored: { key: string; raw: string; value: VisualRecovery | null }[] = [];
  let total = 0;
  for (const existing of snapshot.keys) {
    if (!existing.startsWith(VISUAL_DRAFT_PREFIX)) continue;
    if (stored.length >= VISUAL_COPY_LIMIT) throw new Error("Visual recovery is full (128 copies). No copies were removed.");
    const original = storage.getItem(existing);
    if (original === null) throw new Error("Visual recovery changed while its storage index was being read. Original copies were kept.");
    total += bytes(existing) + bytes(original);
    if (total > VISUAL_TOTAL_BYTES) throw new Error("Visual recovery exceeds its 16 MiB reading budget. Original copies were kept.");
    const pair = identity(existing); let decoded: VisualRecovery | null = null;
    try { if (pair) decoded = decodeVisualRecovery(original, pair[0], pair[1]); } catch { /* Preserve unreadable bytes. */ }
    stored.push({ key: existing, raw: original, value: decoded });
  }
  const latest = stored.reduce((at, item) => item.value?.scope === value.scope ? Math.max(at, item.value.updatedAt) : at, -1);
  const updatedAt = Math.max(validated.updatedAt, latest + 1);
  if (!safeInteger(updatedAt)) throw new Error("The visual recovery clock cannot advance safely. Original copies were kept.");
  const raw = JSON.stringify(decodeVisualRecovery(JSON.stringify({ ...validated, updatedAt }), value.scope, value.writerId));
  // Only superseded clean checkpoints may be collected, and only after the new
  // bytes have been written and read back. A quota error removes nothing.
  const clean = stored.filter(item => item.key !== key && item.value?.scope === value.scope && item.value.draft === null);
  const remaining = stored.filter(item => item.key !== key);
  if (remaining.length + 1 > VISUAL_COPY_LIMIT || bytes(key) + bytes(raw)
    + remaining.reduce((sum, item) => sum + bytes(item.key) + bytes(item.raw), 0) > VISUAL_TOTAL_BYTES) {
    throw new Error("Visual recovery is full (128 copies / 16 MiB). Inputs and unreadable copies were kept.");
  }
  // This is a bounded comparison before a synchronous write, not an atomic
  // cross-window storage transaction. Each writer also has a separate key.
  if (!sameStorageSnapshot(storage, snapshot, stored)) throw new Error("Visual recovery changed in another window while its copies were being read. Original copies were kept.");
  if (storage.getItem(key) !== previous) throw new Error("This visual recovery copy changed in another window. Its contents were kept.");
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The latest visual recovery write could not be verified.");
  for (const item of clean) {
    try { if (storage.getItem(item.key) === item.raw) storage.removeItem(item.key); }
    catch { /* Optional clean-copy collection cannot unconfirm verified input. */ }
  }
  return raw;
}
export function discardVisualRecovery(storage: VisualDraftStorage, key: string, expectedRaw: string): void {
  if (!key.startsWith(VISUAL_DRAFT_PREFIX) || storage.getItem(key) !== expectedRaw) {
    throw new Error("This visual recovery copy changed after it was shown. Refresh the copies before discarding it.");
  }
  storage.removeItem(key);
  if (storage.getItem(key) !== null) throw new Error("The visual recovery copy could not be removed. Refresh the copies before trying again.");
}
