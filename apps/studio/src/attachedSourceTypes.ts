export type AttachedDisposition='structured_observable'|'typed_editable'|'opaque_preserved'|'unavailable';
export interface AttachedProperty {
 path:string;disposition:AttachedDisposition;count:number|null;note:string
}
export interface PreservedGlbChunk {
 source_offset:number;bytes:number;chunk_type:string;sha256:string;
 semantic_editability:AttachedDisposition
}
export interface AttachedSourceInspection {
 schema:'motionwright.attached-native-source-observation/1';
 capsule_id:string;scene_id:string;source_asset_id:string;source_sha256:string;
 source_bytes:number;declared_media_type:string;source_format:string;
 properties:AttachedProperty[];glb_chunks:PreservedGlbChunk[];
 used_extensions:string[];required_extensions:string[];unknown_root_fields:string[];
 original_binary_preserved:true;unknown_extensions_preserved:true;
 semantically_editable_by_motionwright:false;project_snapshot_was_modified:false;
 runtime_or_imported_code_executed:false;native_renderer_fidelity_observed:false;
 human_creative_approval:false;external_rights_or_install_granted:false
}
export interface AttachedInspectionResponse {
 schema:'motionwright.native-attachment-inspection/1';
 observation:AttachedSourceInspection;project_revision:number;
 owner_source_bytes_exported:false;project_changed:false;
 operation:'read_only_attach_first';owner_runtime_granted:false;
 source_semantically_imported:false
}
