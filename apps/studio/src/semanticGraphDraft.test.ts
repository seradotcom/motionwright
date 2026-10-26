import { describe, expect, it, vi } from "vitest";
import type { Project, ProjectEvent } from "./types";
import { fixtureBootstrap } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { graphSame, graphSourceSnapshot, graphToRaw, newSemanticGraph } from "./semanticGraph";
import type { GraphAuthoringContext, GraphProject, GraphRaw, SemanticGraphChange } from "./semanticGraph";
import { acknowledgeGraphDraft, assertGraphCustody, assertGraphRecoverySource, createGraphDraft, decodeGraphCopy, discardGraphCopy, editGraphDraft, encodeGraphCopy,
  failGraphSubmission, forkGraphCopy, graphCopyKey, graphDownload, graphOutcomeMatches, graphPointerKey, inspectGraphOutcome, mergeGraphDraft,
  prepareGraphSubmission, readGraphCopies, readGraphRecoverySource, reconcileGraphDraft, recoverGraphRead, releaseGraphOutcome, reviewGraphDraft, validateGraphRaw, writeGraphCopy } from "./semanticGraphDraft";
import type { GraphCustody, GraphDraft, GraphRecoveryObservation, GraphStorage } from "./semanticGraphDraft";

class Memory implements GraphStorage {
  data = new Map<string, string>(); reads = 0; writes = 0; drop = false; corrupt = false;
  onRead: ((key: string, count: number) => void) | null = null;
  get length() { return this.data.size; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  getItem(key: string) { this.onRead?.(key, ++this.reads); return this.data.get(key) ?? null; }
  setItem(key: string, raw: string) { this.writes++; if (!this.drop) this.data.set(key, this.corrupt ? raw + "corrupt" : raw); }
  removeItem(key: string) { this.data.delete(key); }
}
function source(saved = true) {
  const project = structuredClone(fixtureBootstrap.project) as GraphProject; project.locks = []; project.production_design = emptyProductionDesign();
  const scene = project.scenes[0], profile = project.deliverables[0]; scene.duration = { num: "5", den: "1" }; profile.frame_rate = { num: "30", den: "1" }; profile.included_scene_ids = [];
  const graph = newSemanticGraph(project, scene, profile, "causal_diagram"); graph.authorship.author_label = "Synthetic test author"; graph.authorship.rationale = "Explicit helper fixture";
  if (saved) project.production_design.semantic_graphs = [structuredClone(graph)];
  const context: GraphAuthoringContext = { schema: "motionwright.semantic-graph-authoring-context/1", source_snapshot: graphSourceSnapshot(project), scene_id: scene.id, variant_profile_ref: profile.id,
    component_version: 1, native_profile: "mw-manim-semantic-graph-v1", font_weight: 400, font_selection: { primary_family: "Instrument Sans Variable", mono_family: "IBM Plex Mono" }, availability: "ready", blocking_reasons: [], dependencies: [] };
  return { project, graph, draft: createGraphDraft(project, graph), context };
}
function change(draft: GraphDraft, path: string, edit: (raw: GraphRaw) => void) {
  const raw = structuredClone(draft.input); edit(raw); return editGraphDraft(draft, raw, [path]);
}
/** Test-only exact scheduling response; never a product service implementation. */
function syntheticReceipt(project: Project, submitted: SemanticGraphChange): GraphProject {
  const next = structuredClone(project) as GraphProject; next.revision++;
  const graphs = next.production_design?.semantic_graphs ?? [];
  next.production_design ??= emptyProductionDesign();
  next.production_design.semantic_graphs = submitted.type === "remove_semantic_graph" ? graphs.filter(g => g.id !== submitted.graph_id)
    : [...graphs.filter(g => g.id !== submitted.graph.id), structuredClone(submitted.graph)];
  return next;
}
function copy(draft: GraphDraft) { const local = new Memory(), pointers = new Memory(), custody: GraphCustody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: null }; writeGraphCopy(local, pointers, draft, custody); return { local, pointers, custody }; }

