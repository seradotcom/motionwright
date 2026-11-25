import { useEffect, useMemo, useRef, useState } from "react";
import type { Project } from "./types";
import { audioMediaToProject, audioReviewScope, buildAudioReviewClock, projectToAudioMedia,
  type AudioReviewClock, type AudioReviewRequest } from "./audioReviewClock";
import { useLocalAudioElement } from "./audioReviewPlayback";
import "./audio-review.css";

type Props = { project: Project; request: AudioReviewRequest; playhead: number; editorialPlaying: boolean;
  onSeek: (seconds: number) => void; onClose: () => void };
export default function AudioReviewTransport(props: Props) {
  const inspection = useMemo(() => {
    try {
      if (props.request.scope !== audioReviewScope(props.project))
        throw new Error("Audio review stopped because the saved project or source revision changed. Load the current source again.");
      if (!(props.request.wav instanceof Blob) || !props.request.wav.size || props.request.wav.size > 16 * 1024 * 1024
          || props.request.wav.type !== "audio/wav") throw new Error("The verified local WAV is unavailable.");
      return { clock: buildAudioReviewClock(props.project, props.request), error: null };
    } catch (reason) { return { clock: null, error: reason instanceof Error ? reason.message : String(reason) }; }
  }, [props.project, props.request]);
  return inspection.clock ? <MappedAudioReview {...props} clock={inspection.clock}/>
    : <section className="audio-review-transport" aria-label="Timeline audio review">
        <p className="production-error" role="alert">{inspection.error}</p>
        <button className="button compact" onClick={props.onClose}>Close timeline audio review</button>
      </section>;
}

function MappedAudioReview({ project, request, clock, playhead, editorialPlaying, onSeek, onClose }: Props & { clock: AudioReviewClock }) {
  const [url, setUrl] = useState<string | undefined>();
  const player = useLocalAudioElement();
  const [ready, setReady] = useState(false), readyRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const frame = useRef<number | null>(null), lastEmitted = useRef<number | null>(null);
  const suppressPauseSample = useRef(false);
  const live = useRef({ playhead, onSeek, clock }); live.current = { playhead, onSeek, clock };
  const inside = projectToAudioMedia(clock, playhead) !== null;
  const stopFrame = () => { if (frame.current !== null) cancelAnimationFrame(frame.current); frame.current = null; };
  const publish = () => {
    const element = player.element.current;
    if (!element || !readyRef.current) return;
    const mapped = audioMediaToProject(live.current.clock, element.currentTime);
    if (mapped === null) {
      element.pause(); stopFrame(); setError("The media clock is outside the saved audio range. Playback stopped.");
      return;
    }
    if (lastEmitted.current === null || Math.abs(mapped - lastEmitted.current) >= .5 / clock.sampleRate) {
      lastEmitted.current = mapped;
      live.current.onSeek(mapped);
    }
  };
  const tick = () => {
    publish();
    const element = player.element.current;
    if (element && !element.paused && !element.ended) frame.current = requestAnimationFrame(tick);
    else frame.current = null;
  };
  useEffect(() => {
    const next = URL.createObjectURL(request.wav); setUrl(next);
    return () => { stopFrame(); URL.revokeObjectURL(next); };
  }, [request.wav]); // media ref cleanup pauses before the verified bytes are released
  useEffect(() => {
    const element = player.element.current;
    if (!element) return;
    if (editorialPlaying) {
      suppressPauseSample.current = true; element.pause(); stopFrame();
      setNotice("Audio paused while the design preview is playing.");
      return;
    }
    if (lastEmitted.current !== null && Math.abs(playhead - lastEmitted.current) < .5 / clock.sampleRate) return;
    suppressPauseSample.current = true; element.pause(); stopFrame();
    if (!readyRef.current) return;
    const next = projectToAudioMedia(clock, playhead);
    if (next === null) {
      setNotice("The project playhead is outside this reviewed audio range. Seek to an included scene or the audio start.");
      return;
    }
    if (Math.abs(element.currentTime - next) > .5 / clock.sampleRate) element.currentTime = next;
    lastEmitted.current = playhead; setNotice(null);
  }, [playhead, editorialPlaying, clock]);
  const metadata = () => {
    const element = player.element.current;
    if (!element) return;
    const duration = clock.sampleCount / clock.sampleRate;
    if (!Number.isFinite(element.duration) || Math.abs(element.duration - duration) > 1 / clock.sampleRate + 1e-9) {
      readyRef.current = false; setReady(false); element.pause(); stopFrame();
      setError("The decoded WAV duration differs from the saved source clock. Timeline playback is unavailable.");
      return;
    }
    readyRef.current = true; setReady(true); setError(null);
    const next = projectToAudioMedia(clock, live.current.playhead);
    if (next !== null) { element.currentTime = next; lastEmitted.current = live.current.playhead; }
  };
  return <section className="audio-review-transport" aria-label="Timeline audio review">
    <div className="audio-review-heading">
      <div><strong>Timeline audio review</strong><span>{request.label} · saved r{project.revision}</span></div>
      <button type="button" className="button compact" onClick={onClose}>Close timeline audio review</button>
    </div>
    <div className="audio-review-controls">
      <audio aria-label="Timeline audio playback" ref={player.bind} controls preload="metadata" src={url}
        onLoadedMetadata={metadata}
        onPlay={() => {
          if (!readyRef.current || !inside || error) { player.pause(); return; }
          suppressPauseSample.current = false; player.claim(); setNotice(null); stopFrame(); tick();
        }}
        onPause={() => { stopFrame(); if (!suppressPauseSample.current) publish(); suppressPauseSample.current = false; }}
        onSeeking={() => { if (readyRef.current) publish(); }}
        onTimeUpdate={() => { if (readyRef.current && !player.element.current?.paused) publish(); }}
        onEnded={() => { stopFrame(); publish(); }}
        onError={() => { readyRef.current = false; setReady(false); stopFrame(); setError("The local decoder could not read this verified WAV."); }}/>
      <button type="button" className="button compact" disabled={!ready || !!error}
        onClick={() => { const first = audioMediaToProject(clock, 0); if (first !== null) onSeek(first); }}>
        Seek reviewed audio start
      </button>
    </div>
    <p className="production-help">Audio drives the shared project playhead. Seeking from another view pauses audio.
      {request.kind === "mix" ? " The complete saved delivery cut retains its original scene times." : " This active take uses its measured source clock."}
      {" "}Local review is not final audiovisual synchronization or listening approval.</p>
    {(!inside || notice) && <p role="status" className="production-help">{notice ?? "The project playhead is outside this reviewed audio range."}</p>}
    {error && <p role="alert" className="production-error">{error}</p>}
  </section>;
}
