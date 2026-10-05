export type ProjectState = "current" | "stale" | "unknown";
export type SceneStatus = "draft" | "review" | "approved" | "needs_work";
export type RendererKind =
  | "motion-canvas"
  | "mlt"
  | "blender"
  | "manim-community"
  | "remotion"
  | "manim-gl";
export type LockKind = "content" | "timing" | "position" | "style" | "renderer";
export type CoordinateSpace = "project_pixels" | "normalized" | "scene_local";
export type NodeProperty = "position" | "size" | "rotation" | "opacity" | "text" | "style" | "parent" | "order";
export type BlendMode = "normal" | "multiply" | "screen" | "add";
export type RelationKind = "align_left" | "align_center_x" | "align_right" | "align_top" | "align_center_y" | "align_bottom" | "follow" | "attach";

export interface RationalTime { num: string; den: string; }
export interface NodeStyle {
  fill: string | null;
  stroke: string | null;
  stroke_width: number;
  font_family: string | null;
  font_size: number | null;
  font_weight: number | null;
  line_height: number | null;
  blend_mode: BlendMode;
}
export interface NodeRelation { id: string; kind: RelationKind; target_id: string; }
export interface CanvasTransform {
  x: number; y: number; width: number; height: number;
  rotation_deg: number; opacity: number;
}
export interface CameraState {
  center_x: number; center_y: number; zoom: number;
  rotation_deg: number; safe_margin: number;
}
export interface Beat {
  id: string; label: string; objective: string;
  start: RationalTime; duration: RationalTime;
}
export interface CanvasNode extends CanvasTransform {
  id: string; name: string; kind: string; parent_id: string | null;
  text: string | null; coordinate_space: CoordinateSpace; z_index: number;
  style: NodeStyle; relations: NodeRelation[]; property_locks: NodeProperty[];
}
export interface Scene {
  id: string; name: string; objective: string;
  start: RationalTime; duration: RationalTime;
  renderer: RendererKind; status: SceneStatus;
  beats: Beat[]; nodes: CanvasNode[]; camera: CameraState;
}
export interface Marker { id: string; at: RationalTime; label: string; }
export interface ProjectLock { id: string; resource: string; kind: LockKind; note: string; }
export interface DeliverableProfile {
  id: string; name: string; width: number; height: number;
  language: string; captions: boolean;
}
export interface Branch { id: string; name: string; base_revision: number; created_at: string; }
export interface Asset {
  id: string; name: string; media_type: string;
  content_sha256: string | null; source_revision: string | null;
}

export type SourceReference =
  | { kind: "asset"; asset_id: string }
  | { kind: "url"; url: string }
  | { kind: "project_revision"; revision: number }
  | { kind: "manual_note"; label: string };
export interface Claim {
  id: string; text: string; source: SourceReference | null;
  context: string; source_revision: string | null;
}
export interface Brief {
  objective: string; audience: string; constraints: string[];
  exclusions: string[]; claims: Claim[];
}
export interface NarrativeBeat {
  id: string; label: string; objective: string; audience_takeaway: string;
  claim_ids: string[]; preferred_duration: RationalTime | null;
}
export interface ProtectedSection { beat_id: string; reason: string; }
export interface Narrative {
  premise: string; beats: NarrativeBeat[]; protected_sections: ProtectedSection[];
}
export interface VoiceTrack {
  id: string; asset_id: string; label: string; sample_rate_hz: number; channels: number;
  measured_duration: RationalTime; source_sha256: string;
  loudness_lufs: number | null; true_peak_dbfs: number | null;
}
export type AlignmentEvidence =
  | { kind: "manual" }
  | { kind: "measured"; engine: string; source_sha256: string; confidence_millis: number | null }
  | { kind: "unknown" };