describe("Graph stable-identity draft reconciliation", () => {
  it("retains incomplete geometry and rational bytes through the exact recovery codec", () => {
    const { draft } = source(); const next = change(draft, "/content/vertices/" + draft.input.content.vertices[0].id + "/box/x", raw => { raw.content.vertices[0].box.x = "1e"; raw.time_range.start = { num: "-", den: "" }; });
    const { local, custody } = copy(next), recovered = decodeGraphCopy(local.getItem(graphCopyKey(next.scope, custody.writerId))!, next.scope, custody.writerId).draft;
    expect(recovered.input).toEqual(next.input); expect(recovered.input.content.vertices[0].box.x).toBe("1e"); expect(recovered.input.time_range.start).toEqual({ num: "-", den: "" });
    expect(JSON.parse(graphDownload(next)).draft.input).toEqual(next.input);
  });
  it("merges an unrelated peer edge while keeping a local vertex edit", () => {
    const { project, draft } = source(); const vertexId = draft.input.content.vertices[0].id;
    const local = change(draft, "/content/vertices/" + vertexId + "/label", raw => { raw.content.vertices[0].label = "Human concept"; });
    const peer = structuredClone(project); peer.revision++; peer.production_design!.semantic_graphs![0].content.edges[0].label = "Peer relation";
    const merged = mergeGraphDraft(local, peer); expect(merged.conflicts).toEqual([]); expect(merged.input.content.vertices[0].label).toBe("Human concept"); expect(merged.input.content.edges[0].label).toBe("Peer relation");
    const next = reviewGraphDraft(local, peer); expect(next.baseRevision).toBe(peer.revision); expect(next.input).toEqual(merged.input);
  });
  it("preserves peer additions and ignores collection order as identity", () => {
    const { project, draft } = source(); const a = draft.input.content.vertices[0], b = draft.input.content.vertices[1];
    const local = change(draft, "/content/vertices/" + a.id + "/meaning", raw => { raw.content.vertices[0].meaning = "Later author meaning"; });
    const peer = structuredClone(project); peer.revision++; const graph = peer.production_design!.semantic_graphs![0]; graph.content.vertices.reverse();
    graph.content.vertices.push({ ...structuredClone(graph.content.vertices[0]), id: crypto.randomUUID(), label: "Peer addition" });
    const merged = mergeGraphDraft(local, peer); expect(merged.conflicts).toEqual([]);
    expect(merged.input.content.vertices.map(v => v.id).slice(0, 2)).toEqual([b.id, a.id]); expect(merged.input.content.vertices.find(v => v.id === a.id)?.meaning).toBe("Later author meaning"); expect(merged.input.content.vertices[2].label).toBe("Peer addition");
  });
  it("reports only a real overlap, including a deliberate return to the base value", () => {
    const { project, draft } = source(); const id = draft.input.content.vertices[0].id, path = "/content/vertices/" + id + "/label", base = draft.input.content.vertices[0].label;
    let local = change(draft, path, raw => { raw.content.vertices[0].label = "Intermediate"; }); local = change(local, path, raw => { raw.content.vertices[0].label = base; });
    const peer = structuredClone(project); peer.revision++; peer.production_design!.semantic_graphs![0].content.vertices[0].label = "Peer value";
    const merged = mergeGraphDraft(local, peer); expect(merged.conflicts.map(c => c.path)).toEqual([path]); expect(merged.input.content.vertices[0].label).toBe(base);
    expect(() => reviewGraphDraft(local, peer)).toThrow(/Resolve/);
    const next = reviewGraphDraft(local, peer, { [path]: "current" }); expect(next.input.content.vertices[0].label).toBe("Peer value"); expect(next.edited[path]).toBeUndefined();
  });
  it("retains local input when a peer removes its vertex or the whole Graph", () => {
    const { project, draft } = source(); const id = draft.input.content.vertices[0].id;
    const local = change(draft, "/content/vertices/" + id + "/label", raw => { raw.content.vertices[0].label = "Keep this concept"; });
    const peer = structuredClone(project); peer.revision++; peer.production_design!.semantic_graphs![0].content.vertices.shift(); peer.production_design!.semantic_graphs![0].content.edges = [];
    expect(mergeGraphDraft(local, peer).conflicts.map(c => c.path)).toEqual(["/content/vertices/" + id]);
    peer.production_design!.semantic_graphs = []; expect(mergeGraphDraft(local, peer).conflicts[0].path).toBe(""); expect(local.input.content.vertices[0].label).toBe("Keep this concept");
  });
  it("never reconciles active UNKNOWN or recovered NOT_SENT from incoming props", async () => {
    const { project, draft, context } = source(); const prepared = await prepareGraphSubmission(draft, project, context, crypto.randomUUID()); const receipt = syntheticReceipt(project, prepared.change);
    expect(reconcileGraphDraft(prepared.draft, receipt)).toBe(prepared.draft);
    const notSent = failGraphSubmission(prepared.draft, prepared.draft.pending!.id, "No callback", true), retained = copy(notSent);
    const recovered = decodeGraphCopy(retained.custody.expectedRaw!, draft.scope, retained.custody.writerId).draft;
    expect(recovered.pending?.outcome).toBe("UNKNOWN"); expect(reconcileGraphDraft(recovered, receipt)).toBe(recovered);
  });
  it("rejects project, generation, branch and scene migration before mutation preparation", async () => {
    const { project, draft, context } = source();
    for (const field of ["id", "generation", "active_branch"] as const) await expect(prepareGraphSubmission(draft, { ...project, [field]: crypto.randomUUID() }, context, crypto.randomUUID())).rejects.toThrow(/scope/);
    const raw = structuredClone(draft.input); raw.scene_id = crypto.randomUUID(); expect(() => editGraphDraft(draft, raw)).toThrow(/identity/);
  });
});

