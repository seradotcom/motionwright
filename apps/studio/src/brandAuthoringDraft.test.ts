import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { defaultHeroConfig, emptyProductionDesign, mergeComponentNodes, realizeProductHero } from "./creativeProduction";
import type { Project, ProjectEvent } from "./types";
import {
  acknowledgeBrandDraft, assertBrandOrigins, brandConflict, brandCopyKey, brandHasWork, brandOutcomeMatches,
  brandPointerKey, brandPreparationMatches, brandProjectDigest, brandProposalCurrent, brandScope, brandUnknown,
  createBrandDraft, decodeBrandCopy, editBrandDraft, encodeBrandCopy, inspectBrandOutcome,
  MAX_BRAND_COPY_BYTES, prepareBrandProposal, prepareBrandSubmission, proposedBrandProfile,
  readBrandCopies, readBrandPointer, reconcileBrandDraft, recoverBrandRead, resolveBrandDraft,
  retainBrandFailure, retainBrandOrigin, retainBrandProposal, separateBrandIntent, writeBrandCopy,
} from "./brandAuthoringDraft";
import type {
  BrandChange, BrandCustody, BrandDraft, BrandOperation, BrandPending, BrandProposal, BrandReceiptProof, BrandStorage,
  CampaignRaw, GovernanceRaw,
} from "./brandAuthoringDraft";

// Pure document/recovery oracles. The manually constructed response documents
// below are not StudioService, SQLite, native rendering or human-acceptance evidence.
const id = (value: number) => "10000000-0000-4000-8000-" + value.toString(16).padStart(12, "0");
const ATTEMPT = id(90), WRITER = id(91), NEXT_WRITER = id(92);
const clone = <T,>(value: T): T => structuredClone(value);
function project(campaign = false): Project {
  const result = clone(fixtureProject);
  result.schema_version = 2; result.locks = []; result.reviews = []; result.branch_workspaces = [];
  result.production_design = emptyProductionDesign();
  result.scenes = result.scenes.map(scene => ({ ...scene, status: "draft" as const, renderer: "motion-canvas" as const }));
  while (result.assets.length < 2) result.assets.push({
    id: id(800 + result.assets.length), name: "Pure retained asset " + result.assets.length,
    media_type: "image/png", content_sha256: null, source_revision: null,
  });
  result.production_design.brand_profile = { id: id(20), label: "Synthetic policy", version: 1, rules: [
    { kind: "allowed_accents", id: id(21), colors: ["#A5C8DF", "#F5B84A"] },
    { kind: "required_wordmark", id: id(22), text: "Mw" },
    { kind: "forbidden_phrase", id: id(23), phrase: "unobserved-sentinel-phrase" },
  ] };
  result.production_design.taste_profile = { id: id(24), label: "Saved taste", preferences: [
    { axis: "pace", preference: "Measured entry", inferred: false },
  ] };
  result.production_design.project_decisions = [{
    id: id(25), scene_id: result.scenes[0].id, author: "Earlier test author",
    decision: "Earlier declared choice", rationale: "Pure document fixture only.",
  }];
  if (campaign) {
    result.production_design.heroes = result.scenes.slice(0, 2).map((scene, index) => ({
      id: id(30 + index), scene_id: scene.id, component_version: 1, config: {
        ...defaultHeroConfig(), accent: "#A5C8DF", wordmark: "Mw",
      }, baseline: [],
    }));
  }
  return result;
}
async function campaignProject(): Promise<Project> {
  const result = project(true);
  for (const hero of result.production_design!.heroes) {
    hero.baseline = await realizeProductHero(hero.id, hero.config);
    result.scenes.find(scene => scene.id === hero.scene_id)!.nodes.push(...clone(hero.baseline));
  }
  return result;
}
function metadataProject(): Project {
  const result = project(), otherBranch = result.branches.find(branch => branch.id !== result.active_branch)!;
  const state = {
    production_design: result.production_design, scenes: result.scenes, markers: result.markers, locks: result.locks,
    deliverables: result.deliverables, brief: result.brief, narrative: result.narrative, audio: result.audio,
    visual_language: result.visual_language, proposal_sets: result.proposal_sets, model_invocations: result.model_invocations,
  };
  result.branch_workspaces = [{ branch_id: otherBranch.id, base_revision: otherBranch.base_revision,
    base_state: clone(state), current_state: clone(state) }];
  result.merges = [{ id: id(850), source_branch: otherBranch.id, target_branch: result.active_branch,
    base_revision: otherBranch.base_revision, committed_revision: result.revision,
    merged_at: "2026-10-22T20:00:00Z" }];
  result.reviews = [
    { id: id(851), kind: "creative", anchor: { resource: "scene:" + result.scenes[0].id, branch_id: result.active_branch,
      revision: result.revision, start: null, end: null, locale: null, profile_id: null }, body: "Earlier active review",
      status: "resolved", resolution: "Earlier explicit resolution", created_at: "2026-10-22T20:00:00Z", resolved_at: "2026-10-22T21:00:00Z" },
    { id: id(852), kind: "creative", anchor: { resource: "scene:" + result.scenes[0].id, branch_id: otherBranch.id,
      revision: otherBranch.head_revision, start: null, end: null, locale: null, profile_id: null }, body: "Earlier inactive review",
      status: "resolved", resolution: "Keep this inactive resolution", created_at: "2026-10-22T20:00:00Z", resolved_at: "2026-10-22T21:00:00Z" },
    { id: id(853), kind: "technical", anchor: { resource: "project:" + result.id, branch_id: result.active_branch,
      revision: result.revision, start: null, end: null, locale: null, profile_id: null }, body: "Still open review",
      status: "open", resolution: null, created_at: "2026-10-22T20:00:00Z", resolved_at: null },
  ];
  return result;
}
function governanceDraft(source: Project, patch: Partial<GovernanceRaw> = {}): BrandDraft {
  const draft = createBrandDraft(source, "governance");
  return editBrandDraft(draft, { ...(draft.raw as GovernanceRaw), ...patch });
}
function campaignDraft(source: Project): BrandDraft {
  const draft = createBrandDraft(source, "campaign");
  return editBrandDraft(draft, { ...(draft.raw as CampaignRaw),
    campaign: "  Synthetic launch batch  ", owner: "Original test author", why: "Compare authored values only.",
    sceneIds: source.production_design!.heroes.slice(0, 2).map(hero => hero.scene_id),
    accent: "#F5B84A", ackRebuild: true,
  });
}
async function ready(operation: BrandOperation = "taste") {
  const source = project();
  const draft = governanceDraft(source, {
    brandName: "Changed policy", tasteName: "Changed taste",
    campaign: "Scoped test", author: "Test author", rationale: "A declared test decision.", decision: "A distinct authored choice.",
  });
  const prepared = await prepareBrandSubmission(draft, source, ATTEMPT, operation);
  return { source, ...prepared, original: clone(prepared.draft.pending!) };
}
function committedDocument(source: Project): Project {
  const result = clone(source); result.revision++; result.updated_at = "2026-10-22T23:00:00Z";
  if (result.schema_version === 1) result.schema_version = 2;
  result.branches = result.branches.map(branch => branch.id === result.active_branch
    ? { ...branch, head_revision: result.revision } : branch);
  result.reviews = result.reviews.map(review => review.status === "resolved"
    && review.anchor.branch_id === result.active_branch && review.anchor.revision < result.revision
    ? { ...review, status: "needs_recheck" as const, resolution: null, resolved_at: null } : review);
  result.production_design ??= emptyProductionDesign();
  return result;
}
function response(source: Project, change: BrandChange): Project {
  const result = committedDocument(source), design = result.production_design!;
  if (change.type === "set_brand_governance") {
    design.brand_profile = clone(change.profile);
    design.brand_exceptions = clone(change.exceptions);
  } else if (change.type === "set_taste_profile") design.taste_profile = clone(change.profile);
  else if (change.type === "record_creative_decision")
    design.project_decisions!.push(clone(change.decision));
  else throw new Error("Use the independent canonical-helper campaign document fixture.");
  return result;
}
/** Construct the response document independently from prepareBrandSubmission's
 * live proof, so running these cases against the old model reaches its ACK. */
