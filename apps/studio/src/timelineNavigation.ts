import { graphFrameTime } from "./semanticGraph";
import { editorFps, validEditorRate } from "./timecode";
import { parseBeatTime } from "./sceneBeatTiming";
import { resolveCanvasRelations } from "./canvasFollow";
import { motionKeyRange } from "./timelineMotionRange";
import { validateSemanticSfx } from "./semanticSfx";
import { seconds, rationalSeconds } from "./types";
import type { Project, RationalTime } from "./types";
import type { TimelineSelection } from "./timelineSelection";

export type TimelineSnap = "off" | "frames" | "cues";
export interface TimelineView { zoom: number; start: number; snap: TimelineSnap; expanded: boolean }
export const defaultTimelineView = (): TimelineView => ({ zoom: 1, start: 0, snap: "frames", expanded: false });
export function readTimelineView(raw: string | null): TimelineView {
  if (!raw) return defaultTimelineView();
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid timeline view.");
  const v = value as Record<string, unknown>;
  if (typeof v.zoom !== "number" || !Number.isFinite(v.zoom) || v.zoom < 1 || v.zoom > 4096
    || typeof v.start !== "number" || !Number.isFinite(v.start) || v.start < 0
    || !["off","frames","cues"].includes(String(v.snap)) || typeof v.expanded !== "boolean")
    throw new Error("Invalid timeline view.");
  return { zoom: v.zoom, start: v.start, snap: v.snap as TimelineSnap, expanded: v.expanded };
}
export function timelineWindow(duration: number, view: TimelineView) {
  const span = Math.max(duration / view.zoom, Number.EPSILON);
  const start = Math.max(0, Math.min(duration - span, view.start));
  return { start, end: start + span, span };
}
export function zoomTimeline(view: TimelineView, duration: number, zoom: number, anchor: number): TimelineView {
  const before = timelineWindow(duration, view);
  const ratio = Math.max(0, Math.min(1, (anchor - before.start) / before.span));
  const next = { ...view, zoom: Math.max(1, Math.min(4096, zoom)) };
  next.start = anchor - ratio * duration / next.zoom;
  return { ...next, start: timelineWindow(duration, next).start };
}
export function timelineTicks(start: number, span: number, pixels: number, rate: RationalTime): number[] {
  const fps = editorFps(rate), desired = Math.max(1 / fps, span / Math.max(2, Math.floor(pixels / 80)));
  const power = 10 ** Math.floor(Math.log10(desired));
  const secondsStep = [1,2,5,10].map(value => value * power).find(value => value >= desired) ?? power * 10;
  const stepFrames = Math.max(1, Math.ceil(secondsStep * fps));
  const firstFrame = Math.ceil(start * fps / stepFrames) * stepFrames;
  const count = Math.min(128, Math.ceil(span * fps / stepFrames) + 2);
  return Array.from({ length: count }, (_, index) => (firstFrame + index * stepFrames) / fps)
    .filter(time => time <= start + span);
}
export function addTimelineTime(a: RationalTime, b: RationalTime): RationalTime {
  return parseBeatTime((BigInt(a.num) * BigInt(b.den) + BigInt(b.num) * BigInt(a.den)) + "/" + (BigInt(a.den) * BigInt(b.den)));
}
export function snapTimelineTime(project: Project, time: number, mode: TimelineSnap, rate: RationalTime, tolerance: number): RationalTime {
  const bounded = Math.max(0, Number.isFinite(time) ? time : 0);
  if (mode === "off" || !validEditorRate(rate)) return rationalSeconds(bounded);
  if (mode === "cues") {
    const candidates = [
      ...project.audio.cues.map(cue => cue.at), ...project.markers.map(marker => marker.at),
      ...project.scenes.flatMap(scene => [scene.start, addTimelineTime(scene.start, scene.duration),
        ...scene.beats.map(beat => addTimelineTime(scene.start, beat.start))]),
    ];
    let closest: RationalTime | null = null, distance = Math.max(0,tolerance);
    for (const at of candidates) {
      const gap = Math.abs(seconds(at) - bounded);
      if (gap <= distance) { closest = at; distance = gap; }
    }
    if (closest) return { ...closest };
  }
  const frame = Math.round(bounded * editorFps(rate));
  if (!Number.isSafeInteger(frame)) throw new Error("The displayed instant exceeds the frame navigation range.");
  return graphFrameTime(frame, rate);
}
export function projectTimelineDuration(project: Project): number {
  const sfx = (project.audio.sfx_cues ?? []).flatMap(cue => {
    try { return [validateSemanticSfx(project, cue).absolute_end_frame / 30]; } catch { return []; }
  });
  return Math.max(1,
    ...project.scenes.map(scene => seconds(scene.start) + seconds(scene.duration)),
    ...project.markers.map(marker => seconds(marker.at)),
    ...project.audio.voice_tracks.map(track => seconds(track.measured_duration)),
    ...project.audio.transcript.map(segment => seconds(segment.end)),
    ...project.audio.cues.map(cue => seconds(cue.at)), ...sfx,
    ...project.reviews.filter(review => review.anchor.branch_id === project.active_branch).flatMap(review =>
      review.anchor.end ? [seconds(review.anchor.end)] : review.anchor.start ? [seconds(review.anchor.start)] : []));
}
export interface TimelineRangeEntry {
  key: string; selection: TimelineSelection; label: string; start: RationalTime; end: RationalTime;
  sceneId?: string; historical?: boolean; detail?: string;
}
export interface TimelineExtraTracks {
  music: TimelineRangeEntry[]; sfx: TimelineRangeEntry[]; object: TimelineRangeEntry[];
  camera: TimelineRangeEntry[]; review: TimelineRangeEntry[]; issues: string[];
}
/** All geometry here is a read-only projection of saved source clocks. */
export function timelineExtraTracks(project: Project, profileId: string | null, selectedSceneId: string | null, rate: RationalTime): TimelineExtraTracks {
  const result: TimelineExtraTracks = { music: [], sfx: [], object: [], camera: [], review: [], issues: [] };
  const profile = project.deliverables.find(item => item.id === profileId);
  const mix = profile && project.production_design?.delivery_recipes?.[profile.id]?.original_mix;
  if (profile && mix?.music_asset_id) {
    const asset = project.assets.find(item => item.id === mix.music_asset_id);
    const scenes = profile.included_scene_ids.length ? profile.included_scene_ids.map(id => project.scenes.find(scene => scene.id === id)).filter(scene => scene !== undefined) : project.scenes;
    if (!asset) result.issues.push("The saved music mix refers to a missing source.");
    else for (const scene of scenes) result.music.push({ key: profile.id + ":" + scene.id,
      selection: { kind: "music", id: profile.id }, label: "Music intent · " + asset.name,
      start: scene.start, end: addTimelineTime(scene.start, scene.duration), sceneId: scene.id,
      detail: "Saved mix coverage in project time. Original source duration and PCM remain verified by Native." });
  }
  for (const cue of project.audio.sfx_cues ?? []) {
    try {
      const scheduled = validateSemanticSfx(project, cue), asset = project.assets.find(item => item.id === cue.source_asset_id);
      result.sfx.push({ key: cue.id, selection: { kind: "sfx", id: cue.id }, label: cue.event.replaceAll("_"," ") + " · " + (asset?.name ?? "Original SFX"),
        start: graphFrameTime(scheduled.absolute_start_frame, {num:"30",den:"1"}), end: graphFrameTime(scheduled.absolute_end_frame, {num:"30",den:"1"}),
        sceneId: cue.scene_id, detail: "Authored original SFX placement at exact 30 fps; this lane is not PCM evidence." });
    } catch (reason) { result.issues.push(reason instanceof Error ? reason.message : String(reason)); }
  }
  for (const scene of project.scenes.filter(item => item.id === selectedSceneId)) {
    const resolved = resolveCanvasRelations(scene.nodes, undefined, false, { duration: scene.duration, fps: rate, continuous: false });
    if (resolved.issue) result.issues.push(scene.name + ": " + resolved.issue);
    for (const node of scene.nodes) {
      const derived = resolved.tracks.get(node.id), range = motionKeyRange(derived ?? node.keyframes);
      if (!range) continue;
      result.object.push({ key: node.id, selection: { kind: "object", id: node.id }, label: node.name,
        start: addTimelineTime(scene.start, range.start), end: addTimelineTime(scene.start, range.end), sceneId: scene.id,
        detail: derived ? "Follow timing from its saved target; edit the target for the inherited path." : "Complete authored object animation; channels and curves stay editable." });
    }
  }
  for (const camera of project.production_design?.camera_reframes ?? []) {
    const scene = project.scenes.find(item => item.id === camera.scene_id);
    if (!scene) { result.issues.push("Camera source scene is missing."); continue; }
    result.camera.push({ key: camera.id, selection: { kind: "camera", id: camera.id }, label: scene.name + " · camera",
      start: scene.start, end: addTimelineTime(scene.start, graphFrameTime(camera.duration_frames, {num:"30",den:"1"})), sceneId: scene.id });
  }
  for (const review of project.reviews) {
    if (review.anchor.branch_id !== project.active_branch || !review.anchor.start
      || (review.anchor.profile_id && review.anchor.profile_id !== profileId)) continue;
    result.review.push({ key: review.id, selection: { kind: "review", id: review.id },
      label: review.status.replaceAll("_"," ") + " · " + review.body,
      start: review.anchor.start, end: review.anchor.end ?? review.anchor.start,
      historical: review.anchor.revision !== project.revision,
      detail: "Stored project-time anchor at revision " + review.anchor.revision + "; navigation does not revalidate historical content." });
  }
  return result;
}
export function packTimelineRanges(entries: TimelineRangeEntry[]) {
  const rows: number[] = [];
  const packed = [...entries].sort((a,b) => seconds(a.start) - seconds(b.start)).map(entry => {
    const start = seconds(entry.start), end = seconds(entry.end);
    let row = rows.findIndex(previous => previous <= start);
    if (row < 0) row = rows.length;
    rows[row] = Math.max(end, start + .001);
    return { ...entry, row };
  });
  return { entries: packed, height: Math.max(28, rows.length * 25 + 4) };
}
