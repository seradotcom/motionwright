import type { Bootstrap, BranchState, CanvasNode, Project } from "./types";

const now = new Date().toISOString();
const id = (suffix: string) => "018f0000-0000-7000-8000-" + suffix.padStart(12, "0");
const camera = { center_x: 960, center_y: 540, zoom: 1, rotation_deg: 0, safe_margin: 0.05 };

const node = (
  suffix: string,
  name: string,
  kind: string,
  x: number,
  y: number,
  width: number,
  height: number,
  text: string | null = null,
  zIndex = 0,
): CanvasNode => ({
  id: id(suffix),
  name,
  kind,
  parent_id: null,
  x,
  y,
  width,
  height,
  rotation_deg: 0,
  opacity: 1,
  text,
  coordinate_space: "project_pixels",
  z_index: zIndex,
  style: {
    fill: kind === "text" ? "#F5F5F2" : "#1D242C",
    stroke: kind === "text" ? null : "#5A6570",
    stroke_width: kind === "text" ? 0 : 1,
    font_family: kind === "text" ? "Instrument Sans Variable" : null,
    font_size: kind === "text" ? 72 : null,
    font_weight: kind === "text" ? 700 : null,
    line_height: kind === "text" ? 1.05 : null,
    blend_mode: "normal",
  },
  relations: [],
  property_locks: [],
  keyframes: [],
});