describe("Graph original Promise acknowledgement and reviewed successor", () => {
  it("retains later edits in the submitted field and a return to its old base", async () => {
    const { project, draft, context } = source(); const id = draft.input.content.vertices[0].id, path = "/content/vertices/" + id + "/label", base = draft.input.content.vertices[0].label;
    const own = change(draft, path, raw => { raw.content.vertices[0].label = "Sent label"; }); const prepared = await prepareGraphSubmission(own, project, context, crypto.randomUUID());
    const later = change(prepared.draft, path, raw => { raw.content.vertices[0].label = base; raw.content.vertices[1].meaning = "Further human input"; });
    const acknowledged = acknowledgeGraphDraft(later, prepared.draft.pending!.id, syntheticReceipt(project, prepared.change));
    expect(acknowledged.pending).toBeNull(); expect(acknowledged.input.content.vertices[0].label).toBe(base); expect(acknowledged.input.content.vertices[1].meaning).toBe("Further human input"); expect(acknowledged.edited[path]).toBeGreaterThan(prepared.draft.pending!.sequence);
  });
  it.each(["foreign project", "foreign generation", "foreign branch", "later revision", "different Graph", "different request"])("refuses %s as an original ACK", async kind => {
    const { project, draft, context } = source(), prepared = await prepareGraphSubmission(draft, project, context, crypto.randomUUID()), receipt = syntheticReceipt(project, prepared.change); let id = prepared.draft.pending!.id;
    if (kind === "foreign project") receipt.id = crypto.randomUUID(); if (kind === "foreign generation") receipt.generation = crypto.randomUUID(); if (kind === "foreign branch") receipt.active_branch = crypto.randomUUID();
    if (kind === "later revision") receipt.revision++; if (kind === "different Graph") receipt.production_design!.semantic_graphs![0].content.vertices[0].label = "Different"; if (kind === "different request") id = crypto.randomUUID();
    expect(() => acknowledgeGraphDraft(prepared.draft, id, receipt)).toThrow(); expect(prepared.draft.pending?.outcome).toBe("UNKNOWN");
  });
  it("reads current Project twice and treats matching history as context, not ACK", async () => {
    const { project, draft, context } = source(), prepared = await prepareGraphSubmission(draft, project, context, crypto.randomUUID()), receipt = syntheticReceipt(project, prepared.change); let reads = 0;
    const review = await inspectGraphOutcome(prepared.draft, receipt, { readProject: async () => { reads++; return structuredClone(receipt); }, readHistory: async () => [] });
    expect(reads).toBe(2); expect(prepared.draft.pending?.outcome).toBe("UNKNOWN"); expect(graphOutcomeMatches(prepared.draft, receipt, review)).toBe(true);
    const next = releaseGraphOutcome(prepared.draft, receipt, review); expect(next.pending).toBeNull(); expect(next.baseRevision).toBe(receipt.revision);
  });
  it("rebases later edits after reviewed saved equality without inventing an ACK", async () => {
    const { project, draft, context } = source(); const path = "/content/vertices/" + draft.input.content.vertices[0].id + "/label";
    const input = change(draft, path, raw => { raw.content.vertices[0].label = "Sent"; }), prepared = await prepareGraphSubmission(input, project, context, crypto.randomUUID());
    const later = change(prepared.draft, path, raw => { raw.content.vertices[0].label = "After request"; }), receipt = syntheticReceipt(project, prepared.change);
    const review = await inspectGraphOutcome(later, receipt, { readProject: async () => structuredClone(receipt), readHistory: async () => [] });
    const successor = releaseGraphOutcome(later, receipt, review); expect(successor.input.content.vertices[0].label).toBe("After request"); expect(successor.base).toEqual(graphToRaw(receipt.production_design!.semantic_graphs![0]));
    expect(mergeGraphDraft(successor, receipt).conflicts).toEqual([]); expect(later.pending).not.toBeNull();
  });
  it("retains actual third-value CAS conflicts through an uncertain-outcome review", async () => {
    const { project, draft, context } = source(), path = "/content/vertices/" + draft.input.content.vertices[0].id + "/label";
    const local = change(draft, path, raw => { raw.content.vertices[0].label = "Local"; }), prepared = await prepareGraphSubmission(local, project, context, crypto.randomUUID());
    const peer = structuredClone(project); peer.revision++; peer.production_design!.semantic_graphs![0].content.vertices[0].label = "Peer";
    const review = await inspectGraphOutcome(prepared.draft, peer, { readProject: async () => structuredClone(peer), readHistory: async () => [] });
    const next = releaseGraphOutcome(prepared.draft, peer, review); expect(mergeGraphDraft(next, peer).conflicts.map(item => item.path)).toEqual([path]); expect(next.input.content.vertices[0].label).toBe("Local");
  });
  it("invalidates asynchronous outcome inspection after input or source changes", async () => {
    const { project, draft, context } = source(), prepared = await prepareGraphSubmission(draft, project, context, crypto.randomUUID());
    const review = await inspectGraphOutcome(prepared.draft, project, { readProject: async () => structuredClone(project), readHistory: async () => [] });
    const changed = change(prepared.draft, "/authorship/rationale", raw => { raw.authorship.rationale = "New input"; }); expect(graphOutcomeMatches(changed, project, review)).toBe(false); expect(() => releaseGraphOutcome(changed, project, review)).toThrow();
    let reads = 0; await expect(inspectGraphOutcome(prepared.draft, project, { readProject: async () => ({ ...structuredClone(project), revision: project.revision + (reads++ ? 1 : 0) }), readHistory: async () => [] })).rejects.toThrow(/changed/);
  });
  it("rejects history outside the inspected range and does not silently truncate to accept it", async () => {
    const { project, draft, context } = source(), prepared = await prepareGraphSubmission(draft, project, context, crypto.randomUUID());
    await expect(inspectGraphOutcome(prepared.draft, project, { readProject: async () => structuredClone(project), readHistory: async () => [{ revision: project.revision + 1 } as ProjectEvent] })).rejects.toThrow(/outside/);
  });
});