async function campaignResponse(source: Project, change: BrandChange, proposal: BrandProposal): Promise<Project> {
  if (change.type !== "apply_brand_campaign") throw new Error("This campaign document fixture requires a campaign Change.");
  const result = committedDocument(source), design = result.production_design!;
  for (const [index, row] of proposal.preview.rows.filter(row => row.changed).entries()) {
    const heroIndex = design.heroes.findIndex(hero => hero.id === row.hero_id), hero = design.heroes[heroIndex];
    const scene = result.scenes.find(value => value.id === row.scene_id)!;
    const config = { ...hero.config, accent: row.to_accent, wordmark: row.to_wordmark };
    const baseline = await realizeProductHero(hero.id, config);
    scene.nodes = mergeComponentNodes(hero.baseline, scene.nodes, baseline); scene.status = "draft";
    design.heroes[heroIndex] = { id: hero.id, scene_id: hero.scene_id, component_version: 1, config, baseline };
    design.project_decisions!.push({
        id: id(200 + index), scene_id: row.scene_id, author: change.rollout.reviewer_declaration,
        decision: "Campaign " + change.rollout.campaign + ": brand accent " + row.from_accent + " to " + row.to_accent
          + ", wordmark " + row.from_wordmark + " to " + row.to_wordmark + "; policy SHA-256 " + change.rollout.brand_sha256,
        rationale: change.rollout.rationale,
    });
  }
  return result;
}
async function independentCampaignProof(source: Project, change: BrandChange, saved: Project): Promise<BrandReceiptProof> {
  return { attemptId: ATTEMPT, scope: brandScope(source, "campaign"), sourceRevision: source.revision,
    sourceSha256: await brandProjectDigest(source), change: clone(change),
    heroes: clone(saved.production_design!.heroes), scenes: clone(saved.scenes) };
}
class MemoryStorage implements BrandStorage {
  readonly data = new Map<string, string>();
  get length() { return this.data.size; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key, value); }
}
const custody = (writerId = WRITER, expectedPointer: string | null = null): BrandCustody =>
  ({ writerId, expectedRaw: null, expectedPointer });

