import { useState } from "react";
import { captionTextRuns, decodeTranscriptEmphasis, encodeTranscriptEmphasis, entireTranscriptEmphasis, toggleTranscriptWord, transcriptWords } from "./transcriptEmphasis";
import type { TranscriptEmphasis } from "./types";

export default function TranscriptEmphasisEditor({ id, text, raw, disabled, onChange }: {
  id: string; text: string; raw: string; disabled: boolean; onChange: (raw: string) => void;
}) {
  const [actionError, setActionError] = useState<string | null>(null);
  let ranges: TranscriptEmphasis[] = [], sourceError: string | null = null;
  try { ranges = decodeTranscriptEmphasis(raw, text); } catch (error) { sourceError = error instanceof Error ? error.message : String(error); }
  const words = transcriptWords(text), runs = sourceError ? [] : captionTextRuns(text, ranges);
  const edit = (make: () => TranscriptEmphasis[]) => {
    try { onChange(encodeTranscriptEmphasis(make())); setActionError(null); }
    catch (error) { setActionError(error instanceof Error ? error.message : String(error)); }
  };
  return <fieldset id={id} tabIndex={-1} className="transcript-emphasis-editor" aria-label={"Caption emphasis " + id} disabled={disabled}>
    <legend>Caption emphasis</legend>
    <p>Choose words or the whole cue. Emphasis lasts for the saved cue interval; it adds no word timestamps.</p>
    <div className="transcript-emphasis-words" role="group" aria-label="Caption words">
      {words.slice(0, 128).map((word, index) => <button key={word.start + ":" + word.text} type="button"
        aria-label={"Emphasis word " + (index + 1) + ": " + word.text}
        aria-pressed={ranges.some(range => range.start <= word.start && range.end >= word.end)}
        disabled={!!sourceError} onClick={() => edit(() => toggleTranscriptWord(text, ranges, index))}>{word.text}</button>)}
    </div>
    {words.length > 128 && <p>Word selection shows the first 128 words. Shorten this caption for native reading limits or use whole-cue emphasis.</p>}
    <div className="transcript-emphasis-actions">
      <button type="button" className="button compact" disabled={!words.length} onClick={() => edit(() => entireTranscriptEmphasis(text))}>Emphasize entire cue</button>
      <button type="button" className="button compact" disabled={!raw} onClick={() => edit(() => [])}>Clear caption emphasis</button>
      <span>{ranges.length} / 8 emphasis ranges</span>
    </div>
    {(sourceError || actionError) && <p role="alert" aria-label="Caption emphasis source conflict">{sourceError ?? actionError}</p>}
    {!sourceError && text.trim() && <p className="transcript-emphasis-preview" aria-label="Caption cue style preview">
      {runs.map((run, index) => run.emphasis ? <strong key={index}>{run.text}</strong> : <span key={index}>{run.text}</span>)}
    </p>}
    <small>Source style study · native font layout remains a separate review. WebVTT keeps emphasis; SubRip requires an explicit plain-text choice.</small>
  </fieldset>;
}