describe("Graph verified copy-and-pointer custody", () => {
  it.each(["data drop", "data corruption", "pointer drop"])("blocks dispatch custody after %s", mode => {
    const { draft } = source(false), local = new Memory(), pointers = new Memory(), custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: null };
    if (mode === "data drop") local.drop = true; if (mode === "data corruption") local.corrupt = true; if (mode === "pointer drop") pointers.drop = true;
    expect(() => writeGraphCopy(local, pointers, draft, custody)).toThrow(/not confirmed/); expect(() => assertGraphCustody(local, pointers, draft, custody)).toThrow();
  });
  it("does not overwrite a changed copy or foreign pointer", () => {
    const { draft } = source(), { local, pointers, custody } = copy(draft), key = graphCopyKey(draft.scope, custody.writerId);
    local.data.set(key, "Other writer bytes"); expect(() => writeGraphCopy(local, pointers, draft, custody)).toThrow(/Another runtime/); expect(local.getItem(key)).toBe("Other writer bytes");
    local.data.set(key, custody.expectedRaw!); const foreign = crypto.randomUUID(); pointers.data.set(graphPointerKey(draft.scope), foreign);
    expect(() => writeGraphCopy(local, pointers, draft, custody)).toThrow(/pointer/); expect(pointers.getItem(graphPointerKey(draft.scope))).toBe(foreign);
  });
  it("rechecks copy and pointer after storage enumeration can interleave a peer", () => {
    const { draft } = source(), { local, pointers, custody } = copy(draft), key = graphCopyKey(draft.scope, custody.writerId);
    local.data.set("motionwright.semantic-graph-recovery.v1:peer:record", "peer"); let hit = false;
    local.onRead = readKey => { if (readKey.includes(":peer:") && !hit) { hit = true; local.data.set(key, "Peer replacement"); } };
    expect(() => writeGraphCopy(local, pointers, draft, custody)).toThrow(/changed/); expect(local.data.get(key)).toBe("Peer replacement");
  });
  it("forks an uncertain attempt after an inspected peer revision without altering its original bytes", async () => {
    const { project, draft, context } = source(), prepared = await prepareGraphSubmission(draft, project, context, crypto.randomUUID());
    const peer = structuredClone(project); peer.revision++; peer.title = "Independent peer source";
    const branch = peer.branches.find(item => item.id === peer.active_branch);
    if (!branch) throw new Error("The peer source has no active branch");
    branch.head_revision = peer.revision;
    expect(branch.head_revision).toBe(project.revision + 1);
    const clock = vi.spyOn(Date, "now").mockReturnValue(1000);
    try {
      const held = copy(prepared.draft), oldRaw = held.custody.expectedRaw;
      const review = await inspectGraphOutcome(prepared.draft, peer, { readProject: async () => structuredClone(peer),
        readHistory: async () => [{ revision: peer.revision, change: { type: "rename_project", title: peer.title }, created_at: "2026-10-26T00:00:00Z" }] });
      const next = releaseGraphOutcome(prepared.draft, peer, review);
      clock.mockReturnValue(2000);
      // The UI verifies the current backup before it forks a new intention.
      writeGraphCopy(held.local, held.pointers, structuredClone(prepared.draft), held.custody);
      expect(held.custody.expectedRaw).toBe(oldRaw);
      const writer = forkGraphCopy(held.local, held.pointers, prepared.draft, next, held.custody, crypto.randomUUID());
      expect(held.local.getItem(graphCopyKey(draft.scope, held.custody.writerId))).toBe(oldRaw);
      expect(decodeGraphCopy(oldRaw!, draft.scope, held.custody.writerId).draft.pending?.outcome).toBe("UNKNOWN");
      expect(writer.writerId).not.toBe(held.custody.writerId); assertGraphCustody(held.local, held.pointers, next, writer);
    } finally { clock.mockRestore(); }
  });
  it("retains the exact original copy when the ACK custody fork fails", async () => {
    const { project, draft, context } = source(), prepared = await prepareGraphSubmission(draft, project, context, crypto.randomUUID()), held = copy(prepared.draft), oldRaw = held.custody.expectedRaw;
    const acknowledged = acknowledgeGraphDraft(prepared.draft, prepared.draft.pending!.id, syntheticReceipt(project, prepared.change)); held.local.drop = true;
    expect(() => forkGraphCopy(held.local, held.pointers, prepared.draft, acknowledged, held.custody, crypto.randomUUID())).toThrow(/not confirmed/);
    expect(held.local.getItem(graphCopyKey(draft.scope, held.custody.writerId))).toBe(oldRaw); expect(decodeGraphCopy(oldRaw!, draft.scope, held.custody.writerId).draft.pending?.outcome).toBe("UNKNOWN");
  });
  it("refuses to fork a reconciled snapshot over an exact original UNKNOWN copy", async () => {
    const { project, draft, context } = source(), prepared = await prepareGraphSubmission(draft, project, context, crypto.randomUUID()), held = copy(prepared.draft);
    const changed = { ...prepared.draft, baseRevision: project.revision + 1 }; expect(() => forkGraphCopy(held.local, held.pointers, changed, { ...changed, pending: null }, held.custody, crypto.randomUUID())).toThrow(/exact/);
  });
  it.each(["bad JSON", "unknown fields", "wrong owner", "wrong pending source"])("retains and rejects %s", async kind => {
    const { project, draft, context } = source(), prepared = await prepareGraphSubmission(draft, project, context, crypto.randomUUID()), held = copy(prepared.draft), original = held.custody.expectedRaw!;
    let raw = original; const value = JSON.parse(original);
    if (kind === "bad JSON") raw = "{"; if (kind === "unknown fields") { value.draft.input.python = "import os"; raw = JSON.stringify(value); }
    if (kind === "wrong owner") { value.scope = "[]"; raw = JSON.stringify(value); }
    if (kind === "wrong pending source") { value.draft.pending.submitted.graph.authorship.source_snapshot.generation = crypto.randomUUID(); raw = JSON.stringify(value); }
    held.local.data.set(graphCopyKey(draft.scope, held.custody.writerId), raw); expect(() => decodeGraphCopy(raw, draft.scope, held.custody.writerId)).toThrow();
    expect(readGraphCopies(held.local, draft.scope).entries[0]).toMatchObject({ raw, value: null }); expect(held.local.getItem(graphCopyKey(draft.scope, held.custody.writerId))).toBe(raw);
  });
  it("does not discard a selected copy when its raw bytes or pointer change during review", () => {
    const { draft } = source(), held = copy(draft), other = crypto.randomUUID(), key = graphCopyKey(draft.scope, other);
    const raw = encodeGraphCopy({ schema: "motionwright.semantic-graph-recovery/1", scope: draft.scope, writerId: other, updatedAt: 1, draft }); held.local.data.set(key, raw);
    const entry = readGraphCopies(held.local, draft.scope).entries.find(item => item.writerId === other)!;
    held.local.onRead = currentKey => { if (currentKey === key) held.pointers.data.set(graphPointerKey(draft.scope), other); };
    expect(() => discardGraphCopy(held.local, held.pointers, draft.scope, entry, held.custody.writerId)).toThrow(/pointer/); expect(held.local.data.get(key)).toBe(raw);
  });
  it("requires the correct closed raw schema without turning incomplete input into a product value", () => {
    const { draft } = source(); draft.input.style.font_size = "1e"; expect(() => validateGraphRaw(draft.input)).not.toThrow();
    const bad = structuredClone(draft.input); Object.assign(bad.content.edges[0], { execute: "anything" }); expect(() => validateGraphRaw(bad)).toThrow();
    expect(graphSame(draft.input, JSON.parse(graphDownload(draft)).draft.input)).toBe(true);
  });
});