describe("brand raw ownership and full source preimages", () => {
  it("round-trips every raw field, empty strings, incomplete color input and preference spacing", async () => {
    const source = project(), draft = governanceDraft(source, {
      brandName: "  ", forbidden: "\n  Keep this spacing \r\n\n", accents: "1e", wordmark: "",
      tasteName: "", preferences: [{ axis: "  ", preference: "\nUnfinished\n", inferred: true }],
      sceneId: "", ruleId: "", campaign: "", author: "  author  ", rationale: "\n", decision: "",
    });
    const raw = clone(draft.raw);
    expect(decodeBrandCopy(encodeBrandCopy(draft, WRITER, 10), draft.scope, WRITER).draft.raw).toEqual(raw);
    await expect(prepareBrandSubmission(draft, source, ATTEMPT, "brand")).rejects.toThrow();
    expect(draft.raw).toEqual(raw);
  });

  it("keeps generation, branch and editor scopes separate", () => {
    const source = project(), changed = { ...clone(source), active_branch: id(401) };
    expect(brandScope(source, "governance")).not.toBe(brandScope(changed, "governance"));
    expect(brandScope(source, "governance")).not.toBe(brandScope(source, "campaign"));
    expect(brandScope(source, "campaign")).not.toBe(brandScope({ ...source, generation: id(402) }, "campaign"));
    expect(() => reconcileBrandDraft(governanceDraft(source), changed)).toThrow(/another project/);
  });

  it("rejects a changed full Project at the same owner and revision", async () => {
    const source = project(), draft = governanceDraft(source, { tasteName: "Unsent" }), changed = clone(source);
    changed.title = "A different source under the same revision";
    expect(brandConflict(draft, changed)).toBe(true);
    expect(reconcileBrandDraft(draft, changed)).toBe(draft);
    await expect(prepareBrandSubmission(draft, changed, ATTEMPT, "taste")).rejects.toThrow(/full saved Project/);
  });

  it("retains an explicit clear/ABA edit instead of treating equality as untouched input", () => {
    const source = project(), clean = createBrandDraft(source, "governance");
    const typed = editBrandDraft(clean, { ...(clean.raw as GovernanceRaw), tasteName: "Temporary" });
    const cleared = editBrandDraft(typed, clone(clean.raw));
    const changed = { ...clone(source), revision: source.revision + 1 };
    expect(cleared.raw).toEqual(clean.raw);
    expect(brandHasWork(cleared)).toBe(true);
    expect(reconcileBrandDraft(cleared, changed)).toBe(cleared);
  });

  it("keeps unrepresented saved rules and their IDs/order when only the policy label changes", () => {
    const source = project(), profile = source.production_design!.brand_profile!;
    profile.rules.splice(1, 0, { kind: "allowed_accents", id: id(45), colors: ["#A5C8DF"] });
    profile.rules.push({ kind: "required_wordmark", id: id(46), text: "Mw" });
    const raw = createBrandDraft(source, "governance").raw as GovernanceRaw;
    const next = proposedBrandProfile({ ...raw, brandName: "New label" }, profile);
    expect(next.rules).toEqual(profile.rules);
    expect(next.id).toBe(profile.id);
    expect(next.version).toBe(profile.version + 1);
    expect(profile.label).toBe("Synthetic policy");
  });

  it("does not let an async preparation survive a later clear or a same-revision source change", () => {
    const source = project(), draft = governanceDraft(source, { tasteName: "New taste" });
    expect(brandPreparationMatches(draft, source, clone(draft), clone(source))).toBe(true);
    const edited = editBrandDraft(draft, clone(draft.raw));
    expect(brandPreparationMatches(draft, source, edited, source)).toBe(false);
    expect(brandPreparationMatches(draft, source, draft, { ...source, title: "Changed" })).toBe(false);
  });

  it("checks the existing content lock before preparing a mutation", async () => {
    const source = project();
    source.locks.push({ id: id(47), resource: "project:" + source.id, kind: "content", note: "Pure lock fixture." });
    await expect(prepareBrandSubmission(governanceDraft(source, { tasteName: "Later" }), source, ATTEMPT, "taste"))
      .rejects.toThrow(/content lock/);
  });
});

