import type {NativeAsset,NativeCanvas,CreativeWorkspaceEdit,NativeSceneDifference} from './nativeTypes';

export type RecipeBackend='native_html'|'blender_stage'|'audio_score';
export type Locale='en'|'es'|'de';
export type ColorRole='background'|'surface'|'text'|'muted_text'|'accent'|'secondary'|'warning';
export interface RecipeDefinition {
 id:string;canonical_id:string;version:number;family:string;title:string;purpose:string;
 backend:RecipeBackend;requires_data:boolean;requires_media:boolean;status:string;fixture_contract:string
}
export interface CreativeKit {id:string;version:number;intent:string;recipes:string[];constraints:string[]}
export interface BrandColor {name:string;value:string;role:ColorRole}
export interface BrandProfile {
 id:string;revision:number;name:string;colors:BrandColor[];font:{kind:'sans'|'mono'}|{kind:'asset';asset_id:string;family:string};
 font_asset:NativeAsset|null;logo_asset:NativeAsset|null;body_weight:number;display_weight:number;
 minimum_body_size:number;minimum_margin_ratio:number;require_product_capture_provenance:boolean;
 forbidden_claims:string[];allowed_locales:Locale[];source_note:string
}
export interface TasteProfile {
 id:string;name:string;revision:number;motion_energy:number;density:number;contrast:number;
 minimum_hold_ms:number;preferred_kit:string;avoid:string[];reference_constraints:string[]
}
export interface CopyPack {eyebrow:string;headline:string;body:string;label_a:string;label_b:string;disclosure:string}
export interface DataRow {label:string;value:number;lower:number|null;upper:number|null;comparison:number|null}
export interface DataSeries {
 id:string;title:string;source_title:string;source_uri:string|null;observed_at:string;
 method:string;origin:'observed_source'|'user_provided'|'synthetic_fixture';
 unit:{kind:string;code?:string;label?:string};precision:number;sample_size:number|null;
 rows:DataRow[];content_sha256:string
}
export interface ProceduralOptions {
 count:number;columns:number;influence_x:number;influence_y:number;amplitude:number;falloff:number;spacing:number;
 path:Array<{x:number;y:number}>
}
export interface ComponentRequest {
 instance_id:string;recipe:string;version:1;output:NativeCanvas;copy:CopyPack;locale:Locale;seed:number;motion:boolean;
 data:DataSeries|null;primary_asset:NativeAsset|null;secondary_asset:NativeAsset|null;procedural:ProceduralOptions
}
export interface CreativeCatalog {
 schema:'motionwright.creative-library-catalog/1';recipes:RecipeDefinition[];kits:CreativeKit[];
 default_brand:BrandProfile;default_taste:TasteProfile;default_copy:Record<Locale,CopyPack>;
 default_data:DataSeries;source_authority:string;creative_approval:string
}
export interface CreativeComponentProposal {
 component_id:string;recipe:string;recipe_version:number;input_sha256:string;source_sha256:string;
 brand_id:string;brand_revision:number;taste_id:string;taste_revision:number;
 source_classification:string;creative_approval:string;renderer_plan:{backend:RecipeBackend;source:unknown};
 fidelity_reports:RealizationFidelity[];
 skill_audit:SkillAudit;
 committed:false;authority:string;normalized_edit?:CreativeWorkspaceEdit;difference?:NativeSceneDifference
}

export type FidelityState='native'|'translated'|'baked'|'approximated'|'unavailable';
export type RealizationTarget='hyperframes'|'blender'|'motion_canvas'|'mlt_video'|'manim_community'|'fframes_experimental'|'original_pcm_wav';
export interface PropertyFidelity {
 feature:string;state:FidelityState;original_source_retained:boolean;target_editable:boolean;
 source_explanation:string;loss_or_limitation:string
}
export interface RealizationFidelity {
 schema:'motionwright.realization-fidelity/1';component_id:string;recipe:string;source_sha256:string;
 target:RealizationTarget;native_source_retained:boolean;
 project_level_source_admission_verified:boolean;renderer_readback_verified:boolean;
 creative_quality_approved:boolean;score:null;
 properties:PropertyFidelity[];executable_without_separate_owner_grant:boolean;summary:string
}

export type CreativeSkillId='direction'|'causal-story'|'typography'|'motion'|'product-stage'|'capture'|'sound'|'responsive'|'critic'|'repair'|'distillation'|'delivery';
export interface CreativeSkillDefinition {
  id:CreativeSkillId;canonical_id:string;version:string;status:string;work_product:string;
  review_condition:string;may_grant_execution:boolean;may_approve_own_work:boolean
}
export interface SkillFinding {
  skill:CreativeSkillId;kind:'technical_preflight'|'unverified_evidence'|'human_creative_decision'|'scope_conflict';
  object_id:string|null;source_frame_start:number|null;source_frame_end:number|null;
  observation:string;risk:string;scoped_next_action:string;evidence_level:string
}
export interface SkillAudit {
  schema:'motionwright.creative-skill-advisory/1';component_id:string;recipe:string;
  input_sha256:string;source_sha256:string;
  skill_definitions:CreativeSkillDefinition[];findings:SkillFinding[];
  artifact_classification:string;standalone_runtime_authority:false;
  independent_human_approval:false;source_changed:false
}
