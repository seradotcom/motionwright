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
export type CaptionFormat = "web_vtt" | "srt";
export type VideoCodec = "h264" | "hevc" | "prores_422_hq" | "vp9" | "av1";
export type AudioCodec = "aac" | "pcm_s16_le" | "opus";
export interface DeliverableProfile {
  id: string; name: string; width: number; height: number;
  language: string; captions: boolean; caption_format: CaptionFormat;
  video_codec: VideoCodec; audio_codec: AudioCodec; audio_sample_rate_hz: number;
  brand_profile: string | null; cut_label: string | null;
}
export interface CaptionExportResult { path: string; cue_count: number; }
export interface Branch {
  id: string; name: string; parent_branch: string | null;
  base_revision: number; head_revision: number; protected: boolean; created_at: string;
}
export interface BranchState {
  scenes: Scene[]; markers: Marker[]; locks: ProjectLock[]; deliverables: DeliverableProfile[];
  brief: Brief; narrative: Narrative; audio: AudioState; visual_language: VisualLanguage;
  proposal_sets: ProposalSet[]; model_invocations: ModelInvocationReceipt[];
}
export interface BranchWorkspace {
  branch_id: string; base_revision: number; base_state: BranchState; current_state: BranchState;
}
export type ReviewKind = "technical" | "creative" | "editorial";
export type ReviewStatus = "open" | "resolved" | "dismissed" | "needs_recheck";
export interface ReviewAnchor {
  resource: string; branch_id: string; revision: number;
  start: RationalTime | null; end: RationalTime | null;
  locale: string | null; profile_id: string | null;
}
export interface CreativeReview {
  id: string; kind: ReviewKind; anchor: ReviewAnchor; body: string;
  status: ReviewStatus; resolution: string | null; created_at: string; resolved_at: string | null;
}
export interface MergeRecord {
  id: string; source_branch: string; target_branch: string;
  base_revision: number; committed_revision: number | null; merged_at: string;
}
export interface Asset {
  id: string; name: string; media_type: string;
  content_sha256: string | null; source_revision: string | null;
}

export type ExtensionKind =
  | "remotion-renderer"
  | "manim-gl-renderer"
  | "generative-assets"
  | "catalog-package";
export interface ExtensionProfile {
  id: string;
  name: string;
  kind: ExtensionKind;
  package_version: string;
  digest_sha256: string;
  license: string;
  source: string;
  rights_status: RightsStatus;
  enabled: boolean;
  permissions: Array<"read_project" | "read_assets" | "write_artifacts" | "network">;
}