describe("original brand responses and later author input", () => {
  it("accepts exactly the active-head and resolved-review derivatives without changing inactive workspaces", async () => {
    const source = metadataProject(), draft = governanceDraft(source, { tasteName: "Changed taste" });
    const prepared = await prepareBrandSubmission(draft, source, ATTEMPT, "taste"), saved = response(source, prepared.change);
    const next = acknowledgeBrandDraft(prepared.draft, ATTEMPT, saved, source, prepared.draft.pending!);
    expect(next.pending).toBeNull(); expect(next.baseProject).toEqual(saved);
    expect(saved.branches.find(branch => branch.id === source.active_branch)!.head_revision).toBe(source.revision + 1);
    expect(saved.branches.filter(branch => branch.id !== source.active_branch)).toEqual(source.branches.filter(branch => branch.id !== source.active_branch));
    expect(saved.branch_workspaces).toEqual(source.branch_workspaces);
    expect(saved.merges).toEqual(source.merges); expect(saved.state).toBe(source.state);
    expect(saved.reviews[0]).toEqual({ ...source.reviews[0], status: "needs_recheck", resolution: null, resolved_at: null });
    expect(saved.reviews.slice(1)).toEqual(source.reviews.slice(1));
  });

  it("admits the actual legacy-schema upgrade while preserving its complete original source", async () => {
    const source = clone(fixtureProject); source.schema_version = 1; delete source.production_design;
    source.locks = []; source.reviews = [];
    const draft = governanceDraft(source, { tasteName: "First saved taste", preferences: [] });
    const prepared = await prepareBrandSubmission(draft, source, ATTEMPT, "taste"), saved = response(source, prepared.change);
    expect(saved.schema_version).toBe(2);
    expect(acknowledgeBrandDraft(prepared.draft, ATTEMPT, saved, source, prepared.draft.pending!).pending).toBeNull();
    expect(source.schema_version).toBe(1); expect(source.production_design).toBeUndefined();
  });

  it.each([
    ["schema", (saved: Project) => { saved.schema_version = 3; }],
    ["state", (saved: Project) => { saved.state = "unknown"; }],
    ["inactive branch identity", (saved: Project) => { saved.branches[1].name += " changed"; }],
    ["inactive branch head", (saved: Project) => { saved.branches[1].head_revision++; }],
    ["active branch head", (saved: Project) => { saved.branches.find(branch => branch.id === saved.active_branch)!.head_revision--; }],
    ["branch order", (saved: Project) => { saved.branches.reverse(); }],
    ["inactive workspace", (saved: Project) => { saved.branch_workspaces[0].current_state.brief.objective += " changed"; }],
    ["merge history", (saved: Project) => { saved.merges[0].base_revision++; }],
    ["active review body", (saved: Project) => { saved.reviews[0].body += " changed"; }],
    ["inactive review resolution", (saved: Project) => { saved.reviews[1].resolution = null; }],
    ["open review status", (saved: Project) => { saved.reviews[2].status = "needs_recheck"; }],
    ["review order", (saved: Project) => { saved.reviews.reverse(); }],
    ["updated timestamp shape", (saved: Project) => { saved.updated_at = "not-an-observed-timestamp"; }],
    ["future unrecognized Project data", (saved: Project) => { (saved as Project & { unexpected: string }).unexpected = "Changed outside this request"; }],
  ] as const)("refuses a taste-matching original receipt with changed %s", async (_label, mutate) => {
    const source = metadataProject(), draft = governanceDraft(source, { tasteName: "Changed taste" });
    const prepared = await prepareBrandSubmission(draft, source, ATTEMPT, "taste"), saved = response(source, prepared.change);
    mutate(saved);
    expect(() => acknowledgeBrandDraft(prepared.draft, ATTEMPT, saved, source, prepared.draft.pending!)).toThrow();
    expect(prepared.draft.pending?.outcome).toBe("UNKNOWN");
  });

  it("acknowledges only the saved section and preserves other unsent fields", async () => {
    const { source, draft, change, original } = await ready("taste");
    const saved = response(source, change), next = acknowledgeBrandDraft(draft, ATTEMPT, saved, source, original);
    expect(next.baseProject).toEqual(saved);
    expect(next.pending).toBeNull();
    expect(next.raw).toEqual(draft.raw);
    expect((next.cleanRaw as GovernanceRaw).tasteName).toBe("Changed taste");
    expect((next.cleanRaw as GovernanceRaw).brandName).toBe("Synthetic policy");
    expect(next.intent).toBe(true);
  });

  it("keeps typing and explicit empty values that arrived after dispatch", async () => {
    const { source, draft, change, original } = await ready("taste");
    const later = editBrandDraft(draft, { ...(draft.raw as GovernanceRaw), tasteName: "", decision: "  Later\n" });
    const next = acknowledgeBrandDraft(later, ATTEMPT, response(source, change), source, original);
    expect(next.raw).toEqual(later.raw);
    expect(next.sequence).toBe(later.sequence);
    expect(next.intent).toBe(true);
    expect((next.cleanRaw as GovernanceRaw).tasteName).toBe("Changed taste");
  });

  it.each([
    ["callback identity", (value: { receipt: Project; source: Project; original: BrandPending; id: string }) => { value.id = id(500); }],
    ["receipt revision", (value: { receipt: Project }) => { value.receipt.revision++; }],
    ["branch", (value: { receipt: Project }) => { value.receipt.active_branch = id(501); }],
    ["generation", (value: { receipt: Project }) => { value.receipt.generation = id(502); }],
    ["original full preimage", (value: { source: Project }) => { value.source.title = "Wrong original source"; }],
    ["captured request proof", (value: { original: BrandPending }) => { value.original.sourceSha256 = "b".repeat(64); }],
  ] as const)("does not acknowledge a different %s", async (_label, mutate) => {
    const prepared = await ready();
    const value = { receipt: response(prepared.source, prepared.change), source: clone(prepared.source),
      original: clone(prepared.original), id: ATTEMPT };
    mutate(value);
    expect(() => acknowledgeBrandDraft(prepared.draft, value.id, value.receipt, value.source, value.original)).toThrow();
    expect(prepared.draft.pending?.outcome).toBe("UNKNOWN");
  });

  it.each([
    ["taste value", (saved: Project) => { saved.production_design!.taste_profile!.label = "Other value"; }],
    ["policy", (saved: Project) => { saved.production_design!.brand_profile!.label = "Other policy"; }],
    ["prior decision history", (saved: Project) => { saved.production_design!.project_decisions = []; }],
    ["audio", (saved: Project) => { saved.audio.mix.voice_gain_db = -9; }],
    ["scene", (saved: Project) => { saved.scenes[0].objective += " changed"; }],
    ["asset order", (saved: Project) => { saved.assets = [...saved.assets].reverse(); }],
  ] as const)("refuses a receipt that changed the %s", async (_label, mutate) => {
    const { source, draft, change, original } = await ready();
    const saved = response(source, change); mutate(saved);
    expect(() => acknowledgeBrandDraft(draft, ATTEMPT, saved, source, original)).toThrow();
  });

  it("validates exact prior decisions and one appended authored decision", async () => {
    const { source, draft, change, original } = await ready("decision");
    const saved = response(source, change);
    const next = acknowledgeBrandDraft(draft, ATTEMPT, saved, source, original);
    expect(next.pending).toBeNull();
    expect(saved.production_design!.project_decisions!.slice(0, -1)).toEqual(source.production_design!.project_decisions);
    const corrupted = clone(saved); corrupted.production_design!.project_decisions!.reverse();
    expect(() => acknowledgeBrandDraft(draft, ATTEMPT, corrupted, source, original)).toThrow();
  });

  it("does not ACK or replay UNKNOWN when matching saved props appear", async () => {
    const { source, draft, change } = await ready();
    const failed = retainBrandFailure(draft, ATTEMPT, "The original reply was lost.");
    const saved = response(source, change);
    expect(reconcileBrandDraft(failed, saved)).toBe(failed);
    expect(brandUnknown(failed)).toBe(true);
    await expect(prepareBrandSubmission(failed, saved, id(504), "taste")).rejects.toThrow(/unconfirmed/);
    expect(() => resolveBrandDraft(failed, saved, "draft")).toThrow(/UNKNOWN/);
    expect(() => resolveBrandDraft(failed, saved, "saved")).toThrow(/UNKNOWN/);
  });

  it("decodes a retained NOT_SENT record as UNKNOWN without reusing its live authority", async () => {
    const { source, draft } = await ready();
    const live = retainBrandFailure(draft, ATTEMPT, "Local copy failed before dispatch.", true);
    const recovered = decodeBrandCopy(encodeBrandCopy(live, WRITER, 10), live.scope, WRITER).draft;
    expect(live.pending?.outcome).toBe("NOT_SENT");
    expect(recovered.pending?.outcome).toBe("UNKNOWN");
    await expect(prepareBrandSubmission(recovered, source, id(505), "taste")).rejects.toThrow();
    expect(() => resolveBrandDraft(recovered, source, "draft")).toThrow(/UNKNOWN/);
  });
});