const unknownRead = (): GraphRecoveryObservation => ({ pointerObserved: false, pointer: null, rawObserved: false, raw: null });
describe("Graph failed first read retains original authority and explicit fields", () => {
  it("distinguishes unreadable from observed null and confirms a fresh raw draft without dispatch", () => {
    const { draft } = source(false), local = new Memory(), pointers = new Memory(), observed = unknownRead();
    pointers.onRead = () => { throw new Error("first pointer read failed"); };
    expect(() => readGraphRecoverySource(local, pointers, draft.scope, observed)).toThrow(/first pointer/);
    expect(observed.pointerObserved).toBe(false); expect(observed.pointer).toBeNull();
    const later = change(draft, "/style/font_size", raw => { raw.style.font_size = "1e"; });
    pointers.onRead = null; expect(readGraphRecoverySource(local, pointers, draft.scope, observed)).toBeNull();
    expect(observed.pointerObserved).toBe(true); expect(local.writes).toBe(0); expect(pointers.writes).toBe(0);
    const custody: GraphCustody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: observed.pointer };
    assertGraphRecoverySource(local, pointers, draft.scope, observed); writeGraphCopy(local, pointers, later, custody);
    expect(decodeGraphCopy(custody.expectedRaw!, later.scope, custody.writerId).draft.input.style.font_size).toBe("1e");
    expect(later.pending).toBeNull(); assertGraphCustody(local, pointers, later, custody);
  });
  it("does not adopt a new writer after null was already observed", () => {
    const { draft } = source(false), local = new Memory(), pointers = new Memory(), observed = unknownRead();
    expect(readGraphRecoverySource(local, pointers, draft.scope, observed)).toBeNull();
    const peer = crypto.randomUUID(); pointers.data.set(graphPointerKey(draft.scope), peer);
    expect(() => readGraphRecoverySource(local, pointers, draft.scope, observed)).toThrow(/changed the earlier Graph pointer/);
    expect(observed.pointer).toBeNull(); expect(pointers.data.get(graphPointerKey(draft.scope))).toBe(peer); expect(local.writes).toBe(0);
  });
  it("retains known W when its first data read fails and W2 appears before Retry", async () => {
    const { project, draft, context } = source(), sent = await prepareGraphSubmission(draft, project, context, crypto.randomUUID());
    const held = copy(sent.draft), observed = unknownRead(), original = held.custody.expectedRaw!, originalWriter = held.custody.writerId;
    held.local.onRead = () => { throw new Error("first data read failed"); };
    expect(() => readGraphRecoverySource(held.local, held.pointers, draft.scope, observed)).toThrow(/first data/);
    expect(observed).toMatchObject({ pointerObserved: true, pointer: originalWriter, rawObserved: false });
    held.local.onRead = null; const peer = crypto.randomUUID(); held.pointers.data.set(graphPointerKey(draft.scope), peer);
    const later = change(draft, "/authorship/rationale", raw => { raw.authorship.rationale = "Later text"; });
    expect(() => readGraphRecoverySource(held.local, held.pointers, draft.scope, observed)).toThrow(/changed the earlier Graph pointer/);
    expect(observed.pointer).toBe(originalWriter); expect(later.input.authorship.rationale).toBe("Later text");
    expect(held.local.data.get(graphCopyKey(draft.scope, originalWriter))).toBe(original);
    expect(decodeGraphCopy(original, draft.scope, originalWriter).draft.pending?.outcome).toBe("UNKNOWN");
    expect(held.pointers.data.get(graphPointerKey(draft.scope))).toBe(peer); expect(held.local.writes).toBe(1);
  });
  it("rejects replacement bytes at the same writer during verification and on a later Retry", () => {
    const { draft } = source(), held = copy(draft), observed = unknownRead(), key = graphCopyKey(draft.scope, held.custody.writerId), original = held.custody.expectedRaw!;
    let reads = 0; held.local.onRead = readKey => { if (readKey === key && ++reads === 2) held.local.data.set(key, original + "changed"); };
    expect(() => readGraphRecoverySource(held.local, held.pointers, draft.scope, observed)).toThrow(/original Graph recovery bytes/);
    expect(observed.raw).toBe(original); held.local.onRead = null;
    expect(() => readGraphRecoverySource(held.local, held.pointers, draft.scope, observed)).toThrow(/bytes changed/);
    expect(held.local.data.get(key)).toBe(original + "changed"); expect(held.local.writes).toBe(1);
  });
  it("rejects pointer changes while the original data are being checked", () => {
    const { draft } = source(), held = copy(draft), observed = unknownRead(), original = held.custody.expectedRaw!;
    const peer = crypto.randomUUID(); held.local.onRead = () => { held.pointers.data.set(graphPointerKey(draft.scope), peer); };
    expect(() => readGraphRecoverySource(held.local, held.pointers, draft.scope, observed)).toThrow(/pointer changed/);
    expect(observed.pointer).toBe(held.custody.writerId); expect(held.local.data.get(graphCopyKey(draft.scope, held.custody.writerId))).toBe(original);
    expect(held.pointers.data.get(graphPointerKey(draft.scope))).toBe(peer);
  });
  it("adopts the full untouched recovered input and UNKNOWN without replacing it with provisional defaults", async () => {
    const { project, draft, context } = source();
    const authored = change(draft, "/authorship/rationale", raw => { raw.authorship.rationale = "The original rationale"; raw.content.vertices[1].meaning = "The original second meaning"; });
    const sent = await prepareGraphSubmission(authored, project, context, crypto.randomUUID()), held = copy(sent.draft);
    const observed = unknownRead(), recovered = readGraphRecoverySource(held.local, held.pointers, draft.scope, observed)!;
    const next = recoverGraphRead(draft, recovered, []);
    expect(next.input).toEqual(authored.input); expect(next.pending).toEqual(sent.draft.pending); expect(next.base).toEqual(sent.draft.base);
    expect(held.local.data.get(graphCopyKey(draft.scope, held.custody.writerId))).toBe(held.custody.expectedRaw); expect(held.local.writes).toBe(1);
  });
  it.each(["incomplete", "empty", "ABA"])("overlays only %s field intent and keeps other fields plus the original UNKNOWN", async mode => {
    const { project, draft, context } = source(), path = "/style/font_size", originalFont = draft.input.style.font_size;
    const authored = change(draft, "/authorship/rationale", raw => { raw.authorship.rationale = "Recovered explanation"; raw.style.font_size = "31"; });
    const sent = await prepareGraphSubmission(authored, project, context, crypto.randomUUID()), held = copy(sent.draft), original = held.custody.expectedRaw!;
    let later = change(draft, path, raw => { raw.style.font_size = "intermediate"; });
    const wanted = mode === "incomplete" ? "1e" : mode === "empty" ? "" : originalFont;
    later = change(later, path, raw => { raw.style.font_size = wanted; });
    const recovered = readGraphRecoverySource(held.local, held.pointers, draft.scope, unknownRead())!, next = recoverGraphRead(later, recovered, [path]);
    expect(next.input.style.font_size).toBe(wanted); expect(next.input.authorship.rationale).toBe("Recovered explanation");
    expect(next.pending).toEqual(recovered.pending); expect(next.pending?.outcome).toBe("UNKNOWN"); expect(next.base).toEqual(recovered.base);
    expect(next.edited[path]).toBeGreaterThan(next.pending!.sequence); expect(next.edited["/authorship/rationale"]).toBe(recovered.edited["/authorship/rationale"]);
    expect(held.local.data.get(graphCopyKey(draft.scope, held.custody.writerId))).toBe(original);
    const fresh: GraphCustody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: held.custody.writerId };
    writeGraphCopy(held.local, held.pointers, next, fresh);
    expect(decodeGraphCopy(fresh.expectedRaw!, draft.scope, fresh.writerId).draft.input.style.font_size).toBe(wanted);
    expect(held.local.data.get(graphCopyKey(draft.scope, held.custody.writerId))).toBe(original);
  });
  it("merges child fields by stable ID while preserving recovered order and untouched children", () => {
    const { draft } = source(), id = draft.input.content.vertices[0].id, path = "/content/vertices/" + id + "/label";
    const recovered = change(draft, "/authorship/rationale", raw => { raw.authorship.rationale = "Recovered"; raw.content.vertices.reverse(); raw.content.vertices[0].meaning = "Untouched recovered meaning"; });
    const later = change(draft, path, raw => { raw.content.vertices[0].label = ""; });
    const next = recoverGraphRead(later, recovered, [path]);
    expect(next.input.content.vertices.map(v => v.id)).toEqual(recovered.input.content.vertices.map(v => v.id));
    expect(next.input.content.vertices.find(v => v.id === id)?.label).toBe(""); expect(next.input.content.vertices[0].meaning).toBe("Untouched recovered meaning");
  });
  it("retains a provisional child under its own ID instead of replacing a recovered child by position", () => {
    const { draft } = source(), provisional = structuredClone(draft), oldIds = draft.input.content.vertices.map(v => v.id), fresh = crypto.randomUUID();
    provisional.input.content.vertices[0].id = fresh;
    const path = "/content/vertices/" + fresh + "/label", later = change(provisional, path, raw => { raw.content.vertices[0].label = "Provisional explicit concept"; });
    const next = recoverGraphRead(later, draft, [path]);
    expect(next.input.content.vertices.map(v => v.id)).toEqual([...oldIds, fresh]);
    expect(next.input.content.vertices.slice(0, 2)).toEqual(draft.input.content.vertices); expect(next.input.content.vertices[2].label).toBe("Provisional explicit concept");
  });
  it("does not merge an independently pending request or an unrecorded edit marker", async () => {
    const { project, draft, context } = source(), a = await prepareGraphSubmission(draft, project, context, crypto.randomUUID()), b = await prepareGraphSubmission(draft, project, context, crypto.randomUUID());
    expect(() => recoverGraphRead(a.draft, b.draft, [])).toThrow(/Another unconfirmed/);
    expect(() => recoverGraphRead(draft, b.draft, ["/authorship/rationale"])).toThrow(/edit marker/);
    expect(a.draft.pending?.id).not.toBe(b.draft.pending?.id); expect(b.draft.pending?.outcome).toBe("UNKNOWN");
  });
});