export type ExternalSystem = "launchwright";
export type HandoffDirection = "context_input" | "context_output" | "artifact_output" | "evidence_output";
export type ExternalResourceKind = "release" | "target" | "context" | "artifact" | "evidence";
export interface HandoffBinding {
  id: string;
  system: ExternalSystem;
  direction: HandoffDirection;
  external_kind: ExternalResourceKind;
  external_id: string;
  external_revision: string | null;
  local_resource: string;
  artifact_sha256: string | null;
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
  voice_tracks: VoiceTrack[]; active_voice_track_id: string | null;
  transcript: TranscriptSegment[]; cues: AudioCue[]; mix: MixIntent;
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
export type ModelProviderKind = "manual" | "external_agent" | "local" | "remote";
export interface ModelRequestDraft {
  provider_kind: ModelProviderKind;
  provider: string;
  model: string;
  resource_refs: string[];
  data_classes: DataClass[];
  budget: InvocationBudget;
}
export interface ModelContextDisclosure {
  resource_ref: string;
  data_class: DataClass;
  label: string;
  media_type: string | null;
  estimated_bytes: number;
  content_sha256: string | null;
  preview: string | null;
  preview_truncated: boolean;
  untrusted_data: boolean;
}
export interface ModelRequestPreflight {
  schema: string;
  project_id: string;
  generation: string;
  base_revision: number;
  provider_kind: ModelProviderKind;
  provider: string;
  model: string;
  resource_refs: string[];
  data_classes: DataClass[];
  budget: InvocationBudget;
  disclosures: ModelContextDisclosure[];
  estimated_total_bytes: number;
  source_data_classes: DataClass[];
  explicit_source_consent_required: boolean;
  fallback_provider: string | null;
  studio_network_dispatch_supported: boolean;
  network_dispatched: boolean;
  fingerprint_sha256: string;
}

export type ProductionJobState =
  | "queued"
  | "running"
  | "cancel_requested"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "outcome_unknown";
export type ProductionJobApplicability = "current" | "stale";
export type ProductionObservationState = "observed" | "failed_known" | "outcome_unknown";
export interface ProductionJobProgress {
  completed: number; total: number | null; message: string | null;
}
export interface ProductionJobProjection {
  job_ref: string;
  provider: string;
  root_request_id: string;
  generation: string;
  revision: number;
  state: ProductionJobState;
  cancellation_requested: boolean;
  applicability: ProductionJobApplicability;
  last_command: string;
  created_at: string;
  last_observed_at: string;
  local_observations: number;
  provider_generation: number | null;
  progress: ProductionJobProgress | null;
  artifact_available: boolean;
  result_available: boolean;
  last_observation: ProductionObservationState;
}

export type WorkflowOverviewStatus = "available" | "unconfigured" | "browser_demo";
export type WorkflowAction =
  | "record_start"
  | "record_stop"
  | "compile"
  | "suggestion_compile"
  | "proposal_plan"
  | "proposal_accept"
  | "verify"
  | "replay"
  | "promote";

export interface WorkflowActionResult {
  command: string;
  request_id: string;
  authority: Record<string, unknown> | null;
  result: unknown;
}

export interface WorkflowOverview {
  status: WorkflowOverviewStatus;
  reason: string | null;
  connection_identity: string | null;
  authority: Record<string, unknown> | null;
  traces: Record<string, unknown> | null;
  candidates: Record<string, unknown> | null;
  patterns: Record<string, unknown> | null;
  suggestions: Record<string, unknown> | null;
  proposals: Record<string, unknown> | null;
  promotions: Record<string, unknown> | null;
}

export interface Project {
  schema_version: number; id: string; generation: string; revision: number;
  title: string; state: ProjectState; active_branch: string;
  branches: Branch[]; branch_workspaces: BranchWorkspace[];
  reviews: CreativeReview[]; merges: MergeRecord[];
  scenes: Scene[]; markers: Marker[]; assets: Asset[];
  locks: ProjectLock[]; deliverables: DeliverableProfile[];
  brief: Brief; narrative: Narrative; audio: AudioState;
  visual_language: VisualLanguage; proposal_sets: ProposalSet[];
  model_invocations: ModelInvocationReceipt[]; extensions: ExtensionProfile[]; handoffs: HandoffBinding[]; updated_at: string;
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
  | { type: "set_scene_duration"; scene_id: string; duration: RationalTime }
  | { type: "add_canvas_node"; scene_id: string; node: CanvasNode }
  | { type: "remove_canvas_node"; scene_id: string; node_id: string }
  | { type: "transform_canvas_node"; scene_id: string; node_id: string; transform: CanvasTransform }
  | { type: "update_canvas_text"; scene_id: string; node_id: string; text: string | null }
  | { type: "update_canvas_style"; scene_id: string; node_id: string; style: NodeStyle }
  | { type: "reparent_canvas_node"; scene_id: string; node_id: string; parent_id: string | null; z_index: number }
  | { type: "set_canvas_relations"; scene_id: string; node_id: string; relations: NodeRelation[] }
  | { type: "set_node_property_lock"; scene_id: string; node_id: string; property: NodeProperty; locked: boolean }
  | { type: "set_camera"; scene_id: string; camera: CameraState }
  | { type: "add_marker"; at: RationalTime; label: string }
  | { type: "add_asset"; asset: Asset }
  | { type: "remove_asset"; asset_id: string }
  | { type: "upsert_deliverable"; profile: DeliverableProfile }
  | { type: "remove_deliverable"; profile_id: string }
  | { type: "set_active_voice_track"; track_id: string }
  | { type: "upsert_transcript_segment"; segment: TranscriptSegment }
  | { type: "remove_transcript_segment"; segment_id: string }
  | { type: "upsert_audio_cue"; cue: AudioCue }
  | { type: "remove_audio_cue"; cue_id: string }
  | { type: "set_mix_intent"; mix: MixIntent }
  | { type: "set_visual_language"; visual_language: VisualLanguage }
  | { type: "add_proposal_set"; proposal_set: ProposalSet }
  | { type: "select_proposal"; proposal_set_id: string; proposal_id: string }
  | { type: "upsert_extension"; extension: ExtensionProfile }
  | { type: "remove_extension"; extension_id: string }
  | { type: "upsert_handoff"; binding: HandoffBinding }
  | { type: "remove_handoff"; binding_id: string }
  | { type: "record_model_invocation"; receipt: ModelInvocationReceipt }
  | { type: "create_branch"; name: string }
  | { type: "checkout_branch"; branch_id: string }
  | { type: "merge_branch"; source_branch_id: string }
  | {
      type: "add_review"; kind: ReviewKind; resource: string; body: string;
      start: RationalTime | null; end: RationalTime | null;
      locale: string | null; profile_id: string | null;
    }
  | { type: "resolve_review"; review_id: string; resolution: string }
  | { type: "reopen_review"; review_id: string }
  | { type: "set_lock"; resource: string; kind: LockKind; note: string }
  | { type: "remove_lock"; lock_id: string };

export interface ProjectEvent {
  revision: number;
  change: { type: string; [key: string]: unknown };
  created_at: string;
}

export interface Bootstrap {
  project: Project;
  native_sdk: {
    application: string;
    pinned_revision: string;
    mode: "tauri" | "browser-demo";
  };
}

export interface PortableBundlePlan {
  project_id: string;
  title: string;
  source_generation: string;
  revision: string;
  event_count: number;
  blob_count: number;
  total_blob_bytes: string;
  rotates_generation: boolean;
}

export interface PortableBundleExport {
  destination: string;
  blob_count: number;
  total_blob_bytes: string;
}
export const seconds = (time: RationalTime): number => Number(time.num) / Number(time.den);

const gcd = (left: number, right: number): number => {
  let a = Math.abs(Math.trunc(left));
  let b = Math.abs(Math.trunc(right));
  while (b !== 0) {
    const remainder = a % b;
    a = b;
    b = remainder;
  }
  return a || 1;
};

export const rationalSeconds = (value: number): RationalTime => {
  if (!Number.isFinite(value)) throw new Error("time must be finite");
  const numerator = Math.round(value * 1000);
  if (numerator === 0) return { num: "0", den: "1" };
  const divisor = gcd(numerator, 1000);
  return {
    num: String(numerator / divisor),
    den: String(1000 / divisor),
  };
};
