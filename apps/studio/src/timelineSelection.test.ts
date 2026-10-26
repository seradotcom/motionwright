import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { resolveTimelineSelection, timelineSelectionScope } from "./timelineSelection";
import type { TimelineSelection } from "./timelineSelection";
import type { Project } from "./types";

// Pure view-selection checks. App owns the keyed Timeline lifetime; these
// helpers do not grant authority, commit a revision or exercise a native host.
const sharedId = "10000000-0000-4000-8000-000000000001";
const kinds = ["transcript", "beat", "cue", "marker"] as const;
function source(): Project {
  const project = structuredClone(fixtureProject);
  project.scenes = [{ ...project.scenes[0], beats: [{
    id: sharedId, label: "Current beat", objective: "Read current authored copy",
    start: { num: "0", den: "1" }, duration: { num: "1", den: "1" },
  }] }];
  project.audio.transcript = [{
    id: sharedId, voice_track_id: "selection-test-voice", text: "Current transcript",
    start: { num: "0", den: "1" }, end: { num: "1", den: "1" },
    speaker: null, alignment: { kind: "manual" },
  }];
  project.audio.cues = [{ id: sharedId, label: "Current cue",
    at: { num: "0", den: "1" }, source_segment_id: null, evidence: "manual" }];
  project.markers = [{ id: sharedId, label: "Current marker", at: { num: "0", den: "1" } }];
  return project;
}
const identity = (kind: TimelineSelection["kind"]): TimelineSelection => ({ kind, id: sharedId });

describe("timeline selection source and current resource identity", () => {
  it.each(["id", "generation", "active_branch"] as const)("changes the view scope when %s changes", field => {
    const project = source();
    const next = { ...project, [field]: project[field] + "-other" };
    expect(timelineSelectionScope(next)).not.toBe(timelineSelectionScope(project));
  });

  it("keeps the same view scope and selection through an ordinary revision", () => {
    const project = source();
    const next = { ...structuredClone(project), revision: project.revision + 1, title: "Revised project title" };
    expect(timelineSelectionScope(next)).toBe(timelineSelectionScope(project));
    for (const kind of kinds) {
      expect(resolveTimelineSelection(next, identity(kind)))
        .toStrictEqual(resolveTimelineSelection(project, identity(kind)));
    }
  });

  it("gives a foreign source a different view lifetime even when every lane reuses the same item IDs", () => {
    const project = source();
    const foreign = { ...structuredClone(project), id: project.id + "-foreign" };
    expect(foreign.scenes[0].beats[0].id).toBe(project.scenes[0].beats[0].id);
    expect(foreign.audio.transcript[0].id).toBe(project.audio.transcript[0].id);
    expect(foreign.audio.cues[0].id).toBe(project.audio.cues[0].id);
    expect(foreign.markers[0].id).toBe(project.markers[0].id);
    // App keys Timeline by this scope and therefore drops its old local state.
    // The resource resolver alone cannot infer the origin of a bare {kind,id}.
    expect(timelineSelectionScope(foreign)).not.toBe(timelineSelectionScope(project));
  });

  it("resolves renamed labels and transcript text from the current revision", () => {
    const project = source();
    const next = structuredClone(project);
    next.revision++;
    next.scenes[0].beats[0].label = "Renamed beat";
    next.audio.transcript[0].text = "Revised transcript text";
    next.audio.cues[0].label = "Renamed cue";
    next.markers[0].label = "Renamed marker";
    expect(timelineSelectionScope(next)).toBe(timelineSelectionScope(project));
    for (const [kind, label] of [
      ["beat", "Renamed beat"], ["transcript", "Revised transcript text"],
      ["cue", "Renamed cue"], ["marker", "Renamed marker"],
    ] as const) {
      expect(resolveTimelineSelection(next, identity(kind))).toStrictEqual({ kind, id: sharedId, label });
      expect(resolveTimelineSelection(project, identity(kind))?.label).not.toBe(label);
    }
  });

  it("keeps lane kinds distinct when identifiers coincide", () => {
    const project = source();
    for (const [kind, label] of [
      ["transcript", "Current transcript"], ["beat", "Current beat"],
      ["cue", "Current cue"], ["marker", "Current marker"],
    ] as const) {
      expect(resolveTimelineSelection(project, identity(kind))).toStrictEqual({ kind, id: sharedId, label });
    }
  });

  it.each(kinds)("a removed %s does not resolve an identically named ID from another lane", kind => {
    const project = source();
    const next = structuredClone(project);
    next.revision++;
    if (kind === "beat") next.scenes[0].beats = [];
    else if (kind === "transcript") next.audio.transcript = [];
    else if (kind === "cue") next.audio.cues = [];
    else next.markers = [];
    expect(resolveTimelineSelection(next, identity(kind))).toBeNull();
    for (const other of kinds.filter(item => item !== kind)) {
      expect(resolveTimelineSelection(next, identity(other))).not.toBeNull();
    }
  });

  it("returns no selection for an empty selection or an unavailable ID", () => {
    const project = source();
    expect(resolveTimelineSelection(project, null)).toBeNull();
    for (const kind of kinds) {
      expect(resolveTimelineSelection(project, { kind, id: "unavailable-item" })).toBeNull();
    }
  });

  it("does not mutate the project or the captured selection identities", () => {
    const project = source();
    const before = structuredClone(project);
    const identities = kinds.map(identity);
    const identitiesBefore = structuredClone(identities);
    timelineSelectionScope(project);
    for (const selected of identities) resolveTimelineSelection(project, selected);
    expect(project).toStrictEqual(before);
    expect(identities).toStrictEqual(identitiesBefore);
  });
});