describe("Graph outcome authority is the complete Project", () => {
  it("rejects a first read with the same stamp and different Project contents", async () => {
    const { project, draft, context } = source(), sent = await prepareGraphSubmission(draft, project, context, crypto.randomUUID());
    const changed = structuredClone(project); changed.scenes[0].name += " changed under same stamp"; let historyReads = 0;
    await expect(inspectGraphOutcome(sent.draft, project, { readProject: async () => changed, readHistory: async () => { historyReads++; return []; } })).rejects.toThrow(/currently selected exact source/);
    expect(historyReads).toBe(0); expect(sent.draft.pending?.outcome).toBe("UNKNOWN");
  });
  it("rejects verification with the same stamp and different full Project", async () => {
    const { project, draft, context } = source(), sent = await prepareGraphSubmission(draft, project, context, crypto.randomUUID());
    const changed = structuredClone(project); changed.scenes[0].name += " changed while inspecting"; let reads = 0;
    await expect(inspectGraphOutcome(sent.draft, project, { readProject: async () => structuredClone(reads++ ? changed : project), readHistory: async () => [] })).rejects.toThrow(/changed during outcome inspection/);
    expect(reads).toBe(2); expect(sent.draft.pending?.outcome).toBe("UNKNOWN");
  });
  it("cannot release an inspected UNKNOWN after the selected full Project changes under its stamp", async () => {
    const { project, draft, context } = source(), sent = await prepareGraphSubmission(draft, project, context, crypto.randomUUID()), held = copy(sent.draft), original = held.custody.expectedRaw!;
    const review = await inspectGraphOutcome(sent.draft, project, { readProject: async () => structuredClone(project), readHistory: async () => [] });
    const changed = structuredClone(project); changed.scenes[0].name += " changed after inspecting";
    expect(graphOutcomeMatches(sent.draft, changed, review)).toBe(false); expect(() => releaseGraphOutcome(sent.draft, changed, review)).toThrow(/selected source changed/);
    expect(sent.draft.pending?.outcome).toBe("UNKNOWN"); expect(held.local.data.get(graphCopyKey(draft.scope, held.custody.writerId))).toBe(original);
    expect(graphOutcomeMatches(sent.draft, structuredClone(project), review)).toBe(true);
  });
  it("freezes the first full read when a transport reuses and mutates its Project object", async () => {
    const { project, draft, context } = source(), sent = await prepareGraphSubmission(draft, project, context, crypto.randomUUID());
    const shared = structuredClone(project);
    await expect(inspectGraphOutcome(sent.draft, project, {
      readProject: async () => shared,
      readHistory: async () => { shared.scenes[0].name += " changed on the reused object"; return []; },
    })).rejects.toThrow(/changed during outcome inspection/);
    expect(sent.draft.pending?.outcome).toBe("UNKNOWN");
  });
});