describe("immutable campaign proposals and exact source membership", () => {
  it("acknowledges a needs_work Hero rebuilt to draft while preserving a compatible manual override", async () => {
    const source = await campaignProject(), hero = source.production_design!.heroes[0];
    const scene = source.scenes.find(value => value.id === hero.scene_id)!;
    scene.status = "needs_work";
    const edited = scene.nodes.find(node => node.id === hero.baseline.find(base => base.name.endsWith(" / body"))?.id);
    expect(edited).toBeDefined(); edited!.text = "A manually refined body that this accent edit does not change";
    const draft = campaignDraft(source), reviewed = retainBrandProposal(draft, await prepareBrandProposal(draft, source, id(610)));
    const prepared = await prepareBrandSubmission(reviewed, source, ATTEMPT, "campaign");
    const saved = await campaignResponse(source, prepared.change, reviewed.proposal!);
    const next = acknowledgeBrandDraft(prepared.draft, ATTEMPT, saved, source, prepared.draft.pending!, prepared.receiptProof);
    expect(next.pending).toBeNull();
    const savedScene = saved.scenes.find(value => value.id === scene.id)!;
    expect(savedScene.status).toBe("draft");
    expect(savedScene.nodes.find(node => node.id === edited!.id)?.text).toBe(edited!.text);
    expect(savedScene.nodes.map(node => node.id)).toEqual(scene.nodes.map(node => node.id));
    expect(saved.production_design!.heroes[0].baseline.length).toBeGreaterThan(0);
  });

  it.each(["missing proof", "wrong proof source", "deleted governed nodes", "missing baseline", "reordered nodes", "changed human override"] as const)
    ("refuses campaign acknowledgement with %s", async mode => {
      const source = await campaignProject(), hero = source.production_design!.heroes[0], scene = source.scenes.find(value => value.id === hero.scene_id)!;
      const governed = new Set(hero.baseline.map(node => node.id)), body = hero.baseline.find(node => node.name.endsWith(" / body"))!;
      const authoredBody = scene.nodes.find(node => node.id === body.id)!;
      authoredBody.text = "An explicit compatible source override";
      const draft = campaignDraft(source), reviewed = retainBrandProposal(draft, await prepareBrandProposal(draft, source, id(611)));
      const prepared = await prepareBrandSubmission(reviewed, source, ATTEMPT, "campaign");
      const saved = await campaignResponse(source, prepared.change, reviewed.proposal!);
      const savedScene = saved.scenes.find(value => value.id === scene.id)!;
      let proof: BrandReceiptProof | null = await independentCampaignProof(source, prepared.change, saved);
      if (mode === "missing proof") proof = null;
      else if (mode === "wrong proof source") proof!.sourceSha256 = "c".repeat(64);
      else if (mode === "deleted governed nodes") {
        savedScene.nodes = savedScene.nodes.filter(node => !governed.has(node.id));
        saved.production_design!.heroes[0].baseline = [];
      } else if (mode === "missing baseline") saved.production_design!.heroes[0].baseline = [];
      else if (mode === "reordered nodes") savedScene.nodes.reverse();
      else savedScene.nodes.find(node => node.id === body.id)!.text = "An overwritten human edit";
      expect(() => acknowledgeBrandDraft(prepared.draft, ATTEMPT, saved, source, prepared.draft.pending!, proof)).toThrow();
      expect(prepared.draft.pending?.outcome).toBe("UNKNOWN");
    });

  it("retains raw selection and the exact proposal through edits, stale props and serialization", async () => {
    const source = await campaignProject(), draft = campaignDraft(source);
    const proposal = await prepareBrandProposal(draft, source, id(600));
    const reviewed = retainBrandProposal(draft, proposal), original = JSON.stringify(reviewed.proposal);
    const edited = editBrandDraft(reviewed, { ...(reviewed.raw as CampaignRaw), why: "", sceneIds: [] });
    const newer = { ...clone(source), revision: source.revision + 1 };
    expect(JSON.stringify(edited.proposal)).toBe(original);
    expect(reconcileBrandDraft(edited, newer)).toBe(edited);
    expect(brandProposalCurrent(edited, source)).toBe(false);
    const recovered = decodeBrandCopy(encodeBrandCopy(edited, WRITER, 10), edited.scope).draft;
    expect(recovered.raw).toEqual(edited.raw);
    expect(JSON.stringify(recovered.proposal)).toBe(original);
    expect(Object.isFrozen(recovered.proposal?.rollout)).toBe(true);
    expect(Object.isFrozen(recovered.proposal?.preview.rows)).toBe(true);
  });

  it("requires the same branch and the complete same-revision source before preparing a campaign mutation", async () => {
    const source = await campaignProject(), draft = campaignDraft(source);
    const reviewed = retainBrandProposal(draft, await prepareBrandProposal(draft, source, id(601)));
    const sameRevisionChanged = clone(source); sameRevisionChanged.scenes[0].objective += " changed";
    await expect(prepareBrandSubmission(reviewed, sameRevisionChanged, ATTEMPT, "campaign")).rejects.toThrow(/full saved Project/);
    await expect(prepareBrandSubmission(reviewed, { ...source, active_branch: id(602) }, ATTEMPT, "campaign")).rejects.toThrow(/another project/);
    expect(brandProposalCurrent(reviewed, source)).toBe(true);
  });

  it("does not use a valid proposal whose rollout text differs from its displayed raw input", async () => {
    const source = await campaignProject(), draft = campaignDraft(source);
    const proposal = clone(await prepareBrandProposal(draft, source, id(603)));
    proposal.rollout.rationale = "A different reviewed instruction";
    const restored = { ...draft, proposal };
    expect(brandProposalCurrent(restored, source)).toBe(false);
    await expect(prepareBrandSubmission(restored, source, ATTEMPT, "campaign")).rejects.toThrow(/exact current/);
  });

  it("preserves the original campaign proposal and later input after an original matching result", async () => {
    const source = await campaignProject(), draft = campaignDraft(source);
    const reviewed = retainBrandProposal(draft, await prepareBrandProposal(draft, source, id(604)));
    const prepared = await prepareBrandSubmission(reviewed, source, ATTEMPT, "campaign");
    const original = clone(prepared.draft.pending!);
    const later = editBrandDraft(prepared.draft, { ...(prepared.draft.raw as CampaignRaw), campaign: "", why: "  Next separate intent  " });
    const saved = await campaignResponse(source, prepared.change, reviewed.proposal!);
    const next = acknowledgeBrandDraft(later, ATTEMPT, saved, source, original, prepared.receiptProof);
    expect(next.pending).toBeNull();
    expect(next.raw).toEqual(later.raw);
    expect(next.proposal).toEqual(reviewed.proposal);
    expect(brandProposalCurrent(next, saved)).toBe(false);
    expect(next.intent).toBe(true);
  });

  it("does not ACK a campaign response that loses earlier decisions or an unselected Hero", async () => {
    const source = await campaignProject(), draft = campaignDraft(source);
    const onlyFirst = editBrandDraft(draft, { ...(draft.raw as CampaignRaw), sceneIds: [(draft.raw as CampaignRaw).sceneIds[0]] });
    const reviewed = retainBrandProposal(onlyFirst, await prepareBrandProposal(onlyFirst, source, id(605)));
    const prepared = await prepareBrandSubmission(reviewed, source, ATTEMPT, "campaign");
    const saved = await campaignResponse(source, prepared.change, reviewed.proposal!);
    saved.production_design!.heroes[1].config.body = "A changed unselected component";
    expect(() => acknowledgeBrandDraft(prepared.draft, ATTEMPT, saved, source, prepared.draft.pending!, prepared.receiptProof)).toThrow();
    const historyLost = await campaignResponse(source, prepared.change, reviewed.proposal!);
    historyLost.production_design!.project_decisions!.shift();
    expect(() => acknowledgeBrandDraft(prepared.draft, ATTEMPT, historyLost, source, prepared.draft.pending!, prepared.receiptProof)).toThrow();
  });
});

