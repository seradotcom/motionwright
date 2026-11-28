import { Film, Pause, Play, Plus, SkipBack, SkipForward } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { Change, Project, RationalTime, RendererKind } from "./types";
import { seconds } from "./types";
import { formatEditorTimecode as formatTime, rulerTime, supportsDropFrame, timebaseLabel, validEditorRate } from "./timecode";
import type { TimecodeMode } from "./timecode";
import { resolveTimelineSelection, timelineSelectionScope } from "./timelineSelection";
import type { TimelineSelection } from "./timelineSelection";
import { timelineMarkerTime } from "./timelineMarkerTime";
import { addTimelineTime, defaultTimelineView, packTimelineRanges, projectTimelineDuration, readTimelineView, snapTimelineTime,
  timelineExtraTracks, timelineTicks, timelineWindow, timelineSceneAt, zoomTimeline } from "./timelineNavigation";
import type { TimelineRangeEntry, TimelineSnap } from "./timelineNavigation";
import "./timeline-navigation.css";

function IconButton({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return <button type="button" className="icon-button" aria-label={label} title={label} onClick={onClick}>{children}</button>;
}
function RangeLane({ kind, entries, selection, position, span, select }: {
  kind: string; entries: TimelineRangeEntry[]; selection: TimelineSelection | null;
  position: (time: number) => string; span: number; select: (entry: TimelineRangeEntry) => void;
}) {
  const packed = packTimelineRanges(entries);
  return <div className={"track timeline-range-track timeline-" + kind + "-track"} aria-label={kind + " timeline lane"}>
    {packed.entries.length ? packed.entries.map(entry => <button type="button" key={entry.key}
      className={"timeline-range" + (selection?.kind === entry.selection.kind && selection.id === entry.selection.id ? " selected" : "") + (entry.historical ? " historical" : "")}
      style={{ left: position(seconds(entry.start)), width: ((seconds(entry.end) - seconds(entry.start)) / span) * 100 + "%", top: 3 + entry.row * 25 }}
      aria-label={kind + ": " + entry.label} aria-pressed={selection?.kind === entry.selection.kind && selection.id === entry.selection.id}
      data-source-id={entry.selection.id} data-start={entry.start.num + "/" + entry.start.den} data-end={entry.end.num + "/" + entry.end.den}
      onPointerDown={event => event.stopPropagation()} onClick={() => select(entry)}
      title={entry.label + (entry.detail ? " · " + entry.detail : "")}>
      <span>{entry.label}</span>{entry.historical && <small>historical</small>}
    </button>) : <span className="timeline-empty-lane">No saved {kind === "object" ? "animation in the selected scene" : kind + " ranges"}</span>}
  </div>;
}

export default function Timeline({
  project,
  selectedSceneId,
  selectScene,
  playhead,
  setPlayhead,
  commit,
  playing,
  togglePlayback,
  stepFrame,
  rate,
  mode,
  selectedProfileId,
  onSelectProfile,
  onSelectMode,
  rendererLabels,
  onEditSelection,
}: {
  project: Project;
  selectedSceneId: string | null;
  selectScene: (id: string, seekToStart?: boolean) => void;
  playhead: number;
  setPlayhead: (value: number) => void;
  commit: (change: Change) => Promise<void>;
  playing: boolean;
  togglePlayback: () => void;
  stepFrame: (direction: -1 | 1) => void;
  rate: RationalTime;
  mode: TimecodeMode;
  selectedProfileId: string | null;
  onSelectProfile: (id: string) => void;
  onSelectMode: (mode: TimecodeMode) => void;
  rendererLabels: Record<RendererKind,string>;
  onEditSelection: (selection: TimelineSelection) => void;
}) {
  const [selectionIdentity, setSelection] = useState<TimelineSelection | null>(null);
  const selection = resolveTimelineSelection(project, selectionIdentity);
  const selectionMissing = selectionIdentity !== null && selection === null;
  useEffect(() => {
    // Rendering already hides a removed resource. Clear its identity as well,
    // without erasing a newer selection made before this effect is processed.
    if (selectionMissing) setSelection(current => current === selectionIdentity ? null : current);
  }, [selectionIdentity, selectionMissing]);
  const storageKey = "motionwright.timeline-view.v1:" + timelineSelectionScope(project);
  const [initialView] = useState(() => {
    try { return { view: readTimelineView(window.sessionStorage.getItem(storageKey)), warning: "" }; }
    catch { return { view: defaultTimelineView(), warning: "The prior timeline view could not be restored. Project data is unchanged." }; }
  });
  const [view, setView] = useState(initialView.view);
  const [viewWarning, setViewWarning] = useState(initialView.warning);
  const area = useRef<HTMLDivElement>(null);
  const [pixels, setPixels] = useState(1000);
  const exactSeek = useRef<{ at: RationalTime; value: number } | null>(null);
  const gesture = useRef<{ kind: "seek" | "pan"; x: number; start: number; span: number; width: number } | null>(null);
  useEffect(() => {
    try { window.sessionStorage.setItem(storageKey, JSON.stringify(view)); }
    catch { setViewWarning("The timeline view is available in this session but its recovery preference could not be saved."); }
  }, [storageKey, view]);
  useEffect(() => {
    if (!area.current) return;
    const observer = new ResizeObserver(entries => setPixels(entries[0].contentRect.width));
    observer.observe(area.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (playing || exactSeek.current?.value !== playhead) exactSeek.current = null;
  }, [playhead, playing]);
  const duration = projectTimelineDuration(project);
  const windowRange = timelineWindow(duration, view);
  const position = (time: number) => ((time - windowRange.start) / windowRange.span) * 100 + "%";
  const extras = useMemo(() => timelineExtraTracks(project, selectedProfileId, selectedSceneId, rate),
    [project, selectedProfileId, selectedSceneId, rate]);
  const heights = [24,28,packTimelineRanges(extras.music).height,packTimelineRanges(extras.sfx).height,28,28,36,
    packTimelineRanges(extras.object).height,packTimelineRanges(extras.camera).height,28,packTimelineRanges(extras.review).height];
  const rows = { gridTemplateRows: heights.map(height => height + "px").join(" "), height: heights.reduce((sum,height) => sum + height,0) };
  const pan = (amount: number) => setView(current => ({ ...current, start: timelineWindow(duration, { ...current, start: timelineWindow(duration,current).start + amount }).start }));
  const zoom = (value: number) => setView(current => zoomTimeline(current, duration, value, Math.max(windowRange.start, Math.min(windowRange.end, playhead))));
  const seek = (time: number) => {
    try {
      const at = snapTimelineTime(project, Math.max(0,Math.min(duration,time)), view.snap, rate, windowRange.span * 8 / Math.max(1,pixels));
      const value = Math.max(0,Math.min(duration,seconds(at)));
      exactSeek.current = seconds(at) === value ? { at, value } : null;
      setSelection(null); setPlayhead(value);
      const target = timelineSceneAt(project, value);
      if (target && target.id !== selectedSceneId) selectScene(target.id, false);
    } catch (reason) { setViewWarning(reason instanceof Error ? reason.message : String(reason)); }
  };
  const ticks = timelineTicks(windowRange.start, windowRange.span, pixels, rate);
  // The Audio ledger retains every take. The VO lane represents the active
  // take used by narration and native mix intent, not overlapping alternatives.
  const measuredVoice = project.audio.voice_tracks.filter((track) => track.id === project.audio.active_voice_track_id);
  const measuredAssetIds = new Set(project.audio.voice_tracks.map((track) => track.asset_id));
  const pendingAudio = project.assets.filter(
    (asset) => asset.media_type.startsWith("audio/") && !measuredAssetIds.has(asset.id),
  );
  const beatEntries = project.scenes.flatMap((scene) =>
    scene.beats.map((beat) => {
      const sceneStart = seconds(scene.start);
      const sceneEnd = sceneStart + seconds(scene.duration);
      const start = Math.min(sceneEnd, sceneStart + seconds(beat.start));
      const end = Math.min(sceneEnd, start + seconds(beat.duration));
      return { scene, beat, start, end };
    }),
  );

  const selectAt = (
    time: number,
    nextSelection: TimelineSelection,
    sceneId?: string,
    exactAt?: RationalTime,
  ) => {
    const bounded = Math.max(0, Math.min(duration, time));
    exactSeek.current = exactAt ? { at:{...exactAt},value:bounded } : null;
    setPlayhead(bounded);
    setSelection(nextSelection);
    const targetScene = sceneId
      ? project.scenes.find((scene) => scene.id === sceneId) ?? null
      : timelineSceneAt(project, bounded);
    if (targetScene && targetScene.id !== selectedSceneId) {
      selectScene(targetScene.id, false);
    }
  };

  return (
    <section className={"timeline-panel timeline-with-navigation" + (view.expanded ? " timeline-expanded" : "")} aria-label="Timeline">
      <div className="timeline-header">
        <div className="timeline-title">
          <Film size={15} />
          Timeline
          <span className="muted">shared clock</span>
        </div>
        <div className="timeline-selection-context" aria-live="polite">
          {selection ? (
            <>
              <span>{selection.kind}</span>
              <strong>{selection.label}</strong>
            </>
          ) : (
            <>
              <span>context</span>
              <strong>{project.scenes.find((scene) => scene.id === selectedSceneId)?.name ?? "project"}</strong>
            </>
          )}
        </div>
        <div className="timeline-timebase" aria-label="Editor timebase controls">
          <label htmlFor="editor-timebase">Display profile</label>
          <select
            id="editor-timebase"
            aria-label="Editor preview timebase"
            value={selectedProfileId ?? ""}
            disabled={project.deliverables.length === 0}
            onChange={(event) => onSelectProfile(event.target.value)}
            title="A display-only frame clock. Does not modify the export profile or project."
          >
            {project.deliverables.length === 0 ? <option value="">24 fps fallback</option> : null}
            {project.deliverables.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name} · {validEditorRate(profile.frame_rate)
                  ? timebaseLabel(profile.frame_rate, "ndf")
                  : "invalid rate · 24 fps fallback"}
              </option>
            ))}
          </select>
          {supportsDropFrame(rate) && (
            <select aria-label="Timecode numbering" value={mode}
              title="Display numbering only; does not change exported media."
              onChange={(event) => onSelectMode(event.target.value as TimecodeMode)}>
              <option value="ndf">NDF</option>
              <option value="df">DF</option>
            </select>
          )}
        </div>
        <div className="timeline-transport">
          <IconButton label="Step back one frame" onClick={() => stepFrame(-1)}>
            <SkipBack size={13} />
          </IconButton>
          <IconButton label={playing ? "Pause design preview" : "Play design preview"} onClick={togglePlayback}>
            {playing ? <Pause size={13} /> : <Play size={13} />}
          </IconButton>
          <IconButton label="Step forward one frame" onClick={() => stepFrame(1)}>
            <SkipForward size={13} />
          </IconButton>
          <button
            type="button"
            className="button compact"
            onClick={() => commit({
              type: "add_marker",
              at: exactSeek.current?.value === playhead ? exactSeek.current.at : timelineMarkerTime(playhead, rate),
              label: "Marker " + (project.markers.length + 1),
            })}
          >
            <Plus size={12} /> Marker
          </button>
          <span className="timecode" title={timebaseLabel(rate, mode) + " · display only"}>{formatTime(playhead, rate, mode)}</span>
        </div>
      </div>
      <div className="timeline-navigation" aria-label="Timeline view controls">
        <button className="button compact" aria-label="Zoom out timeline" disabled={view.zoom <= 1} onClick={() => zoom(view.zoom / 2)}>−</button>
        <label>Zoom <select aria-label="Timeline zoom" value={view.zoom} onChange={event => zoom(Number(event.target.value))}>
          {[...new Set([1,2,4,8,16,32,64,128,256,512,1024,2048,4096,view.zoom])].sort((a,b)=>a-b).map(value => <option key={value} value={value}>{value.toFixed(value % 1 ? 2 : 0)}×</option>)}
        </select></label>
        <button className="button compact" aria-label="Zoom in timeline" disabled={view.zoom >= 4096} onClick={() => zoom(view.zoom * 2)}>+</button>
        <button className="button compact" onClick={() => setView(current => ({ ...current, zoom:1,start:0 }))}>Fit project</button>
        <button className="button compact" disabled={!selectedSceneId} onClick={() => {
          const scene = project.scenes.find(item => item.id === selectedSceneId);
          if (scene) setView(current => ({ ...current, zoom: Math.max(1,Math.min(4096,duration / seconds(scene.duration))), start: seconds(scene.start) }));
        }}>Fit scene</button>
        <button className="button compact" aria-label="Pan timeline earlier" disabled={windowRange.start <= 0} onClick={() => pan(-windowRange.span / 2)}>←</button>
        <input type="range" aria-label="Timeline pan" min={0} max={Math.max(0,duration - windowRange.span)} step="any"
          disabled={view.zoom === 1} value={windowRange.start} onChange={event => setView(current => ({ ...current,start:Number(event.target.value) }))}/>
        <button className="button compact" aria-label="Pan timeline later" disabled={windowRange.end >= duration} onClick={() => pan(windowRange.span / 2)}>→</button>
        <button className="button compact" onClick={() => setView(current => ({ ...current,
          start: timelineWindow(duration,{...current,start:playhead-windowRange.span/2}).start }))}>Show playhead</button>
        <label>Snap <select aria-label="Timeline snapping" value={view.snap} onChange={event => setView(current => ({ ...current,snap:event.target.value as TimelineSnap }))}>
          <option value="frames">Frames</option><option value="cues">Cues + frames</option><option value="off">Off</option>
        </select></label>
        <button className="button compact" aria-expanded={view.expanded} onClick={() => setView(current => ({...current,expanded:!current.expanded}))}>{view.expanded ? "Compact timeline" : "Expand timeline"}</button>
        <span className="timeline-window-label">{formatTime(windowRange.start,rate,mode)} — {formatTime(windowRange.end,rate,mode)}</span>
        {selection && selection.kind !== "marker" && <button className="button compact" onClick={() => onEditSelection(selection)}>
          {selection.kind === "object" ? "Edit animation" : selection.kind === "camera" ? "Edit camera" : selection.kind === "review" ? "Open review" : selection.kind === "beat" ? "Edit scene beats" : "Open audio editor"}
        </button>}
        {(viewWarning || extras.issues.length > 0) && <span className="timeline-view-warning" role="status">{viewWarning || extras.issues.join(" · ")}</span>}
      </div>
      <div className="timeline-body">
        <div className="track-labels" style={rows} aria-hidden="true">
          <div className="ruler-label">time</div>
          <div><span>VO</span><span className="track-type">audio</span></div>
          <div><span>Music</span><span className="track-type">intent</span></div>
          <div><span>SFX</span><span className="track-type">source</span></div>
          <div><span>Transcript</span><span className="track-type">text</span></div>
          <div><span>Beats</span><span className="track-type">story</span></div>
          <div><span>Scenes</span><span className="track-type">semantic</span></div>
          <div><span>Objects</span><span className="track-type">scene</span></div>
          <div><span>Camera</span><span className="track-type">path</span></div>
          <div><span>Cues</span><span className="track-type">markers</span></div>
          <div><span>Reviews</span><span className="track-type">ranges</span></div>
        </div>
        <div
          ref={area}
          className="track-area"
          style={rows}
          role="group"
          tabIndex={0}
          aria-label="Timeline navigation surface"
          data-view-start={windowRange.start}
          data-view-end={windowRange.end}
          title="Drag to seek; middle-drag to pan. Left/right step frames, Page Up/Down pan, +/− zoom, Home/End seek."
          onKeyDown={event => {
            if (event.target !== event.currentTarget) return;
            const actions: Record<string,() => void> = {
              ArrowLeft: () => stepFrame(-1), ArrowRight: () => stepFrame(1),
              PageUp: () => pan(-windowRange.span/2), PageDown: () => pan(windowRange.span/2),
              Home: () => seek(0), End: () => seek(duration), "+": () => zoom(view.zoom*2), "=": () => zoom(view.zoom*2),
              "-": () => zoom(view.zoom/2), " ": togglePlayback,
            };
            if (actions[event.key]) { event.preventDefault(); event.stopPropagation(); actions[event.key](); }
          }}
          onPointerDown={event => {
            if (event.button !== 0 && event.button !== 1) return;
            event.preventDefault();
            const bounds = event.currentTarget.getBoundingClientRect();
            gesture.current = {kind:event.button===1?"pan":"seek",x:event.clientX,start:windowRange.start,span:windowRange.span,width:bounds.width};
            event.currentTarget.setPointerCapture(event.pointerId);
            if (event.button===0) seek(windowRange.start + Math.max(0,Math.min(1,(event.clientX-bounds.left)/bounds.width))*windowRange.span);
          }}
          onPointerMove={event => {
            const drag = gesture.current;
            if (!drag) return;
            if (drag.kind==="pan") {
              const nextStart = drag.start-(event.clientX-drag.x)/drag.width*drag.span;
              setView(current=>({...current,start:timelineWindow(duration,{...current,start:nextStart}).start}));
            } else {
              const bounds=event.currentTarget.getBoundingClientRect();
              seek(drag.start+Math.max(0,Math.min(1,(event.clientX-bounds.left)/bounds.width))*drag.span);
            }
          }}
          onPointerUp={event => { gesture.current=null; if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId); }}
          onPointerCancel={() => { gesture.current=null; }}
        >
          <div className="ruler">
            {ticks.map((tick) => (
              <span key={tick} style={{ left: position(tick) }}>
                {windowRange.span < 10 ? formatTime(tick,rate,mode) : rulerTime(tick)}
              </span>
            ))}
          </div>
          <div className="track voice-track">
            {measuredVoice.map((track) => (
              <div
                className="audio-presence measured"
                key={track.id}
                aria-label={"Measured voice track " + track.label}
                style={{ left: position(0), right:"auto", width: (seconds(track.measured_duration) / windowRange.span) * 100 + "%" }}
              >
                <span>{track.label}</span>
                <small>{track.sample_rate_hz} Hz · {track.channels} ch · measured duration</small>
              </div>
            ))}
            {measuredVoice.length === 0 && project.audio.voice_tracks.length > 0 && (
              <div className="audio-pending" aria-label="No active voice take">
                <span>No active voice take</span>
                <small>choose a measured take in Audio</small>
              </div>
            )}
            {project.audio.voice_tracks.length === 0 && pendingAudio.length > 0 && (
              <div className="audio-pending" aria-label="Audio source awaiting measurement">
                <span>{pendingAudio[0].name}</span>
                <small>source attached · duration unmeasured</small>
              </div>
            )}
            {project.audio.voice_tracks.length === 0 && pendingAudio.length === 0 && (
              <div className="audio-pending empty" aria-label="No measured voice track">
                <span>No measured voice</span>
                <small>timeline duration is not inferred from text</small>
              </div>
            )}
          </div>
          <RangeLane kind="music" entries={extras.music} selection={selection} position={position} span={windowRange.span}
            select={entry => selectAt(seconds(entry.start),entry.selection,entry.sceneId,entry.start)}/>
          <RangeLane kind="sfx" entries={extras.sfx} selection={selection} position={position} span={windowRange.span}
            select={entry => selectAt(seconds(entry.start),entry.selection,entry.sceneId,entry.start)}/>
          <div className="track transcript-track">
            {project.audio.transcript.length === 0 ? (
              <span className="timeline-empty-lane">No measured or manually aligned transcript</span>
            ) : (
              project.audio.transcript.map((segment) => {
                const start = seconds(segment.start);
                const end = seconds(segment.end);
                const selected = selection?.kind === "transcript" && selection.id === segment.id;
                return (
                  <button
                    key={segment.id}
                    type="button"
                    className={"timeline-transcript" + (selected ? " selected" : "")}
                    style={{
                      left: position(start),
                      width: (Math.max(0, end - start) / windowRange.span) * 100 + "%",
                    }}
                    aria-label={"Transcript: " + segment.text}
                    aria-pressed={selected}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={() => selectAt(start, { kind: "transcript", id: segment.id },undefined,segment.start)}
                    title={segment.text}
                  >
                    {segment.text}
                  </button>
                );
              })
            )}
          </div>
          <div className="track beat-track">
            {beatEntries.length === 0 ? (
              <span className="timeline-empty-lane">No scene beats authored</span>
            ) : (
              beatEntries.map(({ scene, beat, start, end }) => {
                const selected = selection?.kind === "beat" && selection.id === beat.id;
                return (
                  <button
                    key={beat.id}
                    type="button"
                    className={"timeline-beat" + (selected ? " selected" : "")}
                    data-source-id={beat.id}
                    style={{
                      left: position(start),
                      width: (Math.max(0, end - start) / windowRange.span) * 100 + "%",
                    }}
                    aria-label={"Beat: " + beat.label}
                    aria-pressed={selected}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={() => selectAt(
                      start,
                      { kind: "beat", id: beat.id },
                      scene.id,
                      addTimelineTime(scene.start,beat.start),
                    )}
                    title={beat.objective}
                  >
                    <span>{beat.label}</span>
                  </button>
                );
              })
            )}
          </div>
          <div className="track scene-track">
            {project.scenes.map((scene, index) => (
              <button
                key={scene.id}
                type="button"
                className={"timeline-clip clip-" + (index % 4) + (selectedSceneId === scene.id ? " selected" : "")}
                style={{
                  left: position(seconds(scene.start)),
                  width: (seconds(scene.duration) / windowRange.span) * 100 + "%",
                }}
                onPointerDown={(event) => event.stopPropagation()}
                onClick={() => {
                  setSelection(null);
                  selectScene(scene.id);
                }}
              >
                <span>{scene.name}</span>
                <small>{rendererLabels[scene.renderer]}</small>
              </button>
            ))}
          </div>
          <RangeLane kind="object" entries={extras.object} selection={selection} position={position} span={windowRange.span}
            select={entry => selectAt(seconds(entry.start),entry.selection,entry.sceneId,entry.start)}/>
          <RangeLane kind="camera" entries={extras.camera} selection={selection} position={position} span={windowRange.span}
            select={entry => selectAt(seconds(entry.start),entry.selection,entry.sceneId,entry.start)}/>
          <div className="track review-track">
            {project.audio.cues.map((cue) => {
              const at = seconds(cue.at);
              const selected = selection?.kind === "cue" && selection.id === cue.id;
              return (
                <button
                  key={cue.id}
                  type="button"
                  className={"timeline-cue" + (selected ? " selected" : "")}
                  style={{ left: position(at) }}
                  aria-label={"Cue: " + cue.label}
                  aria-pressed={selected}
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={() => selectAt(at, { kind: "cue", id: cue.id },undefined,cue.at)}
                  title={cue.label + " · " + cue.evidence}
                >
                  <i />
                  <b>{cue.label}</b>
                </button>
              );
            })}
            {project.markers.map((marker) => {
              const at = seconds(marker.at);
              const selected = selection?.kind === "marker" && selection.id === marker.id;
              return (
                <button
                  key={marker.id}
                  type="button"
                  className={"timeline-marker" + (selected ? " selected" : "")}
                  style={{ left: position(at) }}
                  aria-label={"Marker: " + marker.label}
                  aria-pressed={selected}
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={() => selectAt(at, { kind: "marker", id: marker.id },undefined,marker.at)}
                  title={marker.label}
                >
                  <i />
                  <b>{marker.label}</b>
                </button>
              );
            })}
          </div>
          <RangeLane kind="review" entries={extras.review} selection={selection} position={position} span={windowRange.span}
            select={entry => selectAt(seconds(entry.start),entry.selection,entry.sceneId,entry.start)}/>
          <div className="splice-line" style={{ left: position(playhead) }}>
            <span />
          </div>
        </div>
      </div>
    </section>
  );
}