describe("Graph recovery final parent intent covers obsolete descendants", () => {
  it.each(["unit", "basis"])("recovers the final %s variant after editing a descendant and returning to a parent value", kind => {
    const { draft } = source(); let later = draft; const paths: string[] = [];
    if (kind === "unit") {
      const parent = "/content/vertices/" + draft.input.content.vertices[0].id + "/unit", child = parent + "/symbol"; paths.push(parent, child);
      later = change(later, parent, raw => { raw.content.vertices[0].unit = { kind: "symbol", symbol: "" }; });
      later = change(later, child, raw => { raw.content.vertices[0].unit = { kind: "symbol", symbol: "kg" }; });
      later = change(later, parent, raw => { raw.content.vertices[0].unit = { kind: "qualitative" }; });
    } else {
      const parent = "/content/edges/" + draft.input.content.edges[0].id + "/basis", child = parent + "/disclosure"; paths.push(child, parent);
      later = change(later, child, raw => { if (raw.content.kind === "causal_diagram") raw.content.edges[0].basis = { kind: "author_hypothesis", disclosure: "Intermediate disclosed hypothesis" }; });
      later = change(later, parent, raw => { if (raw.content.kind === "causal_diagram") raw.content.edges[0].basis = { kind: "claim_reference", claim_id: crypto.randomUUID(), content_sha256: "a".repeat(64) }; });
    }
    const recovered = change(draft, "/authorship/rationale", raw => { raw.authorship.rationale = "Recovered unrelated explanation"; });
    const next = recoverGraphRead(later, recovered, paths);
    expect(next.input.content).toEqual(later.input.content); expect(next.input.authorship.rationale).toBe("Recovered unrelated explanation");
    expect(next.edited[paths[0]]).toBe(next.sequence); expect(next.edited[paths[1]]).toBe(next.sequence);
    expect(next.base).toEqual(recovered.base); expect(() => validateGraphRaw(next.input)).not.toThrow();
    const held = copy(next); expect(decodeGraphCopy(held.custody.expectedRaw!, next.scope, held.custody.writerId).draft.input.content).toEqual(later.input.content);
  });
});


describe("Graph copy-list reads retain original observations even on later failure", () => {
  it("keeps W bytes observed before a later list entry throws and refuses replacement on Retry", async () => {
    const { project, draft, context } = source(), sent = await prepareGraphSubmission(draft, project, context, crypto.randomUUID());
    const held = copy(sent.draft), original = held.custody.expectedRaw!, writer = held.custody.writerId, observed = unknownRead();
    held.local.onRead = () => { throw new Error("first data read failed"); };
    expect(() => readGraphRecoverySource(held.local, held.pointers, draft.scope, observed)).toThrow(/first data/);
    const laterKey = graphCopyKey(draft.scope, crypto.randomUUID()); held.local.data.set(laterKey, "other entry");
    held.local.onRead = key => { if (key === laterKey) throw new Error("later enumeration read failed"); };
    expect(() => readGraphCopies(held.local, draft.scope, (writerId, raw) => {
      if (writerId === observed.pointer) { observed.raw = raw; observed.rawObserved = true; }
    })).toThrow(/later enumeration/);
    expect(observed.raw).toBe(original); expect(observed.rawObserved).toBe(true);
    held.local.onRead = null; const replacement = JSON.parse(original); replacement.draft.pending = null;
    held.local.data.set(graphCopyKey(draft.scope, writer), JSON.stringify(replacement));
    expect(() => readGraphRecoverySource(held.local, held.pointers, draft.scope, observed)).toThrow(/bytes changed/);
    expect(observed.raw).toBe(original); expect(decodeGraphCopy(observed.raw!, draft.scope, writer).draft.pending?.outcome).toBe("UNKNOWN");
  });
});

