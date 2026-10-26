/** Brand author input and recovery only. App's existing coordinator, grants,
 * StudioService and SQLite remain the sole mutation authority. */
import type { Change, Project, ProjectEvent } from "./types";
import { sameProjectRevision, projectionUuid } from "./projectMutation";
import { emptyProductionDesign, mergeComponentNodes, realizeProductHero, sameValue } from "./creativeProduction";
import {
  brandDigest, validateBrandGovernance, type BrandProfile, type BrandRule, type TastePreference,
} from "./brandGovernance";
import {
  BRAND_ROLLOUT_SCHEMA, previewBrandCampaign, validateBrandCampaignReceipt,
  type BrandCampaignRollout, type BrandCampaignPreview,
} from "./brandCampaign";

export type BrandTarget = "governance" | "campaign";
export interface GovernanceRaw {
  kind: "governance"; brandName: string; forbidden: string; accents: string; wordmark: string;
  tasteName: string; preferences: TastePreference[]; sceneId: string; ruleId: string;
  campaign: string; author: string; rationale: string; decision: string;
}
export interface CampaignRaw {
  kind: "campaign"; campaign: string; owner: string; why: string; sceneIds: string[];
  accent: string; applyWordmark: boolean; ackRebuild: boolean;
}
export type BrandRaw = GovernanceRaw | CampaignRaw;
export type BrandChange = Extract<Change, {
  type: "set_brand_governance" | "set_taste_profile" | "record_creative_decision" | "apply_brand_campaign"
}>;
export type BrandOperation = "brand" | "taste" | "exception" | "revoke-all" | "decision" | "campaign" | { revoke: string };
export interface BrandProposal {
  id: string; scope: string; sequence: number; sourceRevision: number; sourceSha256: string;
  raw: CampaignRaw; rollout: BrandCampaignRollout; preview: BrandCampaignPreview;
}
export interface BrandPending {
  id: string; revision: number; sequence: number; raw: BrandRaw; operation: BrandOperation;
  change: BrandChange; sourceSha256: string; outcome: "UNKNOWN" | "NOT_SENT"; diagnostic: string | null;
}
/** Live preparation proof only: never encoded into recovery or accepted from a
 * later Project read. Existing realization/three-way-merge helpers own the math. */
export interface BrandReceiptProof {
  attemptId: string; scope: string; sourceRevision: number; sourceSha256: string; change: BrandChange;
  heroes: NonNullable<Project["production_design"]>["heroes"]; scenes: Project["scenes"];
}
export interface BrandOrigin { key: string; raw: string }
export interface BrandDraft {
  scope: string; target: BrandTarget; baseRevision: number; baseProject: Project;
  cleanRaw: BrandRaw; raw: BrandRaw; sequence: number; intent: boolean;
  proposal: BrandProposal | null; pending: BrandPending | null; origins: BrandOrigin[];
}
export interface BrandCustody { writerId: string; expectedRaw: string | null; expectedPointer: string | null }
export interface BrandCopy {
  schema: "motionwright.brand-authoring-recovery/1"; scope: string; writerId: string;
  updatedAt: number; draft: BrandDraft;
}
export interface BrandRecoveryEntry {
  key: string; writerId: string; raw: string; value: BrandCopy | null; error: string | null;
}
export type BrandStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem">;
export const BRAND_COPY_PREFIX = "motionwright.brand-authoring-recovery.v1:";
export const BRAND_POINTER_PREFIX = "motionwright.brand-authoring-writer.v1:";
export const MAX_BRAND_COPY_BYTES = 1024 * 1024;
export const MAX_BRAND_RECOVERY_BYTES = 8 * 1024 * 1024;
export const MAX_BRAND_COPIES = 64;
const MAX_KEYS = 4096, MAX_ORIGINS = 16, MAX_HISTORY = 2048;
const clone = <T,>(value: T): T => structuredClone(value);
const equal = (a: unknown, b: unknown) => sameValue(a, b);
const bytes = (value: string) => new TextEncoder().encode(value).length;
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: readonly string[]) =>
  keys.every(key => Object.hasOwn(value, key)) && Object.keys(value).every(key => keys.includes(key));
const sha = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
function immutableProposal(value: BrandProposal): BrandProposal {
  const copied = clone(value);
  const freeze = (item: unknown) => {
    if (item !== null && typeof item === "object") {
      for (const child of Object.values(item)) freeze(child);
      Object.freeze(item);
    }
  };
  freeze(copied); return copied;
}
const step = (sequence: number) => {
  if (!integer(sequence) || !Number.isSafeInteger(sequence + 1)) throw new Error("The brand draft sequence exceeded its safe limit.");
  return sequence + 1;
};
export const brandLabel = (target: BrandTarget) => target === "campaign" ? "campaign" : "brand and taste";
export const brandScope = (project: Project, target: BrandTarget) =>
  JSON.stringify([project.id, project.generation, project.active_branch, target]);