export interface TranscriptSegment {
  id: string; voice_track_id: string; start: RationalTime; end: RationalTime;
  text: string; speaker: string | null; alignment: AlignmentEvidence;
}
export type CueEvidence = "manual" | "transcript_aligned" | "measured" | "unknown";
export interface AudioCue {
  id: string; label: string; at: RationalTime;
  source_segment_id: string | null; evidence: CueEvidence;
}
export interface MixIntent {
  voice_gain_db: number; music_gain_db: number;
  target_lufs: number | null; target_true_peak_dbfs: number | null;
}
export interface AudioState {
  voice_tracks: VoiceTrack[]; transcript: TranscriptSegment[];
  cues: AudioCue[]; mix: MixIntent;
}
export interface VisualToken { name: string; value: string; }
export type ReducedMotionBehavior = "instant" | "fade_only" | "static_equivalent";
export interface MotionVerb {
  name: string; meaning: string; duration_ms: number; reduced_motion: ReducedMotionBehavior;
}
export type RightsStatus = "cleared" | "restricted" | "unknown";
export interface FontRight { family: string; source: string; rights_status: RightsStatus; }
export interface VisualLanguage {
  version: number; name: string; palette: VisualToken[]; type_tokens: VisualToken[];
  motion_grammar: MotionVerb[]; anti_slop_rules: string[]; fonts: FontRight[];
}
export type ProposalScope =
  | { kind: "project" }
  | { kind: "scene"; scene_id: string }
  | { kind: "selection"; resource_refs: string[] };
export interface SearchBudget { candidates: number; model_calls: number; max_tokens: number; }
export type CreativeEdit =
  | { kind: "scene_objective"; scene_id: string; objective: string }
  | { kind: "scene_order"; scene_ids: string[] }
  | { kind: "beat_rewrite"; beat_id: string; objective: string }
  | { kind: "renderer_choice"; scene_id: string; renderer: string };
export interface Proposal {
  id: string; title: string; rationale: string; structure: string[]; edits: CreativeEdit[];
}
export interface ProposalSet {
  id: string; base_revision: number; scope: ProposalScope;
  search_budget: SearchBudget; proposals: Proposal[]; selected: string | null;
}
export type DataClass = "metadata" | "text" | "frame" | "audio" | "source_code";
export type InvocationOutcome = "succeeded" | "failed" | "cancelled" | "unknown";
export interface InvocationBudget { max_calls: number; max_tokens: number; max_cost_microunits: number | null; }
export interface ModelInvocationReceipt {
  id: string; provider: string; model: string; provider_version: string | null;
  base_revision: number; resource_refs: string[]; data_classes: DataClass[];
  budget: InvocationBudget; outcome: InvocationOutcome;
}

export interface Project {
  schema_version: number; id: string; generation: string; revision: number;
  title: string; state: ProjectState; active_branch: string;
  branches: Branch[]; scenes: Scene[]; markers: Marker[]; assets: Asset[];
  locks: ProjectLock[]; deliverables: DeliverableProfile[];
  brief: Brief; narrative: Narrative; audio: AudioState;
  visual_language: VisualLanguage; proposal_sets: ProposalSet[];
  model_invocations: ModelInvocationReceipt[]; updated_at: string;
}

export type Change =
  | { type: "rename_project"; title: string }
  | { type: "set_brief"; objective: string; audience: string; constraints: string[]; exclusions: string[] }
  | { type: "set_narrative_premise"; premise: string }
  | { type: "add_scene"; name: string; objective: string; duration_seconds: number }
  | { type: "move_scene"; scene_id: string; to_index: number }
  | { type: "update_scene_objective"; scene_id: string; objective: string }
  | { type: "set_scene_renderer"; scene_id: string; renderer: RendererKind }
  | { type: "set_scene_status"; scene_id: string; status: SceneStatus }
  | { type: "transform_canvas_node"; scene_id: string; node_id: string; transform: CanvasTransform }
  | { type: "update_canvas_text"; scene_id: string; node_id: string; text: string | null }
  | { type: "set_node_property_lock"; scene_id: string; node_id: string; property: NodeProperty; locked: boolean }
  | { type: "set_camera"; scene_id: string; camera: CameraState }
  | { type: "add_marker"; at: RationalTime; label: string }
  | { type: "set_visual_language"; visual_language: VisualLanguage }
  | { type: "add_proposal_set"; proposal_set: ProposalSet }
  | { type: "select_proposal"; proposal_set_id: string; proposal_id: string }
  | { type: "set_lock"; resource: string; kind: LockKind; note: string }
  | { type: "remove_lock"; lock_id: string };

export interface Bootstrap {
  project: Project;
  native_sdk: {
    application: string;
    pinned_revision: string;
    mode: "tauri" | "browser-demo";
  };
}
export const seconds = (time: RationalTime): number => Number(time.num) / Number(time.den);
export const rationalSeconds = (value: number): RationalTime => ({ num: String(Math.round(value * 1000)), den: "1000" });
