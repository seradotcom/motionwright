import { invoke } from "@tauri-apps/api/core";
import { graphSourceSnapshot, type GraphAuthoringContext, type SemanticGraphChange } from "./semanticGraph";
import { captureGraphRecoveryRequest, decodeGraphOperationRecovery, GRAPH_RECOVERY_READ_ERROR, type GraphOperationRecovery } from "./semanticGraphRecovery";
import type { Project } from "./types";

/**
 * Inspect the persisted, selected Project through the real desktop/service
 * adapter. The response is a read model, not an approval or reusable grant.
 * SemanticGraphWorkbench validates its exact source identity and complete DTO,
 * and keeps the draft blocked when native support is unavailable.
 */
export async function readSemanticGraphContext(
  project: Project,
  sceneId: string,
  profileId: string,
): Promise<GraphAuthoringContext> {
  if (!("__TAURI_INTERNALS__" in window)) {
    throw new Error("Graph source inspection requires the actual StudioService.");
  }
  return invoke<GraphAuthoringContext>("semantic_graph_authoring_context", {
    request: {
      source_snapshot: graphSourceSnapshot(project),
      scene_id: sceneId,
      variant_profile_ref: profileId,
    },
  });
}

/** Read the original operation without issuing a grant, mutation or replacement key. */
export async function readSemanticGraphOperationRecovery(
  requestId: string,
  change: SemanticGraphChange,
): Promise<GraphOperationRecovery> {
  try {
    if (!("__TAURI_INTERNALS__" in window)) throw new Error(GRAPH_RECOVERY_READ_ERROR);
    const original = captureGraphRecoveryRequest(requestId, change);
    const value: unknown = await invoke("semantic_graph_operation_recovery", { request: structuredClone(original) });
    return decodeGraphOperationRecovery(value, original.request_id, original.change);
  } catch {
    // Read failures carry no information about the outcome of the original write.
    throw new Error(GRAPH_RECOVERY_READ_ERROR);
  }
}