export function brandIdentity(scope: string): [string, string, string, BrandTarget] {
  const value: unknown = JSON.parse(scope);
  if (!Array.isArray(value) || value.length !== 4 || !value.slice(0, 3).every(projectionUuid)
      || !["governance", "campaign"].includes(String(value[3])))
    throw new Error("The brand recovery owner is invalid.");
  return value as [string, string, string, BrandTarget];
}
export const brandCopyKey = (scope: string, writerId: string) => BRAND_COPY_PREFIX + scope + ":" + writerId;
export const brandPointerKey = (scope: string) => BRAND_POINTER_PREFIX + scope;
export const brandHasWork = (draft: BrandDraft) => draft.intent || draft.sequence > 0 || draft.pending !== null || draft.proposal !== null;
export const brandUnknown = (draft: BrandDraft) => draft.pending?.outcome === "UNKNOWN";
function owner(draft: BrandDraft, project: Project) {
  if (brandScope(project, draft.target) !== draft.scope)
    throw new Error("This brand input belongs to another project, generation, branch or editor.");
}
export function brandSavedRaw(project: Project, target: BrandTarget): BrandRaw {
  if (target === "campaign") return {
    kind: "campaign", campaign: "", owner: "", why: "", sceneIds: [], accent: "", applyWordmark: false, ackRebuild: false,
  };
  const design = project.production_design, brand = design?.brand_profile;
  const palette = brand?.rules.find(rule => rule.kind === "allowed_accents");
  const mark = brand?.rules.find(rule => rule.kind === "required_wordmark");
  return {
    kind: "governance", brandName: brand?.label ?? "",
    forbidden: brand?.rules.filter(rule => rule.kind === "forbidden_phrase").map(rule => rule.phrase).join("\n") ?? "",
    accents: palette?.kind === "allowed_accents" ? palette.colors.join(", ") : "",
    wordmark: mark?.kind === "required_wordmark" ? mark.text : "",
    tasteName: design?.taste_profile?.label ?? "", preferences: clone(design?.taste_profile?.preferences ?? []),
    sceneId: project.scenes[0]?.id ?? "", ruleId: brand?.rules[0]?.id ?? "",
    campaign: "", author: "", rationale: "", decision: "",
  };
}
export function createBrandDraft(project: Project, target: BrandTarget): BrandDraft {
  brandIdentity(brandScope(project, target));
  const raw = brandSavedRaw(project, target);
  return { scope: brandScope(project, target), target, baseRevision: project.revision, baseProject: clone(project),
    cleanRaw: clone(raw), raw, sequence: 0, intent: false, proposal: null, pending: null, origins: [] };
}
export function editBrandDraft(draft: BrandDraft, raw: BrandRaw): BrandDraft {
  if (!rawShape(raw) || raw.kind !== draft.target) throw new Error("Brand input cannot change its editor kind.");
  return { ...draft, raw: clone(raw), sequence: step(draft.sequence), intent: true };
}
export function reconcileBrandDraft(draft: BrandDraft, project: Project): BrandDraft {
  owner(draft, project);
  if (brandHasWork(draft) || equal(draft.baseProject, project)) return draft;
  return { ...createBrandDraft(project, draft.target), sequence: draft.sequence, origins: draft.origins };
}
export function brandConflict(draft: BrandDraft, project: Project): boolean {
  owner(draft, project);
  return brandHasWork(draft) && (!sameProjectRevision(draft.baseProject, project) || !equal(draft.baseProject, project));
}
export function brandLocked(project: Project) {
  return project.locks.some(lock => lock.resource === "project:" + project.id && lock.kind === "content");
}
function requireBase(draft: BrandDraft, project: Project) {
  owner(draft, project);
  if (draft.pending) throw new Error("The original brand attempt is unconfirmed. Read its outcome before a separate edit.");
  if (!sameProjectRevision(draft.baseProject, project) || !equal(draft.baseProject, project))
    throw new Error("Review the full saved Project before using a new brand base.");
  if (brandLocked(project)) throw new Error("The project has a content lock. Your brand input is retained.");
}
export function brandPreparationMatches(source: BrandDraft, project: Project, current: BrandDraft, selected: Project) {
  return source.scope === current.scope && source.sequence === current.sequence && equal(source, current)
    && sameProjectRevision(project, selected) && equal(project, selected);
}
/** Digest of the complete selected document; arrays retain their authored order. */
export async function brandProjectDigest(project: Project): Promise<string> {
  const canonical = JSON.stringify(project, (_key, value: unknown) => record(value)
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, value[key]])) : value);
  const result = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
  return Array.from(new Uint8Array(result), value => value.toString(16).padStart(2, "0")).join("");
}
/** Update only the controls this panel represents. Extra saved palette/wordmark
 * rules keep their IDs, values and relative positions rather than disappearing. */
