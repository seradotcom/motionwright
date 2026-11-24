import type { Project } from "./types";

export interface TimelineEditorRequest { nonce: number; scope: string; selection: TimelineSelection; sceneId: string | null }

export type TimelineSelection = {
  kind: "transcript" | "beat" | "cue" | "marker" | "object" | "camera" | "music" | "sfx" | "review";
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
    case "object": {
      const node = project.scenes.flatMap(scene=>scene.nodes).find(item=>item.id===selection.id);
      return node ? {...selection,label:node.name} : null;
    }
    case "camera": {
      const camera=project.production_design?.camera_reframes?.find(item=>item.id===selection.id);
      const scene=project.scenes.find(item=>item.id===camera?.scene_id);
      return camera && scene ? {...selection,label:scene.name+" camera"} : null;
    }
    case "music": {
      const profile=project.deliverables.find(item=>item.id===selection.id);
      const asset=project.assets.find(item=>item.id===project.production_design?.delivery_recipes?.[selection.id]?.original_mix?.music_asset_id);
      return profile && asset ? {...selection,label:asset.name} : null;
    }
    case "sfx": {
      const cue=project.audio.sfx_cues?.find(item=>item.id===selection.id);
      return cue ? {...selection,label:cue.event.replaceAll("_"," ")} : null;
    }
    case "review": {
      const review=project.reviews.find(item=>item.id===selection.id && item.anchor.branch_id===project.active_branch);
      return review ? {...selection,label:review.body} : null;
    }
    default:
      return null;
  }
}