describe("Graph unchanged-base uncertainty and exact backup confirmation", () => {
  for (const operation of ["save", "remove"] as const) {
    it(`${operation} UNKNOWN cannot be released by unchanged saved values and empty history`, async () => {
      const { project, draft, context } = source(), prepared = await prepareGraphSubmission(draft, project, context, crypto.randomUUID(), operation);
      const later = change(prepared.draft, "/authorship/rationale", raw => { raw.authorship.rationale = "Later Graph input remains local"; });
      const held = copy(later), key = graphCopyKey(later.scope, held.custody.writerId), original = held.custody.expectedRaw, owner = structuredClone(held.custody);
      let reads = 0;
      const review = await inspectGraphOutcome(later, project, {
        readProject: async () => { reads++; return structuredClone(project); }, readHistory: async () => [],
      });
      expect(reads).toBe(2); expect(review.events).toEqual([]); expect(graphOutcomeMatches(later, project, review)).toBe(true);
      expect(() => releaseGraphOutcome(later, project, review)).toThrow(/could still commit/);
      await expect(prepareGraphSubmission(later, project, context, crypto.randomUUID(), operation)).rejects.toThrow(/cannot be replayed/);
      expect(later.pending).toEqual(prepared.draft.pending); expect(later.input.authorship.rationale).toBe("Later Graph input remains local");
      expect(held.local.getItem(key)).toBe(original); expect(held.custody).toEqual(owner);
      expect(held.pointers.getItem(graphPointerKey(later.scope))).toBe(owner.writerId); assertGraphCustody(held.local, held.pointers, later, held.custody);
    });
    it(`${operation} live known NOT_SENT permits explicit same-base continuation without claiming an ACK`, async () => {
      const { project, draft, context } = source(), prepared = await prepareGraphSubmission(draft, project, context, crypto.randomUUID(), operation);
      const notSent = failGraphSubmission(prepared.draft, prepared.draft.pending!.id, "Recovery prevented the original callback", true), held = copy(notSent);
      const original = held.custody.expectedRaw, key = graphCopyKey(draft.scope, held.custody.writerId);
      const review = await inspectGraphOutcome(notSent, project, { readProject: async () => structuredClone(project), readHistory: async () => [] });
      const next = releaseGraphOutcome(notSent, project, review);
      expect(next.pending).toBeNull(); expect(next.input).toEqual(notSent.input); expect(next.baseRevision).toBe(project.revision);
      expect(notSent.pending?.outcome).toBe("NOT_SENT");
      const separate = forkGraphCopy(held.local, held.pointers, notSent, next, held.custody, crypto.randomUUID());
      expect(held.local.getItem(key)).toBe(original); assertGraphCustody(held.local, held.pointers, next, separate);
      const fresh = await prepareGraphSubmission(next, project, context, crypto.randomUUID(), operation);
      expect(fresh.draft.pending?.id).not.toBe(notSent.pending?.id); expect(fresh.draft.pending?.revision).toBe(project.revision);
    });
    it(`${operation} recovered NOT_SENT is UNKNOWN and cannot release the unchanged saved base`, async () => {
      const { project, draft, context } = source(), prepared = await prepareGraphSubmission(draft, project, context, crypto.randomUUID(), operation);
      const notSent = failGraphSubmission(prepared.draft, prepared.draft.pending!.id, "The earlier runtime did not dispatch", true), held = copy(notSent);
      const raw = held.custody.expectedRaw!, key = graphCopyKey(draft.scope, held.custody.writerId);
      const recovered = decodeGraphCopy(raw, draft.scope, held.custody.writerId).draft;
      expect(recovered.pending?.outcome).toBe("UNKNOWN"); expect(JSON.parse(raw).draft.pending.outcome).toBe("NOT_SENT");
      const review = await inspectGraphOutcome(recovered, project, { readProject: async () => structuredClone(project), readHistory: async () => [] });
      expect(() => releaseGraphOutcome(recovered, project, review)).toThrow(/could still commit/);
      expect(held.local.getItem(key)).toBe(raw); expect(recovered.input).toEqual(notSent.input);
    });
  }
  for (const outcome of ["UNKNOWN", "NOT_SENT"] as const) {
    it(`${outcome} same-draft confirmation retains its exact bytes after the clock advances`, async () => {
      const { project, draft, context } = source(), prepared = await prepareGraphSubmission(draft, project, context, crypto.randomUUID());
      const retained = outcome === "NOT_SENT" ? failGraphSubmission(prepared.draft, prepared.draft.pending!.id, "Known before dispatch", true) : prepared.draft;
      const clock = vi.spyOn(Date, "now").mockReturnValue(1000);
      try {
        const held = copy(retained), raw = held.custody.expectedRaw, key = graphCopyKey(draft.scope, held.custody.writerId), owner = structuredClone(held.custody);
        const writes = held.local.writes; clock.mockReturnValue(2000);
        writeGraphCopy(held.local, held.pointers, structuredClone(retained), held.custody);
        expect(held.local.getItem(key)).toBe(raw); expect(held.local.writes).toBe(writes); expect(held.custody).toEqual(owner);
        expect(JSON.parse(raw!).updatedAt).toBe(1000); expect(JSON.parse(raw!).draft.pending.outcome).toBe(outcome);
        assertGraphCustody(held.local, held.pointers, retained, held.custody);
      } finally { clock.mockRestore(); }
    });
  }
  it("identical Graph bytes do not confirm a dropped pointer until an explicit successful publication", async () => {
    const { project, draft, context } = source(), prepared = await prepareGraphSubmission(draft, project, context, crypto.randomUUID());
    const local = new Memory(), pointers = new Memory(), custody: GraphCustody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: null };
    const key = graphCopyKey(draft.scope, custody.writerId), pointer = graphPointerKey(draft.scope), clock = vi.spyOn(Date, "now").mockReturnValue(1000);
    try {
      pointers.drop = true;
      expect(() => writeGraphCopy(local, pointers, prepared.draft, custody)).toThrow(/pointer write was not confirmed/);
      const raw = local.getItem(key), writes = local.writes;
      clock.mockReturnValue(2000);
      expect(() => writeGraphCopy(local, pointers, structuredClone(prepared.draft), custody)).toThrow(/pointer write was not confirmed/);
      expect(local.getItem(key)).toBe(raw); expect(local.writes).toBe(writes); expect(custody.expectedPointer).toBeNull();
      expect(pointers.getItem(pointer)).toBeNull(); expect(() => assertGraphCustody(local, pointers, prepared.draft, custody)).toThrow();
      pointers.drop = false; clock.mockReturnValue(3000);
      writeGraphCopy(local, pointers, structuredClone(prepared.draft), custody);
      expect(local.getItem(key)).toBe(raw); expect(local.writes).toBe(writes); assertGraphCustody(local, pointers, prepared.draft, custody);
    } finally { clock.mockRestore(); }
  });
  it("same-draft confirmation rejects a lost previously confirmed Graph pointer without claiming it again", () => {
    const { draft } = source(), held = copy(draft), key = graphCopyKey(draft.scope, held.custody.writerId), pointer = graphPointerKey(draft.scope);
    const raw = held.custody.expectedRaw, owner = structuredClone(held.custody), writes = held.local.writes;
    held.pointers.removeItem(pointer);
    expect(() => writeGraphCopy(held.local, held.pointers, structuredClone(draft), held.custody)).toThrow(/changed the Graph pointer/);
    expect(held.local.getItem(key)).toBe(raw); expect(held.local.writes).toBe(writes); expect(held.custody).toEqual(owner);
    expect(held.pointers.getItem(pointer)).toBeNull();
  });
});