export function proposedBrandProfile(raw: GovernanceRaw, saved: BrandProfile | null | undefined): BrandProfile {
  const old = saved?.rules ?? [], phrases = raw.forbidden.split("\n").map(value => value.trim()).filter(Boolean);
  const available = old.filter((rule): rule is Extract<BrandRule, { kind: "forbidden_phrase" }> => rule.kind === "forbidden_phrase");
  const nextPhrases: BrandRule[] = phrases.map(phrase => {
    const index = available.findIndex(rule => rule.phrase === phrase);
    const prior = index < 0 ? null : available.splice(index, 1)[0];
    return { kind: "forbidden_phrase", id: prior?.id ?? crypto.randomUUID(), phrase };
  });
  const colors = raw.accents.split(",").map(value => value.trim()).filter(Boolean), text = raw.wordmark.trim();
  const palette = old.find(rule => rule.kind === "allowed_accents"), mark = old.find(rule => rule.kind === "required_wordmark");
  const rules: BrandRule[] = [];
  let phraseIndex = 0, paletteSeen = false, markSeen = false;
  for (const rule of old) {
    if (rule.kind === "forbidden_phrase") {
      if (phraseIndex < nextPhrases.length) rules.push(nextPhrases[phraseIndex++]);
    } else if (rule.kind === "allowed_accents" && !paletteSeen) {
      paletteSeen = true;
      if (colors.length) rules.push({ kind: "allowed_accents", id: rule.id, colors });
    } else if (rule.kind === "required_wordmark" && !markSeen) {
      markSeen = true;
      if (text) rules.push({ kind: "required_wordmark", id: rule.id, text });
    } else rules.push(clone(rule));
  }
  rules.push(...nextPhrases.slice(phraseIndex));
  if (!palette && colors.length) rules.push({ kind: "allowed_accents", id: crypto.randomUUID(), colors });
  if (!mark && text) rules.push({ kind: "required_wordmark", id: crypto.randomUUID(), text });
  const version = (saved?.version ?? 0) + 1;
  if (!Number.isSafeInteger(version)) throw new Error("The brand version exceeded its safe limit.");
  return { id: saved?.id ?? crypto.randomUUID(), label: raw.brandName.trim(), version, rules };
}
export async function prepareBrandProposal(draft: BrandDraft, project: Project, id: string): Promise<BrandProposal> {
  requireBase(draft, project);
  if (!projectionUuid(id) || draft.raw.kind !== "campaign" || !draft.raw.ackRebuild)
    throw new Error("Review the semantic rebuild consequences before preparing a campaign.");
  const profile = project.production_design?.brand_profile;
  if (!profile) throw new Error("Save a current BrandProfile before reviewing a campaign.");
  const raw = clone(draft.raw), mark = profile.rules.find(rule => rule.kind === "required_wordmark");
  const rollout: BrandCampaignRollout = {
    schema: BRAND_ROLLOUT_SCHEMA, project_id: project.id, generation: project.generation,
    expected_revision: project.revision, brand_sha256: await brandDigest(profile), campaign: raw.campaign,
    scene_ids: [...raw.sceneIds], accent: raw.accent || null,
    wordmark: raw.applyWordmark && mark?.kind === "required_wordmark" ? mark.text : null,
    reviewer_declaration: raw.owner, rationale: raw.why, accepts_semantic_component_rebuild: true,
  };
  const preview = await previewBrandCampaign(project, rollout);
  return immutableProposal({ id, scope: draft.scope, sequence: draft.sequence, sourceRevision: project.revision,
    sourceSha256: await brandProjectDigest(project), raw, rollout, preview: clone(preview) });
}
export function retainBrandProposal(draft: BrandDraft, proposal: BrandProposal): BrandDraft {
  if (draft.pending || draft.target !== "campaign" || proposal.scope !== draft.scope
      || proposal.sequence !== draft.sequence || proposal.sourceRevision !== draft.baseRevision
      || !equal(proposal.raw, draft.raw)) throw new Error("The campaign input changed while preparing its review.");
  return { ...draft, proposal: immutableProposal(proposal) };
}
export function brandProposalCurrent(draft: BrandDraft, project: Project): boolean {
  const value = draft.proposal;
  const raw = draft.raw, mark = project.production_design?.brand_profile?.rules.find(rule => rule.kind === "required_wordmark");
  const rawMatches = !!value && raw.kind === "campaign" && raw.ackRebuild
    && value.rollout.campaign === raw.campaign && value.rollout.reviewer_declaration === raw.owner
    && value.rollout.rationale === raw.why && equal(value.rollout.scene_ids, raw.sceneIds)
    && value.rollout.accent === (raw.accent || null)
    && value.rollout.wordmark === (raw.applyWordmark && mark?.kind === "required_wordmark" ? mark.text : null);
  return !!value && !draft.pending && !brandConflict(draft, project) && value.scope === draft.scope
    && value.sequence === draft.sequence && value.sourceRevision === project.revision
    && rawMatches && equal(value.raw, draft.raw) && validateBrandCampaignReceipt(project, value.rollout, value.preview);
}
export async function prepareBrandSubmission(draft: BrandDraft, project: Project, id: string, operation: BrandOperation) {
  requireBase(draft, project);
  if (!projectionUuid(id) || !operationShape(operation)) throw new Error("The brand submission identity or operation is invalid.");
  const raw = clone(draft.raw), design = project.production_design, profile = design?.brand_profile;
  let change: BrandChange, receiptProof: BrandReceiptProof | null = null;
  if (operation === "campaign") {
    if (raw.kind !== "campaign" || !brandProposalCurrent(draft, project))
      throw new Error("Review the exact current campaign proposal before submitting.");
    const proposal = draft.proposal!;
    if (proposal.sourceSha256 !== await brandProjectDigest(project)
        || !equal(await previewBrandCampaign(project, proposal.rollout), proposal.preview))
      throw new Error("The reviewed brand source changed. Prepare a new review; the old proposal is retained.");
    change = { type: "apply_brand_campaign", rollout: clone(proposal.rollout) };
    const heroes = clone(project.production_design!.heroes), scenes = clone(project.scenes);
    for (const row of proposal.preview.rows.filter(row => row.changed)) {
      const heroIndex = heroes.findIndex(hero => hero.id === row.hero_id), sceneIndex = scenes.findIndex(scene => scene.id === row.scene_id);
      if (heroIndex < 0 || sceneIndex < 0 || heroes[heroIndex].scene_id !== row.scene_id)
        throw new Error("The reviewed campaign scene or component is absent from its source.");
      const hero = heroes[heroIndex], scene = scenes[sceneIndex];
      const config = { ...hero.config, accent: row.to_accent, wordmark: row.to_wordmark };
      const baseline = await realizeProductHero(hero.id, config);
      const nodes = mergeComponentNodes(hero.baseline, scene.nodes, baseline);
      heroes[heroIndex] = { id: hero.id, scene_id: hero.scene_id, component_version: 1, config: clone(config), baseline: clone(baseline) };
      scenes[sceneIndex] = { ...scene, status: "draft", nodes };
    }
    receiptProof = { attemptId: id, scope: draft.scope, sourceRevision: project.revision,
      sourceSha256: proposal.sourceSha256, change: clone(change), heroes, scenes };
  } else {
    if (raw.kind !== "governance") throw new Error("This operation belongs to the brand and taste editor.");
    if (operation === "brand") {
      change = { type: "set_brand_governance", profile: proposedBrandProfile(raw, profile), exceptions: clone(design?.brand_exceptions ?? []) };
    } else if (operation === "taste") {
      change = { type: "set_taste_profile", profile: {
        id: design?.taste_profile?.id ?? crypto.randomUUID(), label: raw.tasteName, preferences: clone(raw.preferences),
      } };
    } else if (operation === "decision") {
      change = { type: "record_creative_decision", decision: {
        id: crypto.randomUUID(), scene_id: raw.sceneId, author: raw.author, decision: raw.decision, rationale: raw.rationale,
      } };
    } else if (operation === "exception") {
      if (!profile) throw new Error("Save a brand policy before recording a scene exception.");
      if (!raw.ruleId || !profile.rules.some(rule => rule.id === raw.ruleId))
        throw new Error("Choose a saved brand rule before recording a scene exception.");
      change = { type: "set_brand_governance", profile: clone(profile), exceptions: [
        ...clone(design?.brand_exceptions ?? []),
        { id: crypto.randomUUID(), rule_id: raw.ruleId, scene_id: raw.sceneId, brand_sha256: await brandDigest(profile),
          campaign: raw.campaign, author: raw.author, rationale: raw.rationale },
      ] };
    } else {
      const exceptions = design?.brand_exceptions ?? [];
      if (!exceptions.length || (typeof operation === "object" && !exceptions.some(value => value.id === operation.revoke)))
        throw new Error("The selected saved brand exception is no longer present.");
      change = { type: "set_brand_governance", profile: clone(profile ?? null),
        exceptions: operation === "revoke-all" ? [] : clone(exceptions.filter(value => value.id !== (operation as { revoke: string }).revoke)) };
    }
    const candidate = clone(project); candidate.production_design ??= emptyProductionDesign();
    if (change.type === "set_brand_governance") {
      candidate.production_design.brand_profile = clone(change.profile);
      candidate.production_design.brand_exceptions = clone(change.exceptions);
    } else if (change.type === "set_taste_profile") candidate.production_design.taste_profile = clone(change.profile);
    else if (change.type === "record_creative_decision")
      candidate.production_design.project_decisions = [...(candidate.production_design.project_decisions ?? []), clone(change.decision)];
    await validateBrandGovernance(candidate);
  }
  const pending: BrandPending = { id, revision: project.revision, sequence: draft.sequence, raw, operation: clone(operation),
    change: clone(change), sourceSha256: await brandProjectDigest(project), outcome: "UNKNOWN", diagnostic: null };
  return { change, draft: { ...draft, intent: true, pending }, receiptProof };
}
export function retainBrandFailure(draft: BrandDraft, id: string, diagnostic: string, notSent = false): BrandDraft {
  return draft.pending?.id === id ? { ...draft, pending: { ...draft.pending,
    outcome: notSent ? "NOT_SENT" : "UNKNOWN", diagnostic: diagnostic.slice(0, 4000) } } : draft;
}
function unrelated(project: Project) {
  // Compare every other Project property, including future wire fields. Only
  // these documented commit derivatives and the actual brand effects vary.
  const { revision: _revision, schema_version: _schema, updated_at: _updated,
    branches: _branches, reviews: _reviews, scenes: _scenes, production_design: _design, ...rest } = project;
  return rest;
}
function commitMetadata(before: Project, after: Project): boolean {
  // domain::apply_change_in_place upgrades legacy schema 1 to current schema 2.
  if (![1, 2].includes(before.schema_version) || after.schema_version !== (before.schema_version === 1 ? 2 : before.schema_version)
      || typeof after.updated_at !== "string"
      || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.test(after.updated_at)
      || !Number.isFinite(Date.parse(after.updated_at))) return false;
  // history::commit_revision changes only the active branch head and resolved
  // reviews of that branch anchored before this new revision. Ordinary brand
  // edits do not run save_active_workspace or mutate inactive branch snapshots.
  const branches = before.branches.map(branch => branch.id === before.active_branch
    ? { ...branch, head_revision: after.revision } : branch);
  const reviews = before.reviews.map(review => review.status === "resolved"
    && review.anchor.branch_id === before.active_branch && review.anchor.revision < after.revision
    ? { ...review, status: "needs_recheck", resolution: null, resolved_at: null } : review);
  return equal(after.branches, branches) && equal(after.reviews, reviews);
}
function governance(project: Project) {
  const design = project.production_design;
  return { brand_profile: design?.brand_profile ?? null, brand_exceptions: design?.brand_exceptions ?? [],
    taste_profile: design?.taste_profile ?? null, project_decisions: design?.project_decisions ?? [] };
}
function otherDesign(project: Project, campaign: boolean) {
  const { brand_profile: _brand, brand_exceptions: _exceptions, taste_profile: _taste,
    project_decisions: _decisions, heroes, ...rest } = project.production_design ?? emptyProductionDesign();
  return campaign ? rest : { ...rest, heroes };
}
function campaignReceipt(before: Project, after: Project, value: BrandProposal, rollout: BrandCampaignRollout, proof: BrandReceiptProof): boolean {
  if (!equal(after.production_design?.heroes ?? [], proof.heroes) || !equal(after.scenes, proof.scenes)) return false;
  const rows = value.preview.rows.filter(row => row.changed);
  const prior = before.production_design?.project_decisions ?? [], decisions = after.production_design?.project_decisions ?? [];
  if (decisions.length !== prior.length + rows.length || !equal(decisions.slice(0, prior.length), prior)) return false;
  const ids = new Set(prior.map(decision => decision.id));
  return rows.every((row, index) => {
    const decision = decisions[prior.length + index];
    if (!projectionUuid(decision.id) || ids.has(decision.id)) return false;
    ids.add(decision.id);
    return equal(decision, { id: decision.id, scene_id: row.scene_id, author: rollout.reviewer_declaration,
      decision: "Campaign " + rollout.campaign + ": brand accent " + row.from_accent + " to " + row.to_accent
        + ", wordmark " + row.from_wordmark + " to " + row.to_wordmark + "; policy SHA-256 " + rollout.brand_sha256,
      rationale: rollout.rationale });
  });
}
function cleanAfterOperation(clean: BrandRaw, pending: BrandPending): BrandRaw {
  const raw = pending.raw;
  if (raw.kind === "campaign") return clone(raw);
  if (clean.kind !== "governance") throw new Error("The saved brand buffer has an incompatible kind.");
  let fields: (keyof GovernanceRaw)[] = [];
  if (pending.operation === "brand") fields = ["brandName", "forbidden", "accents", "wordmark"];
  else if (pending.operation === "taste") fields = ["tasteName", "preferences"];
  else if (pending.operation === "exception") fields = ["sceneId", "ruleId", "campaign", "author", "rationale"];
  else if (pending.operation === "decision") fields = ["sceneId", "author", "rationale", "decision"];
  const next = clone(clean);
  for (const key of fields) Object.assign(next, { [key]: clone(raw[key]) });
  return next;
}
/** This accepts only the original Promise<Project> result for the live attempt.
 * Matching values read later, even at r+1, are never acknowledgement authority. */
