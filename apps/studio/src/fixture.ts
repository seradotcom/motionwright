import type { Bootstrap, Project } from "./types";

const now = new Date().toISOString();
const id = (suffix: string) => "018f0000-0000-7000-8000-" + suffix.padStart(12, "0");

export const fixtureProject: Project = {
  schema_version: 1,
  id: id("1"),
  generation: id("2"),
  revision: 12,
  title: "Semwright Product Film",
  state: "current",
  active_branch: id("3"),
  branches: [
    { id: id("3"), name: "main", base_revision: 0, created_at: now },
    { id: id("4"), name: "vertical-cut", base_revision: 9, created_at: now }
  ],
  scenes: [
    {
      id: id("11"),
      name: "Reasoning is solved",
      objective: "Open on the gap between agents that can plan and software they cannot reliably operate.",
      start: { num: 0, den: 1 },
      duration: { num: 7, den: 1 },
      renderer: "motion-canvas",
      status: "approved",
      beats: [],
      nodes: [
        { id: id("111"), name: "Reasoning headline", kind: "text", parent_id: null, x: 160, y: 160, width: 920, height: 120, rotation_deg: 0, opacity: 1, text: "AI agents can reason." },
        { id: id("112"), name: "Agent trace", kind: "shape", parent_id: null, x: 180, y: 360, width: 860, height: 170, rotation_deg: 0, opacity: 0.82, text: null }
      ]
    },
    {
      id: id("12"),
      name: "Pixels are brittle",
      objective: "Contrast screenshot automation with native semantic control and explicit authority.",
      start: { num: 7, den: 1 },
      duration: { num: 9, den: 1 },
      renderer: "motion-canvas",
      status: "review",
      beats: [],
      nodes: [
        { id: id("121"), name: "Pixel grid", kind: "grid", parent_id: null, x: 180, y: 170, width: 520, height: 420, rotation_deg: 0, opacity: 0.55, text: null },
        { id: id("122"), name: "Semantic tree", kind: "tree", parent_id: null, x: 760, y: 170, width: 520, height: 420, rotation_deg: 0, opacity: 1, text: null }
      ]
    },
    {
      id: id("13"),
      name: "Semwright acts natively",
      objective: "Show the semantic path from agent intent to Broker, Driver Host and real creative application.",
      start: { num: 16, den: 1 },
      duration: { num: 12, den: 1 },
      renderer: "blender",
      status: "draft",
      beats: [],
      nodes: [
        { id: id("131"), name: "Pipeline", kind: "diagram", parent_id: null, x: 120, y: 190, width: 1120, height: 300, rotation_deg: 0, opacity: 1, text: null }
      ]
    },
    {
      id: id("14"),
      name: "One revision graph",
      objective: "Close on artifacts, provenance and revision-safe iteration across tools.",
      start: { num: 28, den: 1 },
      duration: { num: 8, den: 1 },
      renderer: "manim-community",
      status: "draft",
      beats: [],
      nodes: []
    }
  ],
  markers: [
    { id: id("21"), at: { num: 7, den: 1 }, label: "Problem turn" },
    { id: id("22"), at: { num: 28, den: 1 }, label: "Close" }
  ],
  assets: [
    { id: id("31"), name: "voiceover.wav", media_type: "audio/wav", content_sha256: "5f".repeat(32), source_revision: "voice-r3" },
    { id: id("32"), name: "semwright-mark.svg", media_type: "image/svg+xml", content_sha256: "a1".repeat(32), source_revision: "brand-r1" },
    { id: id("33"), name: "native-demo.glb", media_type: "model/gltf-binary", content_sha256: "0c".repeat(32), source_revision: "scene-r8" }
  ],
  locks: [
    { id: id("41"), resource: "scene:" + id("11"), kind: "timing", note: "Approved VO sync" }
  ],
  deliverables: [
    { id: id("51"), name: "Master 16:9", width: 1920, height: 1080, language: "en", captions: true },
    { id: id("52"), name: "Vertical 9:16", width: 1080, height: 1920, language: "en", captions: true },
    { id: id("53"), name: "Square 1:1", width: 1080, height: 1080, language: "en", captions: true }
  ],
  updated_at: now
};

export const fixtureBootstrap: Bootstrap = {
  project: structuredClone(fixtureProject),
  native_sdk: {
    application: "motionwright",
    pinned_revision: "4d291de26724810017ce7b6d185326514cb79fa6",
    mode: "browser-demo"
  }
};
