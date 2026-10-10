import {
  AudioLines,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleDashed,
  FileAudio,
  LocateFixed,
  Plus,
  Save,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type {
  AudioCue,
  Change,
  MixIntent,
  Project,
  RationalTime,
  TranscriptSegment,
  WaveformPage,
} from "./types";
import { rationalSeconds } from "./types";
import { waveformPage as loadWaveformPage } from "./api";
import NarrationTakeReview from "./NarrationTakeReview";

type Commit = (change: Change) => Promise<void>;

const WAVEFORM_PAGE_SIZE = 192;

const valueOf = (time: RationalTime) => Number(time.num) / Number(time.den);
const secondsLabel = (time: RationalTime) => {
  const value = valueOf(time);
  return Number.isFinite(value) ? value.toFixed(3) + " s" : "UNKNOWN";
};

function TranscriptRow({
  segment, commit, playhead, onSeek,
}: {
  segment: TranscriptSegment;
  commit: Commit;
  playhead: number;
  onSeek: (absoluteSeconds: number) => void;
}) {
  const [text, setText] = useState(segment.text);
  const [speaker, setSpeaker] = useState(segment.speaker ?? "");
  const [start, setStart] = useState(String(valueOf(segment.start)));
  const [end, setEnd] = useState(String(valueOf(segment.end)));

  useEffect(() => {
    setText(segment.text);
    setSpeaker(segment.speaker ?? "");
    setStart(String(valueOf(segment.start)));
    setEnd(String(valueOf(segment.end)));
  }, [segment]);

  const valid = text.trim() && Number(start) >= 0 && Number(end) > Number(start);

  const isCurrent = playhead >= valueOf(segment.start) && playhead < valueOf(segment.end);
  return (
    <div className={"audio-transcript-row" + (isCurrent ? " playback-current" : "")} aria-current={isCurrent ? "true" : undefined}>
      <div className="audio-time-pair">
        <input aria-label={"Start " + segment.id} value={start} inputMode="decimal" onChange={(event) => setStart(event.target.value)} />
        <span>→</span>
        <input aria-label={"End " + segment.id} value={end} inputMode="decimal" onChange={(event) => setEnd(event.target.value)} />
      </div>
      <input aria-label={"Speaker " + segment.id} value={speaker} maxLength={256} placeholder="Speaker" onChange={(event) => setSpeaker(event.target.value)} />
      <textarea aria-label={"Transcript " + segment.id} value={text} maxLength={8000} onChange={(event) => setText(event.target.value)} />
      <span className={"evidence-chip evidence-" + segment.alignment.kind}>{segment.alignment.kind.toUpperCase()}</span>
      <div className="audio-row-actions">
        <button
          type="button"
          className="icon-action"
          title="Seek project timeline to transcript start"
          aria-label={"Seek to transcript at " + secondsLabel(segment.start)}
          onClick={() => onSeek(valueOf(segment.start))}
        >
          <LocateFixed size={14} />
        </button>
        <button
          type="button"
          className="icon-action"
          title="Save transcript segment as explicit manual alignment"
          disabled={!valid}
          onClick={() => void commit({
            type: "upsert_transcript_segment",
            segment: {
              ...segment,
              start: rationalSeconds(Number(start)),
              end: rationalSeconds(Number(end)),
              text: text.trim(),
              speaker: speaker.trim() || null,
              alignment: { kind: "manual" },
            },
          })}
        >
          <Save size={14} />
        </button>
        <button
          type="button"
          className="icon-action"
          title="Remove transcript segment"
          onClick={() => void commit({ type: "remove_transcript_segment", segment_id: segment.id })}
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
}

function CueRow({
  cue, commit, playhead, onSeek,
}: {
  cue: AudioCue;
  commit: Commit;
  playhead: number;
  onSeek: (absoluteSeconds: number) => void;
}) {
  const [label, setLabel] = useState(cue.label);
  const [at, setAt] = useState(String(valueOf(cue.at)));
  useEffect(() => {
    setLabel(cue.label);
    setAt(String(valueOf(cue.at)));
  }, [cue]);
  const valid = label.trim() && Number(at) >= 0;
  const isCurrent = Math.abs(playhead - valueOf(cue.at)) < 1 / 24;
  return (
    <div className={"audio-cue-row" + (isCurrent ? " playback-current" : "")} aria-current={isCurrent ? "true" : undefined}>
      <input aria-label={"Cue time " + cue.id} value={at} inputMode="decimal" onChange={(event) => setAt(event.target.value)} />
      <input aria-label={"Cue label " + cue.id} value={label} maxLength={512} onChange={(event) => setLabel(event.target.value)} />
      <span className={"evidence-chip evidence-" + cue.evidence}>{cue.evidence.toUpperCase()}</span>
      <button
        type="button"
        className="icon-action"
        title="Seek project timeline to cue"
        aria-label={"Seek to cue " + cue.label}
        onClick={() => onSeek(valueOf(cue.at))}
      >
        <LocateFixed size={14} />
      </button>
      <button
        type="button"
        className="icon-action"
        disabled={!valid}
        title="Save cue"
        onClick={() => void commit({
          type: "upsert_audio_cue",
          cue: { ...cue, at: rationalSeconds(Number(at)), label: label.trim(), evidence: "manual" },
        })}
      >
        <Save size={14} />
      </button>
      <button
        type="button"
        className="icon-action"
        title="Remove cue"
        onClick={() => void commit({ type: "remove_audio_cue", cue_id: cue.id })}
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
}

export default function AudioWorkspace({
  project,
  commit,
  desktopMode,
  importVoice,
  playhead,
  onSeek,
}: {
  project: Project;
  commit: Commit;
  desktopMode: boolean;
  importVoice: (path: string, label?: string) => Promise<void>;
  playhead: number;
  onSeek: (absoluteSeconds: number) => void;
}) {
  const activeId = project.audio.active_voice_track_id;
  const active = project.audio.voice_tracks.find((track) => track.id === activeId) ?? null;
  const activeSegments = useMemo(
    () => project.audio.transcript
      .filter((segment) => segment.voice_track_id === activeId)
      .sort((left, right) => valueOf(left.start) - valueOf(right.start)),
    [project.audio.transcript, activeId],
  );

  const [path, setPath] = useState("");
  const [takeLabel, setTakeLabel] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [newText, setNewText] = useState("");
  const [newSpeaker, setNewSpeaker] = useState("");
  const [newStart, setNewStart] = useState("");
  const [newEnd, setNewEnd] = useState("");
  const [newCueLabel, setNewCueLabel] = useState("");
  const [newCueAt, setNewCueAt] = useState("");
  const [voiceGain, setVoiceGain] = useState(String(project.audio.mix.voice_gain_db));
  const [musicGain, setMusicGain] = useState(String(project.audio.mix.music_gain_db));
  const [targetLufs, setTargetLufs] = useState(project.audio.mix.target_lufs == null ? "" : String(project.audio.mix.target_lufs));
  const [targetPeak, setTargetPeak] = useState(project.audio.mix.target_true_peak_dbfs == null ? "" : String(project.audio.mix.target_true_peak_dbfs));
  const [waveform, setWaveform] = useState<WaveformPage | null>(null);
  const [waveformPageIndex, setWaveformPageIndex] = useState(0);
  const [waveformBusy, setWaveformBusy] = useState(false);
  const [waveformError, setWaveformError] = useState<string | null>(null);

  useEffect(() => {
    setWaveformPageIndex(0);
    setWaveform(null);
    setWaveformError(null);
  }, [active?.id]);

  useEffect(() => {
    if (!desktopMode || !active) {
      setWaveform(null);
      setWaveformBusy(false);
      return;
    }
    let cancelled = false;
    setWaveformBusy(true);
    setWaveformError(null);
    void loadWaveformPage(project, active.id, waveformPageIndex, WAVEFORM_PAGE_SIZE)
      .then((page) => {
        if (cancelled) return;
        setWaveform(page);
      })
      .catch((reason) => {
        if (cancelled) return;
        setWaveform(null);
        setWaveformError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!cancelled) setWaveformBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [desktopMode, active?.id, waveformPageIndex, project.id]);

  useEffect(() => {
    setVoiceGain(String(project.audio.mix.voice_gain_db));
    setMusicGain(String(project.audio.mix.music_gain_db));
    setTargetLufs(project.audio.mix.target_lufs == null ? "" : String(project.audio.mix.target_lufs));
    setTargetPeak(project.audio.mix.target_true_peak_dbfs == null ? "" : String(project.audio.mix.target_true_peak_dbfs));
  }, [project.revision, project.audio.mix]);

  const doImport = async () => {
    if (!path.trim() || !desktopMode) return;
    setImportBusy(true);
    setImportError(null);
    try {
      await importVoice(path.trim(), takeLabel.trim() || undefined);
      setPath("");
      setTakeLabel("");
    } catch (reason) {
      setImportError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setImportBusy(false);
    }
  };

  const addSegment = () => {
    if (
      !active ||
      !newText.trim() ||
      !newStart.trim() ||
      !newEnd.trim() ||
      Number(newStart) < 0 ||
      Number(newEnd) <= Number(newStart)
    ) return;
    void commit({
      type: "upsert_transcript_segment",
      segment: {
        id: crypto.randomUUID(),
        voice_track_id: active.id,
        start: rationalSeconds(Number(newStart)),
        end: rationalSeconds(Number(newEnd)),
        text: newText.trim(),
        speaker: newSpeaker.trim() || null,
        alignment: { kind: "manual" },
      },
    });
    setNewText("");
    setNewSpeaker("");
    setNewStart("");
    setNewEnd("");
  };

  const addCue = () => {
    if (!newCueLabel.trim() || Number(newCueAt) < 0 || newCueAt.trim() === "") return;
    void commit({
      type: "upsert_audio_cue",
      cue: {
        id: crypto.randomUUID(),
        label: newCueLabel.trim(),
        at: rationalSeconds(Number(newCueAt)),
        source_segment_id: null,
        evidence: "manual",
      },
    });
    setNewCueLabel("");
    setNewCueAt("");
  };

  const saveMix = () => {
    const next: MixIntent = {
      voice_gain_db: Number(voiceGain),
      music_gain_db: Number(musicGain),
      target_lufs: targetLufs.trim() === "" ? null : Number(targetLufs),
      target_true_peak_dbfs: targetPeak.trim() === "" ? null : Number(targetPeak),
    };
    void commit({ type: "set_mix_intent", mix: next });
  };

  const mixValid = [Number(voiceGain), Number(musicGain)].every(
    (value) => Number.isFinite(value) && value >= -120 && value <= 24,
  ) && [targetLufs, targetPeak].every(
    (value) => value.trim() === "" || Number.isFinite(Number(value)),
  );
  const waveformPageCount = waveform
    ? Math.max(1, Math.ceil(waveform.peak_count / waveform.page_size))
    : 0;
  const waveformStartSeconds = waveform
    ? (waveform.start_peak * waveform.frames_per_peak) / waveform.sample_rate_hz
    : 0;
  const waveformEndSeconds = waveform
    ? Math.min(
        waveform.total_frames / waveform.sample_rate_hz,
        ((waveform.start_peak + waveform.peaks.length) * waveform.frames_per_peak)
          / waveform.sample_rate_hz,
      )
    : 0;

  return (
    <section className="audio-workspace" aria-label="Audio workspace">
      <header className="audio-workspace-head">
        <div>
          <span className="section-kicker">SOURCE AUDIO / TAKE LEDGER</span>
          <h2>Voice, transcript and cue evidence</h2>
          <p>Real files are measured before admission. Transcript timing is never inferred from speaking speed.</p>
        </div>
        <div className="audio-playhead-chip">
          <span className="section-kicker">SHARED EDITORIAL TIME</span>
          <strong className="mono">{playhead.toFixed(2)} s</strong>
          <small>{project.audio.voice_tracks.length} measured take{project.audio.voice_tracks.length === 1 ? "" : "s"} · not source playback</small>
        </div>
      </header>

      <div className="audio-import-strip">
        <FileAudio size={17} />
        <label className="audio-path-field">
          <span className="field-label">Voice file · absolute path</span>
          <input
            aria-label="Voice file absolute path"
            value={path}
            disabled={!desktopMode || importBusy}
            placeholder="/absolute/path/voice.wav"
            onChange={(event) => setPath(event.target.value)}
          />
        </label>
        <label>
          <span className="field-label">Take label</span>
          <input
            aria-label="Voice take label"
            value={takeLabel}
            disabled={!desktopMode || importBusy}
            placeholder="Narrator take 03"
            maxLength={256}
            onChange={(event) => setTakeLabel(event.target.value)}
          />
        </label>
        <button type="button" className="button button-primary" disabled={!desktopMode || !path.trim() || importBusy} onClick={() => void doImport()}>
          <Plus size={14} /> {importBusy ? "Measuring…" : "Import + measure"}
        </button>
      </div>
      {!desktopMode && <div className="audio-truth-line"><CircleDashed size={14} /> Desktop runtime required for measured file import.</div>}
      {importError && <div className="portable-message error" role="alert"><strong>Voice import blocked.</strong><span>{importError}</span></div>}

      <NarrationTakeReview project={project} desktopMode={desktopMode} commit={commit}/>

      <div className="audio-main-grid">
        <aside className="voice-take-ledger" aria-label="Voice takes">
          <header><strong>Voice takes</strong><span>Byte-bound measurements</span></header>
          {project.audio.voice_tracks.length === 0 && (
            <div className="audio-empty">
              <AudioLines size={20} />
              <strong>No measured take</strong>
              <span>No waveform or timing evidence is synthesized.</span>
            </div>
          )}
          {project.audio.voice_tracks.map((track) => {
            const selected = track.id === activeId;
            return (
              <button
                type="button"
                key={track.id}
                className={selected ? "voice-take-row selected" : "voice-take-row"}
                onClick={() => !selected && void commit({ type: "set_active_voice_track", track_id: track.id })}
              >
                <span className="take-state">{selected ? <Check size={14} /> : <span />}</span>
                <span className="voice-take-copy">
                  <strong>{track.label}</strong>
                  <span>{track.sample_rate_hz.toLocaleString()} Hz · {track.channels} ch · {secondsLabel(track.measured_duration)}</span>
                  <span className="mono">sha256:{track.source_sha256.slice(0, 12)}</span>
                </span>
              </button>
            );
          })}
        </aside>

        <div className="audio-evidence-editor">
          <section className="audio-meter-ledger" aria-label="Audio measurements">
            <div><span>Active take</span><strong>{active?.label ?? "NONE"}</strong></div>
            <div><span>Loudness</span><strong>{active?.loudness_lufs == null ? "UNKNOWN" : active.loudness_lufs.toFixed(1) + " LUFS"}</strong></div>
            <div><span>True peak</span><strong>{active?.true_peak_dbfs == null ? "UNKNOWN" : active.true_peak_dbfs.toFixed(2) + " dBFS"}</strong></div>
            <div><span>Alignment</span><strong>{activeSegments.length ? activeSegments.length + " SEGMENTS" : "NONE"}</strong></div>
          </section>

          <section className="waveform-proxy-section" aria-label="Measured sample peak waveform">
            <header>
              <div>
                <strong>Measured waveform proxy</strong>
                <span>Sample-peak max-abs from immutable source bytes · not LUFS or true-peak evidence.</span>
              </div>
              {waveform && (
                <span className="mono">
                  {waveformStartSeconds.toFixed(2)}–{waveformEndSeconds.toFixed(2)} s
                </span>
              )}
            </header>
            {!active && (
              <div className="waveform-proxy-state">
                <AudioLines size={18} aria-hidden="true" />
                <span>No active measured take. No waveform is synthesized.</span>
              </div>
            )}
            {active && !desktopMode && (
              <div className="waveform-proxy-state">
                <CircleDashed size={18} aria-hidden="true" />
                <span>Desktop runtime required for measured waveform proxy.</span>
              </div>
            )}
            {active && desktopMode && waveformBusy && !waveform && (
              <div className="waveform-proxy-state" role="status" aria-live="polite">
                <CircleDashed size={18} aria-hidden="true" />
                <span>Decoding immutable source into a paged proxy…</span>
              </div>
            )}
            {active && desktopMode && waveformError && (
              <div className="waveform-proxy-state error" role="alert">
                <CircleDashed size={18} />
                <span>Waveform evidence unavailable: {waveformError}</span>
              </div>
            )}
            {active && desktopMode && waveform && (
              <>
                <div
                  className="waveform-proxy-bars"
                  role="img"
                  aria-label={"Measured sample-peak waveform page " + (waveform.page_index + 1) + " of " + waveformPageCount}
                >
                  {waveform.peaks.map((peak, index) => (
                    <span
                      key={waveform.start_peak + index}
                      style={{ height: Math.max(2, Math.min(100, peak * 100)) + "%" }}
                      title={(((waveform.start_peak + index) * waveform.frames_per_peak / waveform.sample_rate_hz).toFixed(3)) + " s · " + (peak * 100).toFixed(1) + "% sample peak"}
                    />
                  ))}
                </div>
                <footer className="waveform-proxy-footer">
                  <span>
                    {waveform.peaks.length.toLocaleString()} peaks loaded · {waveform.frames_per_peak.toLocaleString()} frames/peak
                  </span>
                  <div className="waveform-page-controls" aria-label="Waveform page controls">
                    <button
                      type="button"
                      className="icon-action"
                      aria-label="Previous waveform page"
                      disabled={waveformBusy || !waveform.has_previous}
                      onClick={() => setWaveformPageIndex((value) => Math.max(0, value - 1))}
                    >
                      <ChevronLeft size={14} />
                    </button>
                    <span className="mono">Page {waveform.page_index + 1} / {waveformPageCount}</span>
                    <button
                      type="button"
                      className="icon-action"
                      aria-label="Next waveform page"
                      disabled={waveformBusy || !waveform.has_next}
                      onClick={() => setWaveformPageIndex((value) => value + 1)}
                    >
                      <ChevronRight size={14} />
                    </button>
                  </div>
                </footer>
              </>
            )}
          </section>

          <section className="audio-editor-section">
            <header>
              <div><strong>Transcript alignment</strong><span>Saving edited timing records manual evidence.</span></div>
              <span className="mono">{active ? "take:" + active.id.slice(0, 8) : "no-active-take"}</span>
            </header>
            {activeSegments.map((segment) => <TranscriptRow key={segment.id} segment={segment} commit={commit} playhead={playhead} onSeek={onSeek} />)}
            <div className="audio-transcript-new">
              <div className="audio-time-pair">
                <input aria-label="New transcript start" value={newStart} inputMode="decimal" placeholder="start s" onChange={(event) => setNewStart(event.target.value)} />
                <span>→</span>
                <input aria-label="New transcript end" value={newEnd} inputMode="decimal" placeholder="end s" onChange={(event) => setNewEnd(event.target.value)} />
              </div>
              <input aria-label="New transcript speaker" value={newSpeaker} placeholder="Speaker" onChange={(event) => setNewSpeaker(event.target.value)} />
              <textarea aria-label="New transcript text" value={newText} placeholder={active ? "Enter transcript text" : "Import/select a measured take first"} disabled={!active} onChange={(event) => setNewText(event.target.value)} />
              <button type="button" className="button" disabled={!active || !newText.trim() || !newStart.trim() || !newEnd.trim() || !(Number(newStart) >= 0) || !(Number(newEnd) > Number(newStart))} onClick={addSegment}>
                <Plus size={14} /> Add manual segment
              </button>
            </div>
          </section>

          <section className="audio-editor-section">
            <header><div><strong>Stable cues</strong><span>Cue IDs survive text and label edits.</span></div><span>{project.audio.cues.length}</span></header>
            {project.audio.cues.map((cue) => <CueRow key={cue.id} cue={cue} commit={commit} playhead={playhead} onSeek={onSeek} />)}
            <div className="audio-cue-new">
              <input aria-label="New cue time" value={newCueAt} inputMode="decimal" placeholder="time s" onChange={(event) => setNewCueAt(event.target.value)} />
              <input aria-label="New cue label" value={newCueLabel} maxLength={512} placeholder="Cue label" onChange={(event) => setNewCueLabel(event.target.value)} />
              <button type="button" className="button" disabled={!newCueLabel.trim() || newCueAt.trim() === "" || Number(newCueAt) < 0} onClick={addCue}><Plus size={14} /> Add cue</button>
            </div>
          </section>

          <section className="audio-editor-section audio-mix-section">
            <header><div><strong>Mix intent</strong><span>Targets are intent; they are not measured output evidence.</span></div></header>
            <div className="audio-mix-grid">
              <label><span className="field-label">Voice gain dB</span><input aria-label="Voice gain dB" value={voiceGain} inputMode="decimal" onChange={(event) => setVoiceGain(event.target.value)} /></label>
              <label><span className="field-label">Music gain dB</span><input aria-label="Music gain dB" value={musicGain} inputMode="decimal" onChange={(event) => setMusicGain(event.target.value)} /></label>
              <label><span className="field-label">Target LUFS</span><input aria-label="Target LUFS" value={targetLufs} inputMode="decimal" placeholder="unset" onChange={(event) => setTargetLufs(event.target.value)} /></label>
              <label><span className="field-label">Target true peak dBFS</span><input aria-label="Target true peak dBFS" value={targetPeak} inputMode="decimal" placeholder="unset" onChange={(event) => setTargetPeak(event.target.value)} /></label>
              <button type="button" className="button" disabled={!mixValid} onClick={saveMix}><Save size={14} /> Save mix intent</button>
            </div>
          </section>
        </div>
      </div>
    </section>
  );
}