export function acknowledgeBrandDraft(draft: BrandDraft, id: string, receipt: Project, submitted: Project, original: BrandPending,
  proof: BrandReceiptProof | null = null): BrandDraft {
  owner(draft, submitted); owner(draft, receipt);
  const pending = draft.pending;
  const attempt = (value: BrandPending) => ({ ...value, outcome: "UNKNOWN", diagnostic: null });
  if (!pending || pending.id !== id || pending.outcome !== "UNKNOWN"
      || receipt.revision !== pending.revision + 1 || submitted.revision !== pending.revision
      || !equal(submitted, draft.baseProject) || !equal(attempt(pending), attempt(original)))
    throw new Error("The original brand receipt does not identify this exact pending source and next revision.");
  if (!equal(unrelated(submitted), unrelated(receipt)) || !commitMetadata(submitted, receipt))
    throw new Error("The brand response changed unrelated project data or its exact commit metadata.");
  const expected = clone(governance(submitted)), change = pending.change;
  if (change.type === "set_brand_governance") {
    expected.brand_profile = clone(change.profile); expected.brand_exceptions = clone(change.exceptions);
  } else if (change.type === "set_taste_profile") expected.taste_profile = clone(change.profile);
  else if (change.type === "record_creative_decision") expected.project_decisions.push(clone(change.decision));
  const campaign = change.type === "apply_brand_campaign";
  if (!equal(otherDesign(submitted, campaign), otherDesign(receipt, campaign)))
    throw new Error("The brand response changed an unrelated production definition.");
  if (campaign) {
    if (!draft.proposal || !equal(draft.proposal.rollout, change.rollout)
        || !proof || proof.attemptId !== id || proof.scope !== draft.scope || proof.sourceRevision !== pending.revision
        || proof.sourceSha256 !== pending.sourceSha256 || !equal(proof.change, change)
        || !campaignReceipt(submitted, receipt, draft.proposal, change.rollout, proof))
      throw new Error("The campaign response does not preserve the reviewed scene identities, overrides and decision history.");
    const { project_decisions: _decisions, ...sourceGovernance } = expected;
    const { project_decisions: _savedDecisions, ...savedGovernance } = governance(receipt);
    if (!equal(sourceGovernance, savedGovernance)) throw new Error("The campaign response changed its governing policy.");
  } else if (!equal(governance(receipt), expected) || !equal(receipt.scenes, submitted.scenes)) {
    throw new Error("The brand response does not contain the submitted values and unchanged scene work.");
  }
  const cleanRaw = cleanAfterOperation(draft.cleanRaw, pending);
  return { ...draft, baseRevision: receipt.revision, baseProject: clone(receipt),
    cleanRaw, intent: !equal(draft.raw, cleanRaw), pending: null };
}
export function resolveBrandDraft(draft: BrandDraft, project: Project, choice: "saved" | "draft"): BrandDraft {
  owner(draft, project);
  if (brandUnknown(draft)) throw new Error("The original brand attempt remains UNKNOWN.");
  const saved = brandSavedRaw(project, draft.target), raw = choice === "saved" ? saved : draft.raw;
  return { ...draft, baseRevision: project.revision, baseProject: clone(project), cleanRaw: saved,
    raw: clone(raw), sequence: step(draft.sequence), intent: choice === "draft", pending: null };
}
function mergeOrigins(a: BrandOrigin[], b: BrandOrigin[]) {
  const result = clone(a);
  for (const value of b) {
    const prior = result.find(origin => origin.key === value.key);
    if (prior && prior.raw !== value.raw) throw new Error("Two different byte sources claim the same brand recovery key.");
    if (!prior) result.push(clone(value));
  }
  if (result.length > MAX_ORIGINS) throw new Error("The retained uncertain brand history reached its bounded limit.");
  return result;
}
export function recoverBrandRead(current: BrandDraft, recovered: BrandDraft, edited: boolean): BrandDraft {
  if (current.scope !== recovered.scope || current.target !== recovered.target) throw new Error("Brand recovery cannot cross owners.");
  const attempt = (pending: BrandPending | null) => pending && { ...pending, outcome: "UNKNOWN", diagnostic: null };
  if (current.pending && !equal(attempt(current.pending), attempt(recovered.pending)))
    throw new Error("Another unresolved brand request is already retained in this editor.");
  return { ...clone(recovered), raw: edited ? clone(current.raw) : clone(recovered.raw), intent: edited || recovered.intent,
    sequence: edited ? step(Math.max(current.sequence, recovered.sequence)) : recovered.sequence,
    proposal: recovered.proposal ? immutableProposal(recovered.proposal) : null,
    pending: recovered.pending ? { ...clone(recovered.pending), outcome: "UNKNOWN" } : null,
    origins: mergeOrigins(recovered.origins, current.origins) };
}
export interface BrandOutcome {
  scope: string; sequence: number; pending: BrandPending; project: Project; events: ProjectEvent[]; matchingRevisions: number[];
}
export const brandOutcomeMatches = (draft: BrandDraft, project: Project, value: BrandOutcome | null) => !!value
  && brandUnknown(draft) && value.scope === draft.scope && value.sequence === draft.sequence
  && brandScope(project, draft.target) === draft.scope && equal(value.pending, draft.pending)
  && sameProjectRevision(project, value.project) && equal(project, value.project);
