import type { RationalTime } from '../types';
export interface NativeRate {num:number;den:number}
export interface NativeCanvas {width:number;height:number;rate:NativeRate;frames:number;background:string|null}
export type NativeProperty='x'|'y'|'width'|'height'|'scale_x'|'scale_y'|'rotation'|'opacity'|'blur'|'clip_top'|'clip_right'|'clip_bottom'|'clip_left';
export type NativeNodeField='content'|'appearance'|'parent'|'keyframes'|'metadata';
export type NativeCurve={kind:'hold'|'linear'|'ease_out_cubic'|'ease_in_out'}|{kind:'cubic_bezier';x1:number;y1:number;x2:number;y2:number};
export interface NativeKeyframe{frame:number;subframe?:{num:number;den:number};property:NativeProperty;value:number;curve:NativeCurve}
export interface NativePose{x:number;y:number;width:number;height:number;scale_x:number;scale_y:number;rotation:number;opacity:number;z_index:number}
export interface NativePoint{x:number;y:number}
export type NativeFont={kind:'sans'|'mono'}|{kind:'asset';asset_id:string;family:string};
export interface NativeTextRun{text:string;color:string;weight:number;italic:boolean}
export type NativeContent=
 |{kind:'group'}
 |{kind:'text';runs:NativeTextRun[];font:NativeFont;size:number;line_height:number;align:'left'|'center'|'right'}
 |{kind:'rectangle';fill:string;stroke:string|null;stroke_width:number;radius:number}
 |{kind:'ellipse';fill:string;stroke:string|null;stroke_width:number}
 |{kind:'path';points:NativePoint[];closed:boolean;fill:string|null;stroke:string;stroke_width:number}
 |{kind:'image';asset_id:string;fit:'contain'|'cover'|'stretch'}
 |{kind:'video';asset_id:string;fit:'contain'|'cover'|'stretch';source_start_frame:number;source_rate:NativeRate};
export type NativeClip={kind:'none'}|{kind:'inset';top:number;right:number;bottom:number;left:number;radius:number}|{kind:'circle';radius:number;center_x:number;center_y:number}|{kind:'polygon';points:NativePoint[]};
export interface NativeNode{id:string;name:string;parent_id:string|null;pose:NativePose;content:NativeContent;blend:'normal'|'multiply'|'screen'|'overlay'|'difference'|'lighten'|'darken';clip:NativeClip;effects:{blur:number;shadow:{x:number;y:number;blur:number;color:string}|null};keyframes:NativeKeyframe[];locked_properties:NativeProperty[];locked_fields?:NativeNodeField[]}
export interface NativeAsset{id:string;sha256:string;kind:'png'|'jpeg'|'mp4'|'woff2';rights:{owner:string;license:string;attribution:string;use_authorized:boolean;redistribute:boolean}}
export interface HyperframesDocument{version:1;canvas:NativeCanvas;camera:{x:number;y:number;zoom:number;rotation:number;keyframes:NativeKeyframe[]};nodes:NativeNode[];assets:NativeAsset[]}
export interface NativeSceneDocument{id:string;scene_id:string;profile_id:string;label:string;source:{renderer:'hyperframes';document:HyperframesDocument};source_capsule_id:string|null}
export interface CreativeWorkspace{native_scenes:NativeSceneDocument[]}
export type NativeProtection={kind:'property';property:NativeProperty}|{kind:'field';field:NativeNodeField};
export type CreativeWorkspaceEdit=
 |{kind:'upsert_native_scene';scene:NativeSceneDocument;expected_source_sha256:string|null}
 |{kind:'remove_native_scene';id:string;expected_source_sha256:string}
 |{kind:'set_native_protection';id:string;node_id:string;protection:NativeProtection;locked:boolean;expected_source_sha256:string;rationale:string};
export interface NativeSceneDifference{document_id:string;source_sha256:string|null;proposed_source_sha256:string|null;scene_id:string;profile_id:string;added_nodes:string[];removed_nodes:string[];changed_nodes:string[];camera_changed:boolean;canvas_changed:boolean;asset_dependencies_changed:boolean;dirty_start:RationalTime;dirty_end:RationalTime;evidence_level:string}
export interface NativeArtifact{relative_path:string;sha256:string;bytes:number;media_type:string}
export interface HyperframesRenderEvidence{project_id:string;generation:string;revision:number;scene_id:string;document_id:string;profile_id:string;job_ref:string;source_sha256:string;plan_sha256:string;rate:NativeRate;width:number;height:number;frame_count:number;alpha:boolean;color:string;frames:NativeArtifact;mezzanine:NativeArtifact;source:NativeArtifact;document:NativeArtifact;observations:NativeArtifact;runtime_receipt_sha256:string;creative_approval:'required';preview:Array<{token:string;segment_id:string;scene_ids:string[];frame_count:number}>}

export type PoseRepairProperty='x'|'y'|'width'|'height'|'scale_x'|'scale_y'|'rotation'|'opacity';
export type NativeRepairOperation=
 |{kind:'set_pose';node_id:string;property:PoseRepairProperty;expected:number;next:number}
 |{kind:'set_blur';node_id:string;expected:number;next:number}
 |{kind:'replace_text_run';node_id:string;run_index:number;expected_text:string;next_text:string}
 |{kind:'set_keyframe_value';node_id:string;property:NativeProperty;frame:number;
   subframe:{num:number;den:number}|null;expected:number;next:number};
export interface LocalizedNativeRepair {
 schema:'motionwright.native-repair-proposal/1';expected_source_sha256:string;proposed_source_sha256:string;
 description:string;changed_nodes:string[];changed_properties:string[];
 dirty_first_frame:number;dirty_end_frame_exclusive:number;dirty_reason:string;
 document:HyperframesDocument;committed:false;runtime_executed:false;
 renderer_equivalence_checked:false;creative_approval:'human_approval_required'
}
export interface LocalizedNativeRepairPreflight {
 schema:'motionwright.native-localized-repair-preflight/1';proposal:LocalizedNativeRepair;
 difference:NativeSceneDifference;normalized_edit:CreativeWorkspaceEdit;committed:false;rendered:false;
 requires_owner_approval:true;authority:string
}