describe("verified local brand custody", () => {
  it.each(["silent", "throw"] as const)("retries pointer publication after a %s failure even when data bytes are already identical", mode => {
    const draft = governanceDraft(project(), { brandName: "Retain exact input" });
    const storage = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    pointers.setItem = () => { if (mode === "throw") throw new Error("Injected pointer failure"); };
    expect(() => writeBrandCopy(storage, pointers, draft, owner)).toThrow();
    const written = storage.getItem(brandCopyKey(draft.scope, WRITER));
    expect(written).not.toBeNull();
    expect(owner.expectedRaw).toBe(written);
    expect(owner.expectedPointer).toBeNull();
    pointers.setItem = (key, value) => { pointers.data.set(key, value); };
    expect(writeBrandCopy(storage, pointers, draft, owner)).toBe(written);
    expect(pointers.getItem(brandPointerKey(draft.scope))).toBe(WRITER);
    expect(owner.expectedPointer).toBe(WRITER);
  });

  it("never adopts a pointer written just before its setter throws", () => {
    const draft = governanceDraft(project()), storage = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    pointers.setItem = (key, value) => { pointers.data.set(key, value); throw new Error("Write then throw"); };
    expect(() => writeBrandCopy(storage, pointers, draft, owner)).toThrow("Write then throw");
    const raw = storage.getItem(brandCopyKey(draft.scope, WRITER));
    pointers.setItem = (key, value) => { pointers.data.set(key, value); };
    expect(owner.expectedPointer).toBeNull();
    expect(() => writeBrandCopy(storage, pointers, draft, owner)).toThrow(/Another writer/);
    expect(storage.getItem(brandCopyKey(draft.scope, WRITER))).toBe(raw);
  });

  it("keeps a silent data-write failure unconfirmed", () => {
    const draft = governanceDraft(project()), storage = new MemoryStorage(), pointers = new MemoryStorage(), owner = custody();
    storage.setItem = () => {};
    expect(() => writeBrandCopy(storage, pointers, draft, owner)).toThrow(/bytes were not confirmed/);
    expect(owner.expectedRaw).toBeNull();
    expect(owner.expectedPointer).toBeNull();
    expect(pointers.length).toBe(0);
  });

  it("anchors the first observed null and rejects disappearance of a previously known pointer", () => {
    const storage = new MemoryStorage(), scope = brandScope(project(), "governance");
    expect(readBrandPointer(storage, scope, { observed: false, value: null })).toEqual({ observed: true, value: null });
    storage.setItem(brandPointerKey(scope), WRITER);
    expect(readBrandPointer(storage, scope, { observed: true, value: WRITER }).value).toBe(WRITER);
    storage.data.delete(brandPointerKey(scope));
    expect(() => readBrandPointer(storage, scope, { observed: true, value: WRITER })).toThrow(/pointer changed/);
  });

  it("merges explicit ABA input after recovery-read failure while keeping the recovered UNKNOWN source", async () => {
    const { source, draft: recovered } = await ready();
    const clean = createBrandDraft(source, "governance");
    const typed = editBrandDraft(clean, { ...(clean.raw as GovernanceRaw), tasteName: "Intermediate" });
    const aba = editBrandDraft(typed, clone(clean.raw));
    const merged = recoverBrandRead(aba, recovered, true);
    expect(merged.raw).toEqual(clean.raw);
    expect(merged.pending).toEqual(recovered.pending);
    expect(merged.baseProject).toEqual(recovered.baseProject);
    expect(merged.sequence).toBeGreaterThan(Math.max(aba.sequence, recovered.sequence));
    expect(merged.intent).toBe(true);
    const other = { ...clone(recovered), pending: { ...clone(recovered.pending!), id: id(700) } };
    expect(() => recoverBrandRead(other, recovered, true)).toThrow(/Another unresolved/);
  });

  it("publishes restored UNKNOWN A under a new writer without replacing the exact bytes of A or current B", async () => {
    const { source, draft: a } = await ready();
    const storage = new MemoryStorage(), pointers = new MemoryStorage(), aOwner = custody();
    const rawA = writeBrandCopy(storage, pointers, a, aOwner), keyA = brandCopyKey(a.scope, WRITER);
    const b = governanceDraft(source, { decision: "B must survive" }), bOwner = custody(NEXT_WRITER, WRITER);
    const rawB = writeBrandCopy(storage, pointers, b, bOwner), keyB = brandCopyKey(b.scope, NEXT_WRITER);
    const restored = retainBrandOrigin(decodeBrandCopy(rawA, a.scope, WRITER).draft, { key: keyA, raw: rawA });
    const cOwner = custody(id(701), NEXT_WRITER);
    pointers.setItem = () => {};
    expect(() => writeBrandCopy(storage, pointers, restored, cOwner)).toThrow(/pointer was not confirmed/);
    pointers.setItem = (key, value) => { pointers.data.set(key, value); };
    const rawC = writeBrandCopy(storage, pointers, restored, cOwner);
    expect(storage.getItem(keyA)).toBe(rawA);
    expect(storage.getItem(keyB)).toBe(rawB);
    expect(decodeBrandCopy(rawC, a.scope).draft.pending).toEqual(a.pending);
    expect(decodeBrandCopy(rawC, a.scope).draft.raw).toEqual(a.raw);
    storage.data.set(keyA, rawA + " ");
    expect(() => assertBrandOrigins(storage, restored)).toThrow(/original uncertain/);
    expect(() => writeBrandCopy(storage, pointers, restored, cOwner)).toThrow(/original uncertain/);
  });

  it("retains corrupt own-scope bytes and ignores foreign-owner copies", () => {
    const source = project(), draft = governanceDraft(source), storage = new MemoryStorage();
    const badKey = brandCopyKey(draft.scope, WRITER), bad = "{corrupt retained bytes";
    storage.setItem(badKey, bad);
    const otherProject = { ...clone(source), generation: id(702) }, other = governanceDraft(otherProject);
    storage.setItem(brandCopyKey(other.scope, NEXT_WRITER), encodeBrandCopy(other, NEXT_WRITER, 10));
    const result = readBrandCopies(storage, draft.scope);
    expect(result.entries).toHaveLength(1);
    expect(result.entries[0]).toMatchObject({ key: badKey, raw: bad, value: null });
    expect(result.entries[0].error).toBeTruthy();
    expect(storage.getItem(badKey)).toBe(bad);
  });

  it("rejects a foreign recovery owner and extra fields inside a submitted policy", async () => {
    const { draft } = await ready("brand");
    const raw = encodeBrandCopy(draft, WRITER, 10);
    expect(() => decodeBrandCopy(raw, brandScope({ ...project(), active_branch: id(703) }, "governance"))).toThrow();
    const edited = JSON.parse(raw);
    edited.draft.pending.change.profile.extra = "unrecognized";
    expect(() => decodeBrandCopy(JSON.stringify(edited), draft.scope)).toThrow(/incompatible/);
    expect(() => decodeBrandCopy(raw + "\n", draft.scope)).toThrow(/incompatible/);
  });

  it("refuses oversized retention without truncating the in-memory input", () => {
    const draft = governanceDraft(project(), { rationale: "z".repeat(MAX_BRAND_COPY_BYTES) });
    expect(() => encodeBrandCopy(draft, WRITER, 10)).toThrow(/1 MiB/);
    expect((draft.raw as GovernanceRaw).rationale).toHaveLength(MAX_BRAND_COPY_BYTES);
  });
});

