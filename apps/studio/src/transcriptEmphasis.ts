import type { TranscriptEmphasis } from "./types";

export const MAX_TRANSCRIPT_EMPHASIS = 8;
const whitespace = (value: string) => /^\p{White_Space}$/u.test(value);
const invalid = () => new Error("Caption emphasis must use at most eight ordered whole-word ranges matching the current transcript. Clear or select emphasis again after a text change.");
export function validateTranscriptEmphasis(text: string, value: unknown): TranscriptEmphasis[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > MAX_TRANSCRIPT_EMPHASIS
    || (value.length > 0 && /\p{Cc}/u.test(text.replaceAll("\n", "")))) throw invalid();
  const chars = Array.from(text);
  let previous = 0;
  return value.map(item => {
    if (!item || typeof item !== "object" || Object.keys(item).sort().join() !== "end,start,text") throw invalid();
    const { start, end, text: fragment } = item as TranscriptEmphasis;
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < previous || start < 0
      || start >= end || end > chars.length || end > 65535 || typeof fragment !== "string"
      || chars.slice(start, end).join("") !== fragment || whitespace(chars[start]) || whitespace(chars[end - 1])
      || (start > 0 && !whitespace(chars[start - 1])) || (end < chars.length && !whitespace(chars[end]))) throw invalid();
    previous = end;
    return { start, end, text: fragment };
  });
}
export function decodeTranscriptEmphasis(raw: string, text: string): TranscriptEmphasis[] {
  let value: unknown;
  try { value = raw.trim() ? JSON.parse(raw) : []; } catch { throw invalid(); }
  return validateTranscriptEmphasis(text, value);
}
export const encodeTranscriptEmphasis = (ranges: readonly TranscriptEmphasis[]) => ranges.length ? JSON.stringify(ranges) : "";
export function sameTranscriptEmphasis(text: string, left: unknown, right: unknown): boolean {
  try { return JSON.stringify(validateTranscriptEmphasis(text, left)) === JSON.stringify(validateTranscriptEmphasis(text, right)); }
  catch { return false; }
}
/** Retain source-bound ranges when the existing editor trims outer whitespace. */
export function trimTranscriptEmphasis(text: string, ranges: unknown): TranscriptEmphasis[] {
  const valid = validateTranscriptEmphasis(text, ranges);
  const leading = Array.from(text).length - Array.from(text.trimStart()).length;
  return validateTranscriptEmphasis(text.trim(), valid.map(range => ({ ...range, start: range.start - leading, end: range.end - leading })));
}
export interface CaptionTextRun { text: string; emphasis: boolean }
export function captionTextRuns(text: string, ranges?: unknown): CaptionTextRun[] {
  const valid = validateTranscriptEmphasis(text, ranges), chars = Array.from(text), runs: CaptionTextRun[] = [];
  let cursor = 0, end = chars.length;
  while (cursor < chars.length && whitespace(chars[cursor])) cursor++;
  while (end > cursor && whitespace(chars[end - 1])) end--;
  for (const range of valid) {
    if (range.start > cursor) runs.push({ text: chars.slice(cursor, range.start).join(""), emphasis: false });
    runs.push({ text: range.text, emphasis: true }); cursor = range.end;
  }
  if (cursor < end) runs.push({ text: chars.slice(cursor, end).join(""), emphasis: false });
  return runs;
}
export function transcriptWords(text: string): TranscriptEmphasis[] {
  const chars = Array.from(text), words: TranscriptEmphasis[] = [];
  for (let index = 0; index < chars.length;) {
    if (whitespace(chars[index])) { index++; continue; }
    const start = index;
    while (index < chars.length && !whitespace(chars[index])) index++;
    words.push({ start, end: index, text: chars.slice(start, index).join("") });
  }
  return words;
}
export function entireTranscriptEmphasis(text: string): TranscriptEmphasis[] {
  const words = transcriptWords(text);
  if (!words.length) return [];
  const start = words[0].start, end = words.at(-1)!.end;
  return validateTranscriptEmphasis(text, [{ start, end, text: Array.from(text).slice(start, end).join("") }]);
}
export function toggleTranscriptWord(text: string, ranges: unknown, wordIndex: number): TranscriptEmphasis[] {
  const valid = validateTranscriptEmphasis(text, ranges), words = transcriptWords(text), chars = Array.from(text);
  if (!Number.isInteger(wordIndex) || !words[wordIndex]) throw new Error("The selected caption word no longer exists.");
  const selected = words.map(word => valid.some(range => range.start <= word.start && range.end >= word.end));
  selected[wordIndex] = !selected[wordIndex];
  const result: TranscriptEmphasis[] = [];
  let adjacent = false;
  words.forEach((word, index) => {
    if (!selected[index]) { adjacent = false; return; }
    if (adjacent) { const previous = result[result.length - 1]; previous.end = word.end; previous.text = chars.slice(previous.start, previous.end).join(""); }
    else result.push({ ...word });
    adjacent = true;
  });
  return validateTranscriptEmphasis(text, result);
}
