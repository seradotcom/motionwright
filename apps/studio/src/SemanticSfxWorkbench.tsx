import type { Change, Project } from "./types";
import SemanticSfxEditor from "./SemanticSfxEditor";

// Existing Audio workspace entry point; Project receipts come from the shared mutation gate.
export default function SemanticSfxWorkbench({ project, commit, onSeek }: {
  project: Project; commit: (change: Change) => Promise<Project>; onSeek: (seconds: number) => void;
}) {
  return <SemanticSfxEditor project={project} commit={commit} onSeek={onSeek} />;
}
