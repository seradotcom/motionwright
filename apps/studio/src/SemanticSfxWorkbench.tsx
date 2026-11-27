import type { Change, Project } from "./types";
import type { CraftSoundPreset } from "./craftLibrary";
import SemanticSfxEditor from "./SemanticSfxEditor";

// Existing Audio workspace entry point; Project receipts come from the shared mutation gate.
export default function SemanticSfxWorkbench({ project, commit, onSeek, craftPreset }: {
  project: Project; commit: (change: Change) => Promise<Project>; onSeek: (seconds: number) => void;
  craftPreset?: CraftSoundPreset;
}) {
  return <SemanticSfxEditor project={project} commit={commit} onSeek={onSeek} craftPreset={craftPreset} />;
}
