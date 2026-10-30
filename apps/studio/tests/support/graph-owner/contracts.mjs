import { readFileSync } from 'node:fs';
import { isDeepStrictEqual } from 'node:util';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { need, integer, canonical } from './custody.mjs';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
export const SPEC_SCHEMA = JSON.parse(readFileSync(path.join(ROOT, 'schemas/spec.r2.json')));
export const FINAL_SCHEMA = JSON.parse(readFileSync(path.join(ROOT, 'schemas/final.r2.json')));
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export const REQUEST_ID = /^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/(0|[1-9][0-9]*)$/;
export const ERROR_CODES = new Set(['SPEC_INVALID','SOURCE_MISMATCH','RUNTIME_INVALID','STORE_INVALID','BINARY_MISMATCH',
  'OWNER_START_FAILED','CLIENT_LIMIT','REQUEST_INVALID','CLIENT_START_FAILED','SERVICE_BARRIER_INVALID','OWNER_CLOSE_FAILED',
  'CUSTODY_FAILED','DEADLINE_EXCEEDED','CANCELLED','INTERNAL_ERROR']);
export function object(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
export function exact(value, fields, reason) {
  need(object(value) && Object.keys(value).sort().join('\0') === [...fields].sort().join('\0'), reason);
}
export function schema(value, rule, location = '$') {
  if (rule.anyOf) { let accepted = false; for (const alternative of rule.anyOf) { try { schema(value, alternative, location); accepted = true; break; } catch {} } need(accepted, 'SCHEMA_ANYOF:' + location); return; }
  if ('const' in rule) need(isDeepStrictEqual(value, rule.const), 'SCHEMA_CONST:' + location);
  if (rule.enum) need(rule.enum.some(item => isDeepStrictEqual(value, item)), 'SCHEMA_ENUM:' + location);
  if (rule.type === 'object') {
    need(object(value), 'SCHEMA_OBJECT:' + location);
    for (const key of rule.required ?? []) need(Object.hasOwn(value, key), 'SCHEMA_REQUIRED:' + location + '.' + key);
    for (const [key, item] of Object.entries(value)) {
      if (Object.hasOwn(rule.properties ?? {}, key)) schema(item, rule.properties[key], location + '.' + key);
      else need(rule.additionalProperties !== false, 'SCHEMA_EXTRA:' + location + '.' + key);
    }
  } else if (rule.type === 'array') {
    need(Array.isArray(value) && (rule.maxItems === undefined || value.length <= rule.maxItems), 'SCHEMA_ARRAY:' + location);
    value.forEach((item, index) => schema(item, rule.items, location + '[' + index + ']'));
  } else if (rule.type === 'string') {
    need(typeof value === 'string' && (rule.minLength === undefined || value.length >= rule.minLength)
      && (!rule.pattern || new RegExp(rule.pattern).test(value)), 'SCHEMA_STRING:' + location);
  } else if (rule.type === 'integer') need(integer(value, rule.minimum ?? -Number.MAX_SAFE_INTEGER, rule.maximum ?? Number.MAX_SAFE_INTEGER), 'SCHEMA_INTEGER:' + location);
  else if (rule.type === 'boolean') need(typeof value === 'boolean', 'SCHEMA_BOOLEAN:' + location);
  else if (rule.type === 'null') need(value === null, 'SCHEMA_NULL:' + location);
}

/** Duplicate-key rejecting bounded JSON reader, including nested objects. */
export function strictJSON(raw, maximum = 1048576) {
  const bytes = Buffer.isBuffer(raw) ? raw : Buffer.from(raw);
  need(bytes.length <= maximum, 'JSON_BYTE_BOUND');
  const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); let at = 0;
  const ws = () => { while (/[\x20\t\r\n]/.test(text[at] ?? '') && at < text.length) at++; };
  const string = () => {
    need(text[at] === '"', 'JSON_STRING'); const start = at++;
    while (at < text.length) { const char = text[at++]; if (char === '\\') at++; else if (char === '"') return JSON.parse(text.slice(start, at)); }
    throw new Error('JSON_UNTERMINATED_STRING');
  };
  const value = depth => {
    need(depth <= 64, 'JSON_DEPTH'); ws(); const char = text[at];
    if (char === '"') return string();
    if (char === '{') {
      at++; ws(); const result = {};
      if (text[at] === '}') { at++; return result; }
      while (true) { ws(); const key = string(); need(!Object.hasOwn(result, key), 'JSON_DUPLICATE_KEY'); ws(); need(text[at++] === ':', 'JSON_COLON');
        Object.defineProperty(result, key, { value: value(depth + 1), enumerable: true, configurable: true, writable: true }); ws();
        const separator = text[at++]; if (separator === '}') return result; need(separator === ',', 'JSON_OBJECT_SEPARATOR'); }
    }
    if (char === '[') { at++; ws(); const result = []; if (text[at] === ']') { at++; return result; }
      while (true) { result.push(value(depth + 1)); ws(); const separator = text[at++]; if (separator === ']') return result; need(separator === ',', 'JSON_ARRAY_SEPARATOR'); } }
    const match = /^(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/.exec(text.slice(at));
    need(match, 'JSON_TOKEN'); at += match[0].length; const result = JSON.parse(match[0]);
    need(typeof result !== 'number' || Number.isFinite(result), 'JSON_NONFINITE'); return result;
  };
  const result = value(0); ws(); need(at === text.length, 'JSON_TRAILING'); return result;
}
export function tuple(context) {
  exact(context, ['helper_id','launch_index','binary_path','database_path'], 'LAUNCH_CONTEXT_FIELDS');
  need(UUID.test(context.helper_id) && integer(context.launch_index, 0, 63), 'LAUNCH_CONTEXT_ID');
  canonical(context.binary_path); canonical(context.database_path); return context.helper_id + '/' + context.launch_index;
}
function time(value) { return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value) && Number.isFinite(Date.parse(value)); }

