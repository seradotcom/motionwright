import type { Bootstrap } from "../../src/types";

type SyntheticProjectRead = (command: string, args: unknown, fixture: Bootstrap) => unknown;

declare global {
  interface Window {
    __SYNTHETIC_PROJECT_READ__: SyntheticProjectRead;
  }
}

/** Install an explicit adapter; each caller supplies its current synthetic state.
 * The function is self-contained because Playwright serializes it into the page.
 * It neither intercepts Tauri nor stores or manufactures a selected Project.
 */
export function installSyntheticProjectReads(
  receiver: { __SYNTHETIC_PROJECT_READ__?: SyntheticProjectRead } = window,
): SyntheticProjectRead {
  const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
  const read: SyntheticProjectRead = (command, args, fixture) => {
    if (!["bootstrap", "project_projection_head", "open_project"].includes(command)) {
      throw new Error("Unsupported synthetic project read: " + command);
    }
    const project = fixture?.project;
    if (!project || !uuid.test(project.id) || !uuid.test(project.generation)
        || !Number.isSafeInteger(project.revision) || project.revision < 0
        || !Number.isSafeInteger(project.schema_version) || project.schema_version < 1
        || typeof project.title !== "string" || !Array.isArray(project.scenes)
        || !fixture.native_sdk) {
      throw new Error("Synthetic project reads require the caller's complete project identity.");
    }
    const request = typeof args === "object" && args !== null
      ? (args as { request?: unknown }).request : undefined;
    if (typeof request !== "object" || request === null
        || !Object.hasOwn(request, "project_id")) {
      throw new Error("Synthetic project reads require an explicit project_id.");
    }
    const projectId = (request as { project_id: unknown }).project_id;
    if (command === "bootstrap" && projectId === null) {
      return {
        project: null,
        native_sdk: structuredClone(fixture.native_sdk),
        project_selection: { status: "unselected" },
      };
    }
    if (typeof projectId !== "string" || !uuid.test(projectId) || projectId !== project.id) {
      throw new Error("Synthetic project read requires the explicitly selected matching project.");
    }
    if (command === "project_projection_head") {
      return {
        identity: { project_id: project.id, generation: project.generation, revision: String(project.revision) },
        schema_version: project.schema_version,
        title: project.title,
        scene_count: project.scenes.length,
      };
    }
    if (command === "open_project") return structuredClone(project);
    return {
      ...structuredClone(fixture),
      project_selection: { status: "selected", project_id: project.id },
    };
  };
  receiver.__SYNTHETIC_PROJECT_READ__ = read;
  return read;
}