describe("uncertain brand outcomes remain observations", () => {
  it("reads a stable full Project and contiguous history without acknowledging or sending anything", async () => {
    const { source, draft, change } = await ready();
    const saved = response(source, change);
    const event: ProjectEvent = { revision: saved.revision, change, created_at: "2026-10-22T23:00:00Z" };
    let reads = 0, histories = 0;
    const review = await inspectBrandOutcome(draft, saved, {
      readProject: async () => { reads++; return clone(saved); },
      readHistory: async (selected, after, limit) => {
        histories++; expect(selected).toEqual(saved); expect(after).toBe(source.revision); expect(limit).toBe(256);
        return [clone(event)];
      },
    });
    expect({ reads, histories }).toEqual({ reads: 2, histories: 1 });
    expect(review.matchingRevisions).toEqual([saved.revision]);
    expect(draft.pending?.outcome).toBe("UNKNOWN");
    expect(brandOutcomeMatches(draft, saved, review)).toBe(true);
    const changed = { ...clone(saved), title: "Changed after read" };
    expect(brandOutcomeMatches(draft, changed, review)).toBe(false);
    expect(() => separateBrandIntent(draft, changed, review)).toThrow(/current full Project/);
    const separate = separateBrandIntent(draft, saved, review);
    expect(separate.pending).toBeNull();
    expect(separate.raw).toEqual(draft.raw);
    expect(draft.pending?.outcome).toBe("UNKNOWN");
  });

  it("does not release an UNKNOWN request while its original revision could still commit", async () => {
    const { source, draft } = await ready();
    const review = await inspectBrandOutcome(draft, source, {
      readProject: async () => clone(source), readHistory: async () => [],
    });
    expect(() => separateBrandIntent(draft, source, review)).toThrow(/could still commit/);
    expect(draft.pending?.outcome).toBe("UNKNOWN");
  });

  it("rejects a gap or a Project changed during the second saved read", async () => {
    const { source, draft, change } = await ready(), saved = response(source, change);
    await expect(inspectBrandOutcome(draft, saved, {
      readProject: async () => clone(saved), readHistory: async () => [],
    })).rejects.toThrow(/incomplete/);
    let reads = 0;
    await expect(inspectBrandOutcome(draft, saved, {
      readProject: async () => ++reads === 1 ? clone(saved) : { ...clone(saved), title: "A later observed source" },
      readHistory: async () => [{ revision: saved.revision, change, created_at: "2026-10-22T23:00:00Z" }],
    })).rejects.toThrow(/changed during/);
  });
});