export async function inspectBrandOutcome(draft: BrandDraft, selected: Project, transport: {
  readProject(id: string): Promise<Project>; readHistory(project: Project, after: number, limit: number): Promise<ProjectEvent[]>;
}): Promise<BrandOutcome> {
  owner(draft, selected);
  if (!brandUnknown(draft)) throw new Error("There is no uncertain brand request to inspect.");
  const pending = clone(draft.pending!), project = clone(await transport.readProject(selected.id));
  if (!sameProjectRevision(project, selected) || !equal(project, selected))
    throw new Error("Reload the saved Project before comparing the brand outcome.");
  if (!integer(project.revision) || project.revision < pending.revision || project.revision - pending.revision > MAX_HISTORY)
    throw new Error("The brand history exceeds its bounded inspection window.");
  const events: ProjectEvent[] = []; let cursor = pending.revision;
  do {
    const page = await transport.readHistory(project, cursor, 256);
    if (!Array.isArray(page) || page.length > 256 || (!page.length && cursor < project.revision))
      throw new Error("The saved brand history is incomplete.");
    for (const event of page) {
      if (!event || event.revision !== cursor + 1 || event.revision > project.revision
          || typeof event.change?.type !== "string" || typeof event.created_at !== "string" || !event.created_at)
        throw new Error("The saved brand history is not contiguous.");
      events.push(clone(event)); cursor = event.revision;
    }
  } while (cursor < project.revision);
  if (!equal(await transport.readProject(selected.id), project)) throw new Error("The saved Project changed during brand inspection.");
  return { scope: draft.scope, sequence: draft.sequence, pending, project, events,
    matchingRevisions: events.filter(event => equal(event.change, pending.change)).map(event => event.revision) };
}
export function separateBrandIntent(draft: BrandDraft, project: Project, review: BrandOutcome): BrandDraft {
  if (!brandOutcomeMatches(draft, project, review)) throw new Error("Read the current full Project and complete history before a separate brand edit.");
  if (project.revision <= draft.pending!.revision) throw new Error("The original request could still commit on its base. Keep it UNKNOWN.");
  const saved = brandSavedRaw(project, draft.target);
  return { ...draft, baseRevision: project.revision, baseProject: clone(project), cleanRaw: saved,
    pending: null, intent: true, sequence: step(draft.sequence) };
}
const governanceStrings = ["brandName", "forbidden", "accents", "wordmark", "tasteName", "sceneId", "ruleId", "campaign", "author", "rationale", "decision"] as const;
function rawShape(value: unknown): value is BrandRaw {
  if (!record(value)) return false;
  if (value.kind === "governance") return exact(value, ["kind", ...governanceStrings, "preferences"])
    && governanceStrings.every(key => typeof value[key] === "string") && Array.isArray(value.preferences)
    && value.preferences.length <= 64 && value.preferences.every(item => record(item) && exact(item, ["axis", "preference", "inferred"])
      && typeof item.axis === "string" && typeof item.preference === "string" && typeof item.inferred === "boolean");
  return value.kind === "campaign" && exact(value, ["kind", "campaign", "owner", "why", "sceneIds", "accent", "applyWordmark", "ackRebuild"])
    && ["campaign", "owner", "why", "accent"].every(key => typeof value[key] === "string")
    && Array.isArray(value.sceneIds) && value.sceneIds.length <= 8 && value.sceneIds.every(item => typeof item === "string")
    && new Set(value.sceneIds).size === value.sceneIds.length && typeof value.applyWordmark === "boolean" && typeof value.ackRebuild === "boolean";
}
function projectShape(value: unknown): value is Project {
  return record(value) && [value.id, value.generation, value.active_branch].every(projectionUuid)
    && integer(value.revision) && Array.isArray(value.scenes) && Array.isArray(value.assets) && Array.isArray(value.locks)
    && Array.isArray(value.reviews) && Array.isArray(value.deliverables) && record(value.audio)
    && (value.production_design === undefined || record(value.production_design));
}
function operationShape(value: unknown): value is BrandOperation {
  return typeof value === "string" ? ["brand", "taste", "exception", "revoke-all", "decision", "campaign"].includes(value)
    : record(value) && exact(value, ["revoke"]) && projectionUuid(value.revoke);
}
function preferenceShape(value: unknown) {
  return record(value) && exact(value, ["axis", "preference", "inferred"])
    && typeof value.axis === "string" && typeof value.preference === "string" && typeof value.inferred === "boolean";
}
function profileShape(value: unknown) {
  return record(value) && exact(value, ["id", "label", "version", "rules"])
    && projectionUuid(value.id) && typeof value.label === "string" && integer(value.version) && value.version > 0
    && Array.isArray(value.rules) && value.rules.length >= 1 && value.rules.length <= 64 && value.rules.every(rule => {
      if (!record(rule) || !projectionUuid(rule.id)) return false;
      if (rule.kind === "forbidden_phrase") return exact(rule, ["kind", "id", "phrase"]) && typeof rule.phrase === "string";
      if (rule.kind === "required_wordmark") return exact(rule, ["kind", "id", "text"]) && typeof rule.text === "string";
      return rule.kind === "allowed_accents" && exact(rule, ["kind", "id", "colors"])
        && Array.isArray(rule.colors) && rule.colors.length >= 1 && rule.colors.length <= 16 && rule.colors.every(color => typeof color === "string");
    });
}
function exceptionShape(value: unknown) {
  return record(value) && exact(value, ["id", "rule_id", "scene_id", "brand_sha256", "campaign", "author", "rationale"])
    && [value.id, value.rule_id, value.scene_id].every(projectionUuid) && sha(value.brand_sha256)
    && ["campaign", "author", "rationale"].every(key => typeof value[key] === "string");
}
function changeShape(value: unknown): value is BrandChange {
  if (!record(value)) return false;
  if (value.type === "set_brand_governance") return exact(value, ["type", "profile", "exceptions"])
    && (value.profile === null || profileShape(value.profile)) && Array.isArray(value.exceptions)
    && value.exceptions.length <= 128 && value.exceptions.every(exceptionShape);
  if (value.type === "set_taste_profile") return exact(value, ["type", "profile"]) && (value.profile === null
    || (record(value.profile) && exact(value.profile, ["id", "label", "preferences"])
      && projectionUuid(value.profile.id) && typeof value.profile.label === "string"
      && Array.isArray(value.profile.preferences) && value.profile.preferences.length <= 64 && value.profile.preferences.every(preferenceShape)));
  if (value.type === "record_creative_decision") return exact(value, ["type", "decision"]) && record(value.decision)
    && exact(value.decision, ["id", "scene_id", "author", "decision", "rationale"])
    && projectionUuid(value.decision.id) && projectionUuid(value.decision.scene_id)
    && ["author", "decision", "rationale"].every(key => typeof (value.decision as Record<string, unknown>)[key] === "string");
  return value.type === "apply_brand_campaign" && exact(value, ["type", "rollout"]) && rolloutShape(value.rollout);
}
function rolloutShape(value: unknown): value is BrandCampaignRollout {
  return record(value) && exact(value, ["schema", "project_id", "generation", "expected_revision", "brand_sha256", "campaign",
    "scene_ids", "accent", "wordmark", "reviewer_declaration", "rationale", "accepts_semantic_component_rebuild"])
    && value.schema === BRAND_ROLLOUT_SCHEMA && projectionUuid(value.project_id) && projectionUuid(value.generation)
    && integer(value.expected_revision) && sha(value.brand_sha256)
    && ["campaign", "reviewer_declaration", "rationale"].every(key => typeof value[key] === "string")
    && Array.isArray(value.scene_ids) && value.scene_ids.length >= 1 && value.scene_ids.length <= 8
    && value.scene_ids.every(projectionUuid) && new Set(value.scene_ids).size === value.scene_ids.length
    && (value.accent === null || typeof value.accent === "string") && (value.wordmark === null || typeof value.wordmark === "string")
    && value.accepts_semantic_component_rebuild === true;
}
function proposalShape(value: unknown, scope: string): value is BrandProposal {
  if (!record(value) || !exact(value, ["id", "scope", "sequence", "sourceRevision", "sourceSha256", "raw", "rollout", "preview"])
      || !projectionUuid(value.id) || value.scope !== scope || !integer(value.sequence) || !integer(value.sourceRevision)
      || !sha(value.sourceSha256) || !rawShape(value.raw) || value.raw.kind !== "campaign" || !rolloutShape(value.rollout)
      || value.rollout.expected_revision !== value.sourceRevision || !record(value.preview)) return false;
  const owner = brandIdentity(scope), preview = value.preview;
  return value.rollout.project_id === owner[0] && value.rollout.generation === owner[1]
    && exact(preview, ["schema", "project_id", "generation", "revision", "brand_sha256", "rows", "changed_count",
      "owner_review_required", "committed", "output_rendered", "publication_approved"])
    && preview.schema === BRAND_ROLLOUT_SCHEMA && preview.project_id === owner[0] && preview.generation === owner[1]
    && preview.revision === value.sourceRevision && preview.brand_sha256 === value.rollout.brand_sha256
    && Array.isArray(preview.rows) && preview.rows.length <= 8 && preview.rows.every(row => record(row)
      && exact(row, ["scene_id", "hero_id", "scene_name", "from_accent", "to_accent", "from_wordmark", "to_wordmark", "changed"])
      && projectionUuid(row.scene_id) && projectionUuid(row.hero_id)
      && ["scene_name", "from_accent", "to_accent", "from_wordmark", "to_wordmark"].every(key => typeof row[key] === "string")
      && typeof row.changed === "boolean")
    && integer(preview.changed_count) && preview.changed_count > 0 && preview.owner_review_required === true
    && preview.committed === false && preview.output_rendered === false && preview.publication_approved === false;
}
function originShape(value: unknown, scope: string): value is BrandOrigin {
  if (!record(value) || !exact(value, ["key", "raw"]) || typeof value.key !== "string" || typeof value.raw !== "string"
      || bytes(value.raw) > MAX_BRAND_COPY_BYTES) return false;
  const writer = value.key.slice(value.key.lastIndexOf(":") + 1);
  if (!projectionUuid(writer) || value.key !== brandCopyKey(scope, writer)) return false;
  const original: unknown = JSON.parse(value.raw);
  return record(original) && original.schema === "motionwright.brand-authoring-recovery/1"
    && original.scope === scope && original.writerId === writer && record(original.draft) && record(original.draft.pending)
    && ["UNKNOWN", "NOT_SENT"].includes(String(original.draft.pending.outcome));
}
function draftShape(value: unknown): value is BrandDraft {
  if (!record(value) || !exact(value, ["scope", "target", "baseRevision", "baseProject", "cleanRaw", "raw", "sequence", "intent", "proposal", "pending", "origins"])
      || typeof value.scope !== "string" || brandIdentity(value.scope)[3] !== value.target || !integer(value.baseRevision)
      || !projectShape(value.baseProject) || value.baseProject.revision !== value.baseRevision
      || brandScope(value.baseProject, value.target as BrandTarget) !== value.scope
      || !rawShape(value.raw) || !rawShape(value.cleanRaw) || value.raw.kind !== value.target || value.cleanRaw.kind !== value.target
      || !integer(value.sequence) || typeof value.intent !== "boolean"
      || (value.proposal !== null && (!proposalShape(value.proposal, value.scope) || value.target !== "campaign"))
      || !Array.isArray(value.origins) || value.origins.length > MAX_ORIGINS
      || !value.origins.every(origin => originShape(origin, value.scope as string))
      || new Set(value.origins.map(origin => (origin as BrandOrigin).key)).size !== value.origins.length) return false;
  const pending = value.pending;
  if (pending === null) return true;
  if (!record(pending) || !exact(pending, ["id", "revision", "sequence", "raw", "operation", "change", "sourceSha256", "outcome", "diagnostic"])
      || !projectionUuid(pending.id) || pending.revision !== value.baseRevision || !integer(pending.sequence)
      || pending.sequence > value.sequence || !rawShape(pending.raw) || pending.raw.kind !== value.target
      || !operationShape(pending.operation) || !changeShape(pending.change) || !sha(pending.sourceSha256)
      || !["UNKNOWN", "NOT_SENT"].includes(String(pending.outcome))
      || (pending.diagnostic !== null && typeof pending.diagnostic !== "string")) return false;
  const operation = pending.operation;
  return operation === "campaign" ? value.target === "campaign" && pending.change.type === "apply_brand_campaign"
    && value.proposal !== null && equal((value.proposal as BrandProposal).rollout, pending.change.rollout)
    : value.target === "governance" && (operation === "taste" ? pending.change.type === "set_taste_profile"
      : operation === "decision" ? pending.change.type === "record_creative_decision" : pending.change.type === "set_brand_governance");
}
export function encodeBrandCopy(draft: BrandDraft, writerId: string, updatedAt = Date.now()) {
  if (!projectionUuid(writerId) || !integer(updatedAt) || !draftShape(draft)) throw new Error("The brand recovery record is invalid.");
  const raw = JSON.stringify({ schema: "motionwright.brand-authoring-recovery/1", scope: draft.scope, writerId, updatedAt, draft } satisfies BrandCopy);
  if (bytes(raw) > MAX_BRAND_COPY_BYTES) throw new Error("This brand recovery copy exceeds 1 MiB. Export the retained input before closing.");
  return raw;
}
export function decodeBrandCopy(raw: string, scope: string, writerId?: string): BrandCopy {
  if (bytes(raw) > MAX_BRAND_COPY_BYTES) throw new Error("The brand recovery copy exceeds its size limit.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || JSON.stringify(value) !== raw || !exact(value, ["schema", "scope", "writerId", "updatedAt", "draft"])
      || value.schema !== "motionwright.brand-authoring-recovery/1" || value.scope !== scope || !projectionUuid(value.writerId)
      || (writerId !== undefined && value.writerId !== writerId) || !integer(value.updatedAt)
      || !draftShape(value.draft) || value.draft.scope !== scope)
    throw new Error("This brand recovery copy has an incompatible owner or shape. Its original bytes were kept.");
  const result = clone(value as unknown as BrandCopy);
  if (result.draft.pending) result.draft.pending.outcome = "UNKNOWN";
  if (result.draft.proposal) result.draft.proposal = immutableProposal(result.draft.proposal);
  return result;
}
export function readBrandPointer(storage: Pick<BrandStorage, "getItem">, scope: string, prior: { observed: boolean; value: string | null }) {
  brandIdentity(scope);
  const value = storage.getItem(brandPointerKey(scope));
  if (prior.observed && value !== prior.value) throw new Error("The earlier brand recovery pointer changed.");
  return { observed: true, value };
}
export function assertBrandOrigins(storage: Pick<BrandStorage, "getItem">, draft: BrandDraft) {
  for (const origin of draft.origins) if (storage.getItem(origin.key) !== origin.raw)
    throw new Error("An original uncertain brand copy changed. Current input remains available; saving is blocked.");
}
export function retainBrandOrigin(draft: BrandDraft, origin: BrandOrigin): BrandDraft {
  if (!draft.pending) return draft;
  const writer = origin.key.slice(origin.key.lastIndexOf(":") + 1), original = decodeBrandCopy(origin.raw, draft.scope, writer).draft;
  if (origin.key !== brandCopyKey(draft.scope, writer)
      || !equal(original, { ...draft, pending: { ...draft.pending, outcome: "UNKNOWN" } }))
    throw new Error("The brand attempt does not match its exact original copy.");
  const attempt = (pending: BrandPending) => ({ ...pending, outcome: "UNKNOWN", diagnostic: null });
  if (draft.origins.some(value => {
    const pending = (JSON.parse(value.raw) as BrandCopy).draft.pending;
    return pending !== null && equal(attempt(pending), attempt(draft.pending!));
  })) return draft;
  return { ...draft, origins: mergeOrigins(draft.origins, [origin]) };
}
export function writeBrandCopy(storage: BrandStorage, pointers: BrandStorage, draft: BrandDraft, custody: BrandCustody): string {
  const key = brandCopyKey(draft.scope, custody.writerId), pointer = brandPointerKey(draft.scope);
  const check = () => {
    assertBrandOrigins(storage, draft);
    if (storage.getItem(key) !== custody.expectedRaw || pointers.getItem(pointer) !== custody.expectedPointer)
      throw new Error("Another writer changed the brand copy or pointer. Earlier bytes were kept.");
  };
  check();
  const sameCopy = custody.expectedRaw !== null && equal(decodeBrandCopy(custody.expectedRaw, draft.scope, custody.writerId).draft, draft);
  if (sameCopy && custody.expectedPointer === custody.writerId) return custody.expectedRaw!;
  const raw = sameCopy ? custody.expectedRaw! : encodeBrandCopy(draft, custody.writerId);
  if (!sameCopy) {
    if (storage.length > MAX_KEYS) throw new Error("Brand recovery enumeration exceeds its bounded limit.");
    let total = bytes(raw), count = 1;
    for (let i = 0; i < storage.length; i++) {
      const other = storage.key(i);
      if (!other?.startsWith(BRAND_COPY_PREFIX) || other === key) continue;
      count++; total += bytes(storage.getItem(other) ?? "");
    }
    if (count > MAX_BRAND_COPIES || total > MAX_BRAND_RECOVERY_BYTES)
      throw new Error("Brand recovery storage is full. Export the retained input before closing.");
    check(); storage.setItem(key, raw);
    if (storage.getItem(key) !== raw) throw new Error("The brand recovery bytes were not confirmed. No project change may be sent.");
    custody.expectedRaw = raw;
  }
  if (pointers.getItem(pointer) !== custody.expectedPointer) throw new Error("The brand pointer changed before publication.");
  pointers.setItem(pointer, custody.writerId);
  if (pointers.getItem(pointer) !== custody.writerId) throw new Error("The brand recovery pointer was not confirmed.");
  custody.expectedPointer = custody.writerId;
  if (storage.getItem(key) !== raw) throw new Error("The brand recovery bytes changed during pointer publication.");
  assertBrandOrigins(storage, draft);
  return raw;
}
export function readBrandCopies(storage: BrandStorage, scope: string): { entries: BrandRecoveryEntry[]; limited: boolean } {
  brandIdentity(scope);
  const entries: BrandRecoveryEntry[] = [], limit = Math.min(storage.length, MAX_KEYS);
  for (let i = 0; i < limit; i++) {
    const key = storage.key(i);
    if (!key?.startsWith(BRAND_COPY_PREFIX + scope + ":")) continue;
    const writerId = key.slice((BRAND_COPY_PREFIX + scope + ":").length), raw = storage.getItem(key);
    if (raw === null) continue;
    try { entries.push({ key, writerId, raw, value: decodeBrandCopy(raw, scope, writerId), error: null }); }
    catch (reason) { entries.push({ key, writerId, raw, value: null, error: reason instanceof Error ? reason.message : String(reason) }); }
  }
  return { entries, limited: storage.length > MAX_KEYS };
}