export function validateServiceClosure(value, expected, databasePath, expectedOwners = 1) {
  exact(value, ['schema','recorded_at','directory','database_path','status','phases','sealed_helper_ids','participants'], 'SERVICE_FIELDS');
  need(value.schema === 'motionwright.service-closure/1' && value.status === 'CLOSED' && time(value.recorded_at), 'SERVICE_STATUS');
  need(value.database_path === databasePath && value.directory === path.dirname(databasePath), 'SERVICE_STORE');
  exact(value.phases, ['preparation','oracle','owner_admission_seal'], 'SERVICE_PHASE_FIELDS');
  need(Array.isArray(value.phases.preparation) && value.phases.preparation.every(item => item === 'fulfilled')
    && value.phases.oracle === 'fulfilled' && isDeepStrictEqual(value.phases.owner_admission_seal, Array(expectedOwners).fill('fulfilled')), 'SERVICE_PHASES');
  const ids = [...expected.keys()].sort();
  need(isDeepStrictEqual(value.sealed_helper_ids, ids) && Array.isArray(value.participants) && value.participants.length === ids.length, 'SERVICE_ROSTER');
  const seen = new Set();
  for (const participant of value.participants) {
    exact(participant, ['helper_id','database_path','binary_path','launch_count','process_exits','process_streams','active_child_count','starting_count','closing_count','pending_count','taints','close_status'], 'SERVICE_PARTICIPANT_FIELDS');
    const want = expected.get(participant.helper_id);
    need(want && !seen.has(participant.helper_id), 'SERVICE_HELPER'); seen.add(participant.helper_id);
    need(participant.database_path === databasePath && participant.binary_path === want.binaryPath, 'SERVICE_BINARY_OR_STORE');
    need(integer(participant.launch_count, 1, 64) && participant.launch_count === want.launches.length
      && Array.isArray(participant.process_exits) && Array.isArray(participant.process_streams)
      && participant.process_exits.length === participant.launch_count && participant.process_streams.length === participant.launch_count, 'SERVICE_LAUNCH_COUNT');
    need(['active_child_count','starting_count','closing_count','pending_count'].every(key => integer(participant[key], 0, 0))
      && Array.isArray(participant.taints) && participant.taints.length === 0 && participant.close_status === 'fulfilled', 'SERVICE_TAINT_OR_PENDING');
    for (const exit of participant.process_exits) {
      exact(exit, ['process_id','code','signal','observed_at'], 'SERVICE_EXIT_FIELDS');
      need(integer(exit.process_id, 1) && integer(exit.code, 0, 0) && exit.signal === null && time(exit.observed_at), 'SERVICE_EXIT');
    }
    const exits = new Set();
    for (const [index, stream] of participant.process_streams.entries()) {
      exact(stream, ['launch_index','process_id','exit_index','stdout_ended','stderr_ended','jsonl_reader_closed'], 'SERVICE_STREAM_FIELDS');
      need(integer(stream.launch_index, index, index) && integer(stream.exit_index, 0, participant.launch_count - 1)
        && !exits.has(stream.exit_index), 'SERVICE_EXIT_BIJECTION'); exits.add(stream.exit_index);
      need(stream.process_id === participant.process_exits[stream.exit_index].process_id
        && stream.process_id === want.launches[index].pid
        && participant.process_exits[stream.exit_index].observed_at === want.launches[index].exit?.observedAt
        && participant.process_exits[stream.exit_index].code === want.launches[index].exit?.code
        && participant.process_exits[stream.exit_index].signal === want.launches[index].exit?.signal
        && ['stdout_ended','stderr_ended','jsonl_reader_closed'].every(key => stream[key] === true)
        && want.launches[index].rawComplete === true, 'SERVICE_RAW_OR_PID');
    }
  }
  return true;
}
export function same(actual, expected, reason) { need(isDeepStrictEqual(actual, expected), reason); }

/** Buffer only the unfinished line. A transport chunk may contain many legal
 * lines; the bound applies separately before each parse, not to their sum. */
export class BoundedJsonLines {
  constructor(receive, maximum = 65536) { this.receive = receive; this.maximum = maximum; this.pending = Buffer.alloc(0); this.ended = false; this.failed = false; }
  push(chunk) {
    try {
      need(!this.ended && !this.failed && Buffer.isBuffer(chunk), 'OWNER_PROTOCOL_RAW_DECODED_OR_CLOSED');
      let start = 0;
      while (start < chunk.length) {
        const newline = chunk.indexOf(10, start), end = newline === -1 ? chunk.length : newline;
        const part = chunk.subarray(start, end);
        need(this.pending.length + part.length <= this.maximum, 'OWNER_PROTOCOL_LINE_BOUND');
        const line = this.pending.length ? Buffer.concat([this.pending,part]) : part;
        if (newline === -1) { this.pending = Buffer.from(line); break; }
        this.pending = Buffer.alloc(0); this.receive(strictJSON(line,this.maximum)); start = newline + 1;
      }
    } catch(error) { this.failed = true; throw error; }
  }
  end() { need(!this.ended && !this.failed && this.pending.length === 0, 'OWNER_PROTOCOL_PARTIAL_LINE'); this.ended = true; }
}
