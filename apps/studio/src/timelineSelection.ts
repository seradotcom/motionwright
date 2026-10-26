import type { Project } from "./types";

export type TimelineSelection = {
  kind: "transcript" | "beat" | "cue" | "marker";
  id: string;
};

/** The Timeline owns its view selection within this source, across revisions. */
export function timelineSelectionScope(project: Pick<Project, "id" | "generation" | "active_branch">): string {
  return JSON.stringify([project.id, project.generation, project.active_branch]);
}

/** Resolve current copy and existence without retaining a second resource snapshot. */
export function resolveTimelineSelection(
  project: Project,
  selection: TimelineSelection | null,
): (TimelineSelection & { label: string }) | null {
  if (!selection) return null;
  switch (selection.kind) {
    case "transcript": {
      const segment = project.audio.transcript.find(item => item.id === selection.id);
      return segment ? { ...selection, label: segment.text } : null;
    }
    case "beat": {
      for (const scene of project.scenes) {
        const beat = scene.beats.find(item => item.id === selection.id);
        if (beat) return { ...selection, label: beat.label };
      }
      return null;
    }
    case "cue": {
      const cue = project.audio.cues.find(item => item.id === selection.id);
      return cue ? { ...selection, label: cue.label } : null;
    }
    case "marker": {
      const marker = project.markers.find(item => item.id === selection.id);
      return marker ? { ...selection, label: marker.label } : null;
    }
    default:
      return null;
  }
}
