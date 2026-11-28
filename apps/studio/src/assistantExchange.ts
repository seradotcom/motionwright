import { createScopedDraft, encodeScopedDraft, nodeTransform, queueDraftEdit, selectDraftNode } from "./scopedChangeDraft";
import type { ScopedChangeDraft } from "./scopedChangeDraft";
import type { CanvasTransform, Project, Scene } from "./types";

const MAX_EXCHANGE_BYTES = 128 * 1024;
const transformFields = ["x", "y", "width", "height", "rotation_deg", "opacity"] as const;

function sourceFor(project: Project, scene: Scene) {
  return {
    project_id: project.id, generation: project.generation, branch_id: project.active_branch,
    revision: project.revision, scene_id: scene.id,
  };
}

function record(value: unknown, keys: readonly string[], label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).some(key => !keys.includes(key))) {
    throw new Error(label + " contains unsupported fields.");
  }
  return value as Record<string, unknown>;
}

function byteLength(value: string) {
  return new TextEncoder().encode(value).byteLength;
}

export function createAssistantRequest(project: Project, scene: Scene, nodeId: string | null, playhead: number, intention: string) {
  const scope = scene.nodes.filter(node => nodeId === null || node.id === nodeId);
  if (!scope.length) throw new Error("Choose a scope with at least one scene object.");
  const context = {
    schema: "motionwright.assistant-request/1",
    source: sourceFor(project, scene),
    intention: intention.trim(),
    playhead_seconds: playhead,
    scene: { name: scene.name, objective: scene.objective },
    scope: scope.map(node => ({
      id: node.id, name: node.name, kind: node.kind, text: node.text,
      transform: nodeTransform(node), property_locks: node.property_locks,
    })),
    response_contract: {
      schema: "motionwright.assistant-proposal/1",
      source: "Copy the source object exactly.",
      rationale: "Describe the visible reason for these edits.",
      edits: "Use {kind: text, node_id, text} or {kind: transform, node_id, transform} with all six transform fields. Edit only objects in scope.",
    },
  };
  if (byteLength(intention) > 4000) throw new Error("Keep the editing instruction within 4000 UTF-8 bytes.");
  const raw = JSON.stringify(context, null, 2);
  if (byteLength(raw) > MAX_EXCHANGE_BYTES) throw new Error("The request exceeds 128 KiB. Choose an individual object.");
  return raw;
}

function readTransform(raw: unknown): CanvasTransform {
  const transform = record(raw, transformFields, "Transform");
  if (!transformFields.every(key => typeof transform[key] === "number" && Number.isFinite(transform[key]))) {
    throw new Error("Every transform field must be a finite number.");
  }
  const next = transform as unknown as CanvasTransform;
  if (next.width <= 0 || next.height <= 0 || next.opacity < 0 || next.opacity > 1) {
    throw new Error("Size must be positive and opacity must be between zero and one.");
  }
  return nodeTransform(next);
}

export function readAssistantProposal(raw: string, project: Project, scene: Scene, nodeId: string | null): ScopedChangeDraft {
  if (byteLength(raw) > MAX_EXCHANGE_BYTES) throw new Error("The proposal exceeds 128 KiB.");
  let decoded: unknown;
  try { decoded = JSON.parse(raw); }
  catch { throw new Error("Paste the complete proposal as valid JSON."); }
  const proposal = record(decoded, ["schema", "source", "rationale", "edits"], "Proposal");
  if (proposal.schema !== "motionwright.assistant-proposal/1") throw new Error("Unsupported proposal schema.");
  const expected = sourceFor(project, scene);
  const source = record(proposal.source, Object.keys(expected), "Source");
  if (Object.entries(expected).some(([key, value]) => source[key] !== value)) {
    throw new Error("This proposal belongs to a different project, branch, scene or revision. Export the current context and request a new proposal.");
  }
  if (typeof proposal.rationale !== "string" || !proposal.rationale.trim() || byteLength(proposal.rationale) > 4000) {
    throw new Error("Enter a proposal reason of 1–4000 UTF-8 bytes.");
  }
  if (!Array.isArray(proposal.edits) || !proposal.edits.length || proposal.edits.length > 128) {
    throw new Error("A proposal must contain 1–128 edits.");
  }

  const base = createScopedDraft(project, scene);
  const nodes = new Map(scene.nodes.filter(node => nodeId === null || node.id === nodeId).map(node => [node.id, node]));
  const seen = new Set<string>();
  let draft = { ...base, rationale: proposal.rationale.trim() };
  for (const item of proposal.edits) {
    const edit = record(item, ["kind", "node_id", "text", "transform"], "Edit");
    const node = typeof edit.node_id === "string" ? nodes.get(edit.node_id) : undefined;
    if (!node) throw new Error("A proposed object is outside the selected scope.");
    if (edit.kind !== "text" && edit.kind !== "transform") throw new Error("Only text and transform edits are supported.");
    const key = JSON.stringify([node.id, edit.kind]);
    if (seen.has(key)) throw new Error("The proposal repeats a property of the same object.");
    seen.add(key);
    const editor = selectDraftNode(base, node).editor;
    if (edit.kind === "text") {
      if ("transform" in edit || typeof edit.text !== "string" || edit.text.length > 8000) {
        throw new Error("A text edit requires replacement text of at most 8000 characters.");
      }
      draft = queueDraftEdit({ ...draft, editor: { ...editor, kind: "text", text: edit.text } });
    } else {
      if ("text" in edit) throw new Error("Put text and transform changes in separate edits.");
      draft = queueDraftEdit({ ...draft, editor: { ...editor, kind: "transform", transform: readTransform(edit.transform) } });
    }
  }
  // Imported values belong to the queue. Removing an edit should not leave a second copy as unqueued text or geometry.
  const selected = selectDraftNode(base, nodes.get(draft.editor.nodeId));
  draft = { ...draft, editor: selected.editor };
  encodeScopedDraft(draft);
  return draft;
}