export const fixtureProject: Project = {
  schema_version: 1,
  id: id("1"),
  generation: id("2"),
  revision: 12,
  title: "Semwright Product Film",
  state: "current",
  active_branch: id("3"),
  branches: [
    { id: id("3"), name: "main", parent_branch: null, base_revision: 0, head_revision: 12, protected: false, created_at: now },
    { id: id("4"), name: "vertical-cut", parent_branch: id("3"), base_revision: 9, head_revision: 9, protected: false, created_at: now }
  ],
  branch_workspaces: [],
  reviews: [
    {
      id: id("71"),
      kind: "creative",
      anchor: {
        resource: "scene:" + id("12"),
        branch_id: id("3"),
        revision: 11,
        start: null,
        end: null,
        locale: "en-US",
        profile_id: null,
      },
      body: "Check whether the mechanism contrast still reads before the renderer handoff.",
      status: "needs_recheck",
      resolution: null,
      created_at: now,
      resolved_at: null,
    }
  ],
  merges: [],
  scenes: [
    {
      id: id("11"),
      name: "Reasoning is solved",
      objective: "Open on the gap between agents that can plan and software they cannot reliably operate.",
      start: { num: "0", den: "1" },
      duration: { num: "7", den: "1" },
      renderer: "motion-canvas",
      status: "approved",
      beats: [
        {
          id: id("211"),
          label: "Reasoning gap",
          objective: "Name the divide between planning and reliable software action.",
          start: { num: "1", den: "1" },
          duration: { num: "2", den: "1" },
        },
      ],
      nodes: [
        node("111", "Reasoning headline", "text", 160, 160, 920, 120, "AI agents can reason.", 2),
        { ...node("112", "Agent trace", "shape", 180, 360, 860, 170, null, 1), opacity: 0.82 }
      ],
      camera: { ...camera },
    },
    {
      id: id("12"),
      name: "Pixels are brittle",
      objective: "Contrast screenshot automation with native semantic control and explicit authority.",
      start: { num: "7", den: "1" },
      duration: { num: "9", den: "1" },
      renderer: "motion-canvas",
      status: "review",
      beats: [
        {
          id: id("212"),
          label: "Pixel failure",
          objective: "Make brittle coordinate automation concrete before the semantic contrast.",
          start: { num: "1", den: "1" },
          duration: { num: "3", den: "1" },
        },
      ],
      nodes: [
        { ...node("121", "Pixel grid", "grid", 180, 170, 520, 420, null, 1), opacity: 0.55 },
        node("122", "Semantic tree", "tree", 760, 170, 520, 420, null, 2)
      ],
      camera: { ...camera },
    },
    {
      id: id("13"),
      name: "Semwright acts natively",
      objective: "Show the semantic path from agent intent to Broker, Driver Host and real creative application.",
      start: { num: "16", den: "1" },
      duration: { num: "12", den: "1" },
      renderer: "blender",
      status: "draft",
      beats: [
        {
          id: id("213"),
          label: "Native handoff",
          objective: "Follow intent through policy and the native application boundary.",
          start: { num: "2", den: "1" },
          duration: { num: "3", den: "1" },
        },
      ],
      nodes: [
        node("131", "Pipeline", "diagram", 120, 190, 1120, 300, null, 1)
      ],
      camera: { ...camera, zoom: 1.05 },
    },
    {
      id: id("14"),
      name: "One revision graph",
      objective: "Close on artifacts, provenance and revision-safe iteration across tools.",
      start: { num: "28", den: "1" },
      duration: { num: "8", den: "1" },
      renderer: "manim-community",
      status: "draft",
      beats: [
        {
          id: id("214"),
          label: "Revision trail",
          objective: "Resolve the story on provenance and rebuildable project state.",
          start: { num: "1", den: "1" },
          duration: { num: "2", den: "1" },
        },
      ],
      nodes: [],
      camera: { ...camera },
    }
  ],
  markers: [
    { id: id("21"), at: { num: "7", den: "1" }, label: "Problem turn" },
    { id: id("22"), at: { num: "28", den: "1" }, label: "Close" }
  ],
  assets: [
    { id: id("31"), name: "voiceover.wav", media_type: "audio/wav", content_sha256: "5f".repeat(32), source_revision: "fixture-r3" },
    { id: id("32"), name: "semwright-mark.svg", media_type: "image/svg+xml", content_sha256: "a1".repeat(32), source_revision: "fixture-r1" },
    { id: id("33"), name: "native-demo.glb", media_type: "model/gltf-binary", content_sha256: "0c".repeat(32), source_revision: "fixture-r8" }
  ],
  locks: [
    { id: id("41"), resource: "scene:" + id("11"), kind: "timing", note: "Approved VO sync" }
  ],
  deliverables: [
    { id: id("51"), name: "Master 16:9", width: 1920, height: 1080, language: "en", captions: true, caption_format: "web_vtt", video_codec: "h264", audio_codec: "aac", audio_sample_rate_hz: 48000, brand_profile: null, cut_label: null },
    { id: id("52"), name: "Vertical 9:16", width: 1080, height: 1920, language: "en", captions: true, caption_format: "web_vtt", video_codec: "h264", audio_codec: "aac", audio_sample_rate_hz: 48000, brand_profile: null, cut_label: "social" },
    { id: id("53"), name: "Square 1:1", width: 1080, height: 1080, language: "en", captions: true, caption_format: "web_vtt", video_codec: "h264", audio_codec: "aac", audio_sample_rate_hz: 48000, brand_profile: null, cut_label: null }
  ],
  brief: {
    objective: "Explain why native semantic software control is a stronger substrate for agentic creative work than pixel-only automation.",
    audience: "Technical product builders evaluating reliable AI-assisted creative workflows.",
    constraints: ["Preserve Native SDK authority boundaries", "Do not imply evidence that has not been measured"],
    exclusions: ["No fake waveform", "No fabricated renderer PASS"],
    claims: [],
  },
  narrative: {
    premise: "Agents already reason well; the hard part is applying intent safely and repeatably inside real software.",
    beats: [],
    protected_sections: [],
  },
  audio: {
    voice_tracks: [],
    active_voice_track_id: null,
    transcript: [],
    cues: [
      {
        id: id("221"),
        label: "Problem contrast",
        at: { num: "8", den: "1" },
        source_segment_id: null,
        evidence: "manual",
      },
      {
        id: id("222"),
        label: "Broker handoff",
        at: { num: "18", den: "1" },
        source_segment_id: null,
        evidence: "manual",
      },
    ],
    mix: {
      voice_gain_db: 0,
      music_gain_db: -12,
      target_lufs: null,
      target_true_peak_dbfs: null,
    },
  },
  visual_language: {
    version: 1,
    name: "Cut Room Ledger",
    palette: [
      { name: "ink", value: "#F2F4F3" },
      { name: "review", value: "#F5B84A" },
      { name: "surface", value: "#0F1216" },
    ],
    type_tokens: [],
    motion_grammar: [
      { name: "splice", meaning: "Commit a timing or selection change without decorative motion.", duration_ms: 180, reduced_motion: "static_equivalent" },
    ],
    anti_slop_rules: ["No glassmorphism", "No decorative KPI cards", "Never use color as the only state signal"],
    fonts: [],
  },
  proposal_sets: [
    {
      id: id("61"),
      base_revision: 11,
      scope: { kind: "scene", scene_id: id("12") },
      search_budget: { candidates: 3, model_calls: 0, max_tokens: 0 },
      proposals: [
        {
          id: id("611"),
          title: "Direct proof",
          rationale: "Fastest path from fragility to native authority.",
          structure: ["Pixel action", "Semantic target", "Revision receipt"],
          edits: [{ kind: "scene_objective", scene_id: id("12"), objective: "Show brittle pixel targeting, then replace it with a native semantic target and revision receipt." }],
        },
        {
          id: id("612"),
          title: "Contrast cut",
          rationale: "Makes the mechanism difference legible at a glance.",
          structure: ["Screenshot uncertainty", "Native semantic control", "Verified state"],
          edits: [{ kind: "scene_objective", scene_id: id("12"), objective: "Contrast screenshot uncertainty with native semantic control and explicit verified state." }],
        },
        {
          id: id("613"),
          title: "Artifact trail",
          rationale: "Emphasizes provenance and maintenance across revisions.",
          structure: ["Intent", "Broker", "Driver", "Readback", "Revision"],
          edits: [{ kind: "scene_objective", scene_id: id("12"), objective: "Follow one intent through Broker, Driver, readback and the resulting revision artifact." }],
        }
      ],
      selected: null,
    }
  ],
  model_invocations: [],
  extensions: [],
  handoffs: [],
  updated_at: now
};

const fixtureBranchState = (): BranchState => structuredClone({
  scenes: fixtureProject.scenes,
  markers: fixtureProject.markers,
  locks: fixtureProject.locks,
  deliverables: fixtureProject.deliverables,
  brief: fixtureProject.brief,
  narrative: fixtureProject.narrative,
  audio: fixtureProject.audio,
  visual_language: fixtureProject.visual_language,
  proposal_sets: fixtureProject.proposal_sets,
  model_invocations: fixtureProject.model_invocations,
});

const mainState = fixtureBranchState();
const verticalState = fixtureBranchState();
verticalState.scenes[1].objective = "Reframe the pixel-versus-native contrast for a vertical composition.";
fixtureProject.branch_workspaces = [
  { branch_id: id("3"), base_revision: 0, base_state: structuredClone(mainState), current_state: structuredClone(mainState) },
  { branch_id: id("4"), base_revision: 9, base_state: structuredClone(mainState), current_state: verticalState },
];

export const fixtureBootstrap: Bootstrap = {
  project: structuredClone(fixtureProject),
  native_sdk: {
    application: "motionwright",
    pinned_revision: "937177b82a403ef9b6284065639d3b47ee5da941",
    mode: "browser-demo"
  }
};
