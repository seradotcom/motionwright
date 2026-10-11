import type { Project } from "./types";

export type BrandRule =
  | { kind:"forbidden_phrase"; id:string; phrase:string }
  | { kind:"allowed_accents"; id:string; colors:string[] }
  | { kind:"required_wordmark"; id:string; text:string };
export interface BrandProfile { id:string; label:string; version:number; rules:BrandRule[]; }
export interface TastePreference { axis:string; preference:string; inferred:boolean; }
export interface TasteProfile { id:string; label:string; preferences:TastePreference[]; }
export interface BrandException {
  id:string; rule_id:string; scene_id:string; brand_sha256:string;
  campaign:string; author:string; rationale:string;
}
export interface CreativeDecision {
  id:string; scene_id:string; author:string; decision:string; rationale:string;
}
const hexColor=/^#[a-fA-F0-9]{6}$/;
const sha=/^[0-9a-f]{64}$/;
const encoder=new TextEncoder();
function required(value:string, max:number, name:string) {
  if (!value.trim() || encoder.encode(value).length>max || /[\u0000-\u0009\u000b-\u001f\u007f]/u.test(value)) throw new Error("Invalid "+name);
}
/** Match Rust serde_json struct + internally tagged BrandRule field order.
 * The hash is content-bound, never dependent on JavaScript insertion order.
 * Rule ARRAY order still participates in the authoritative policy digest.
 */
export async function brandDigest(profile:BrandProfile):Promise<string> {
  const rules=profile.rules.map(rule=>{
    switch(rule.kind) {
      case "forbidden_phrase": return {kind:rule.kind,id:rule.id,phrase:rule.phrase};
      case "allowed_accents": return {kind:rule.kind,id:rule.id,colors:rule.colors};
      case "required_wordmark": return {kind:rule.kind,id:rule.id,text:rule.text};
      default: throw new Error("Unsupported BrandProfile rule kind.");
    }
  });
  const canonical={id:profile.id,label:profile.label,version:profile.version,rules};
  const digest=await crypto.subtle.digest("SHA-256",encoder.encode(JSON.stringify(canonical)));
  return Array.from(new Uint8Array(digest), b=>b.toString(16).padStart(2,"0")).join("");
}
export function validateBrandProfile(profile:BrandProfile) {
  required(profile.label,160,"brand label");
  if (!Number.isSafeInteger(profile.version) || profile.version<=0 || profile.rules.length<1 || profile.rules.length>64) throw new Error("Brand needs positive version and 1..=64 rules.");
  const ids=new Set<string>();
  for (const rule of profile.rules) {
    if (ids.has(rule.id)) throw new Error("Duplicate brand rule id.");
    ids.add(rule.id);
    if (rule.kind==="forbidden_phrase") required(rule.phrase,128,"forbidden phrase");
    else if (rule.kind==="required_wordmark") required(rule.text,64,"required wordmark");
    else {
      if (rule.colors.length===0 || rule.colors.length>16 || rule.colors.some(c=>!hexColor.test(c)) || new Set(rule.colors.map(c=>c.toLowerCase())).size!==rule.colors.length) throw new Error("Brand palette needs distinct #RRGGBB colors.");
    }
  }
}
export function validateTasteProfile(taste:TasteProfile) {
  required(taste.label,160,"taste label");
  if (taste.preferences.length>64) throw new Error("Taste preference budget exceeded.");
  const seen=new Set<string>();
  for (const pref of taste.preferences) {
    required(pref.axis,80,"taste axis"); required(pref.preference,1000,"taste preference");
    if (seen.has(pref.axis.toLowerCase())) throw new Error("Duplicate taste axis.");
    seen.add(pref.axis.toLowerCase());
  }
}
/** Studio drafts mirror Rust brand checks on every project change. */
export async function validateBrandGovernance(project:Project):Promise<void> {
  const design=project.production_design;
  const brand=design?.brand_profile;
  const waivers=design?.brand_exceptions ?? [];
  const taste=design?.taste_profile;
  const decisions=design?.project_decisions ?? [];
  if (taste) validateTasteProfile(taste);
  if (waivers.length>128 || decisions.length>256) throw new Error("Governance collection budget exceeded.");
  const seen=new Set<string>();
  for (const decision of decisions) {
    required(decision.author,160,"decision author");
    required(decision.decision,1000,"creative decision");
    required(decision.rationale,2000,"decision rationale");
    if (seen.has(decision.id) || !project.scenes.some(s=>s.id===decision.scene_id)) throw new Error("Duplicate or orphaned creative decision.");
    seen.add(decision.id);
  }
  if (!brand) {
    if (waivers.length) throw new Error("Brand exceptions require a profile.");
    return;
  }
  validateBrandProfile(brand);
  const digest=await brandDigest(brand);
  seen.clear();
  for (const waiver of waivers) {
    required(waiver.campaign,160,"campaign scope");
    required(waiver.author,160,"exception author");
    required(waiver.rationale,2000,"exception rationale");
    if (!sha.test(waiver.brand_sha256) || waiver.brand_sha256!==digest ||
        !brand.rules.some(r=>r.id===waiver.rule_id) ||
        !project.scenes.some(s=>s.id===waiver.scene_id) || seen.has(waiver.id))
      throw new Error("Exception has duplicate id, missing scene/rule or stale policy digest.");
    seen.add(waiver.id);
  }
  for (const scene of project.scenes) {
    const hero=design?.heroes.find(h=>h.scene_id===scene.id);
    for (const rule of brand.rules) {
      if (waivers.some(e=>e.scene_id===scene.id && e.rule_id===rule.id)) continue;
      let violation=false;
      if (rule.kind==="forbidden_phrase") {
        const needle=rule.phrase.toLowerCase();
        violation=scene.nodes.some(n=>n.text?.toLowerCase().includes(needle)) ||
          (!!hero && [hero.config.eyebrow,hero.config.headline,hero.config.body,hero.config.wordmark].some(v=>v.toLowerCase().includes(needle)));
      } else if (hero && rule.kind==="allowed_accents") {
        const allowed=(v:string)=>rule.colors.some(c=>c.toLowerCase()===v.toLowerCase());
        violation=!allowed(hero.config.accent) ||
          scene.nodes.filter(n=>n.id===hero.baseline.find(b=>b.name.endsWith(" / wordmark"))?.id ||
                                n.id===hero.baseline.find(b=>b.name.endsWith(" / rule"))?.id ||
                                (hero.config.layout==="metric_evidence" &&
                                 n.id===hero.baseline.find(b=>b.name.endsWith(" / headline"))?.id))
            .some(n=>n.style.fill!==null && !allowed(n.style.fill));
      } else if (hero && rule.kind==="required_wordmark") {
        const wordmark=hero.baseline.find(n=>n.name.endsWith(" / wordmark"));
        violation=hero.config.wordmark!==rule.text ||
          (!wordmark || !scene.nodes.some(n=>n.id===wordmark.id && n.text===rule.text));
      }
      if (violation) throw new Error(`Brand rule ${rule.id} violated in scene ${scene.id}; scoped campaign exception required.`);
    }
  }
}
