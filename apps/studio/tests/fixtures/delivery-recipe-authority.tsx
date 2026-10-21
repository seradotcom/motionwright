import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import DeliveryProfiles from "../../src/DeliveryProfiles";
import NativeOriginalMixReview from "../../src/NativeOriginalMixReview";
import { applyChange } from "../../src/api";
import { defaultOriginalMix } from "../../src/deliveryRecipe";
import { fixtureBootstrap } from "../../src/fixture";
import { ProjectMutationGate } from "../../src/projectMutation";
import type { Change, Project } from "../../src/types";

export interface RecipeFixtureRequest { id: number; source: Project; change: Change; settled: boolean }
export interface DeliveryRecipeAuthorityHarness {
  project(): Project;
  requests(): RecipeFixtureRequest[];
  show(group: "film" | "mix"): Promise<void>;
  setScope(patch: Partial<Pick<Project, "id" | "generation" | "active_branch">>): Promise<void>;
  externalChange(change: Change): Promise<void>;
  resolve(id: number): Promise<void>;
  reject(id: number, reason: string): Promise<void>;
  unmount(): Promise<void>;
  mount(): Promise<void>;
}
declare global { interface Window { __DELIVERY_RECIPE_AUTHORITY__: DeliveryRecipeAuthorityHarness } }

const host = document.getElementById("delivery-recipe-root");
if (!host) throw new Error("Delivery recipe fixture root is missing.");
const root = createRoot(host), gate = new ProjectMutationGate();
let project: Project = structuredClone(fixtureBootstrap.project);
// Seed through the current browser API so its schema, revisions and read history
// are real browser-adapter behavior. No Project schema is assigned by this fixture.
for (const profile of project.deliverables.slice(0, 2)) project = await applyChange(project, {
  type: "set_delivery_recipe", deliverable_profile_id: profile.id,
  recipe: { schema: "motionwright.delivery-recipe/1", original_mix: defaultOriginalMix(),
    scene_intents: project.scenes.filter(scene => scene.renderer === "motion-canvas").map((scene, index) => ({
      scene_id: scene.id, role: index === 0 ? "hook" : "problem", archetype: index === 0 ? "statement" : "diagram_build",
    })) },
});
let group: "film" | "mix" = new URL(location.href).searchParams.get("group") === "mix" ? "mix" : "film";
let mounted = true;
const requests: RecipeFixtureRequest[] = [];
const replies = new Map<number, { resolve(project: Project): void; reject(reason: Error): void }>();
const paint = () => new Promise<void>(done => requestAnimationFrame(() => requestAnimationFrame(() => done())));
const commit = (change: Change): Promise<Project> => gate.run(project, () => project, async source => {
  const request = { id: requests.length + 1, source: structuredClone(source), change: structuredClone(change), settled: false };
  requests.push(request);
  return new Promise<Project>((resolve, reject) => replies.set(request.id, { resolve, reject }));
}, saved => { project = saved; render(); });
const legacyCommit = async (change: Change) => { await commit(change); };
const unexpectedNative = () => { throw new Error("Native results are outside this component/browser fixture."); };

function render() {
  flushSync(() => root.render(<StrictMode>
    <header><p role="note">Actual Delivery components and browser API. Reply timing is controlled for regression tests. No StudioService, SQLite, Tauri, Film render or PCM result is claimed.</p></header>
    {mounted && (group === "film" ? <DeliveryProfiles project={project} commit={legacyCommit} commitRecipe={commit}
      desktopMode={false} currentProjectState={() => ({ project, ...gate.activity })}
      renderEvidence={null} onRenderEvidence={unexpectedNative} avEvidence={null} onAvEvidence={unexpectedNative}
      exportEvidence={null} onExportEvidence={unexpectedNative} onOpenAudio={() => { group = "mix"; render(); }} />
      : <NativeOriginalMixReview project={project} desktopMode={false} commit={commit} />)}
  </StrictMode>));
}
function take(id: number) {
  const request = requests.find(request => request.id === id), reply = replies.get(id);
  if (!request || !reply) throw new Error("No pending recipe fixture reply " + id);
  replies.delete(id); request.settled = true;
  return { request, reply };
}
window.__DELIVERY_RECIPE_AUTHORITY__ = {
  project: () => structuredClone(project), requests: () => structuredClone(requests),
  async show(value) { group = value; render(); await paint(); },
  async setScope(patch) { project = { ...project, ...patch }; render(); await paint(); },
  async externalChange(change) { project = await applyChange(project, change); render(); await paint(); },
  async resolve(id) {
    const { request, reply } = take(id);
    try { reply.resolve(await applyChange(request.source, request.change)); }
    catch (failure) { reply.reject(failure instanceof Error ? failure : new Error(String(failure))); }
    await paint();
  },
  async reject(id, reason) { take(id).reply.reject(new Error(reason)); await paint(); },
  async unmount() { mounted = false; render(); await paint(); },
  async mount() { mounted = true; render(); await paint(); },
};
render();
