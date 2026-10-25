#[cfg(test)]
use crate::semantic_graph_source_fixture as graph_source_guard_fixture;

use super::*;

#[path = "blender_product_cycle.rs"]
mod cycle;
#[path = "blender_product_lineage.rs"]
mod lineage;
use crate::blender_product_scene::{
    Binding, NativeCall, ObservedReference, ProductProjection, ProductScene, PropertyExpectation,
    SHELL_SEGMENTS, SemanticStep,
};
pub use cycle::{
    BLENDER_PRODUCT_RECIPE_MEDIA_TYPE, BlenderProductCycleJournal, BlenderProductCycleRequest,
    decode_blender_product_recipe,
};
pub use lineage::{
    BlenderProductAnnotationEdit, BlenderProductAssetBinding, BlenderProductAttemptProjection,
    BlenderProductAttemptRequest, BlenderProductAttemptState, BlenderProductAttemptSummary,
    BlenderProductLineageSnapshot, BlenderProductNativeBinding, BlenderProductSourceScope,
    BlenderProductSourceSnapshot, read_blender_product_attempt, read_blender_product_source,
    realize_blender_product_cycle_with_connections,
};
use semwright_driver_blender::authoring::{
    AuthoringIntent, BlenderAuthoringSpec, NativeOperation, NativeSnapshot,
};
use semwright_semantic_composition as composition;

#[derive(Debug, Default)]
struct ProductReadEpoch {
    generation: Option<u64>,
    // Observations live only for one read phase or one pending mutation.
    // Every native getter still resolves the generation-bound reference in the SDK.
    bindings: BTreeMap<Binding, ObservedRna>,
}

#[derive(Debug, Clone)]
struct ObservedRna {
    reference: String,
    native_name: String,
    rna_type: Option<String>,
    index: Option<u64>,
    generation: u64,
}

fn checked_observed_reference(reference: &str, generation: u64) -> NativeResult<()> {
    let suffix = reference.rsplit_once('@').map(|(_, value)| value);
    if generation == 0
        || generation > i32::MAX as u64
        || !reference.starts_with("blender-rna/v1/")
        || !(16..=1536).contains(&reference.chars().count())
        || suffix.and_then(|value| value.parse::<u64>().ok()) != Some(generation)
    {
        return Err(backend(
            "Blender observation has an invalid semantic reference",
        ));
    }
    Ok(())
}

impl ProductReadEpoch {
    fn observe(&mut self, generation: u64) -> NativeResult<()> {
        if generation == 0 || generation > i32::MAX as u64 {
            return Err(backend(
                "Blender observation has an invalid semantic generation",
            ));
        }
        if self
            .generation
            .is_some_and(|expected| expected != generation)
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Blender semantic generation changed while binding one operation",
            ));
        }
        self.generation = Some(generation);
        Ok(())
    }
    fn reference(&mut self, reference: &str, generation: u64) -> NativeResult<()> {
        checked_observed_reference(reference, generation)?;
        self.observe(generation)
    }
}

impl ObservedRna {
    fn projection_reference(&self) -> ObservedReference {
        ObservedReference {
            reference: self.reference.clone(),
            native_name: Some(self.native_name.clone()),
        }
    }
}

impl<R: OriginalPhaseScope, P, J: OriginalCallJournal> OriginalSession<'_, R, P, J> {
    async fn product_root(
        &mut self,
        epoch: &mut ProductReadEpoch,
        root: &str,
        name: &str,
    ) -> NativeResult<ObservedRna> {
        let (reference, generation) = self.lookup(root, name).await?;
        epoch.reference(&reference, generation)?;
        Ok(ObservedRna {
            reference,
            native_name: name.to_owned(),
            rna_type: None,
            index: None,
            generation,
        })
    }

    async fn product_relations(
        &mut self,
        epoch: &mut ProductReadEpoch,
        parent: &ObservedRna,
        property: &str,
    ) -> NativeResult<Vec<ObservedRna>> {
        epoch.reference(&parent.reference, parent.generation)?;
        let (generation, pages) = self.relations(&parent.reference, property).await?;
        epoch.observe(generation)?;
        let mut references = BTreeSet::new();
        let mut indices = BTreeSet::new();
        Self::page_items(&pages)
            .map(|item| {
                let reference = native_string(item, "ref")?.to_owned();
                let name = native_string(item, "name")?.to_owned();
                let rna_type = native_string(item, "rna_type")?.to_owned();
                let index = item
                    .get("index")
                    .and_then(Value::as_u64)
                    .ok_or_else(|| backend("Blender relation omitted its observed index"))?;
                epoch.reference(&reference, generation)?;
                if !references.insert(reference.clone()) || !indices.insert(index) {
                    return Err(backend("Blender relation contains duplicate observations"));
                }
                Ok(ObservedRna {
                    reference,
                    native_name: name,
                    rna_type: Some(rna_type),
                    index: Some(index),
                    generation,
                })
            })
            .collect()
    }

    async fn product_only_relation(
        &mut self,
        epoch: &mut ProductReadEpoch,
        parent: &ObservedRna,
        property: &str,
        expected_type: Option<&str>,
    ) -> NativeResult<ObservedRna> {
        let mut rows = self.product_relations(epoch, parent, property).await?;
        if rows.len() != 1
            || expected_type.is_some_and(|kind| rows[0].rna_type.as_deref() != Some(kind))
        {
            return Err(backend(
                "Blender relation is absent, ambiguous, or has a different RNA type",
            ));
        }
        Ok(rows.remove(0))
    }

    async fn product_property(
        &mut self,
        epoch: &mut ProductReadEpoch,
        target: &ObservedRna,
        property: &str,
    ) -> NativeResult<Value> {
        epoch.reference(&target.reference, target.generation)?;
        let data = self
            .call(
                "driver.blender.semantic.property.get",
                json!({"ref": target.reference, "property": property}),
                false,
            )
            .await?;
        if data.get("ref").and_then(Value::as_str) != Some(target.reference.as_str())
            || data.get("property").and_then(Value::as_str) != Some(property)
            || data.pointer("/descriptor/id").and_then(Value::as_str) != Some(property)
            || !matches!(
                data.pointer("/descriptor/status").and_then(Value::as_str),
                Some("managed" | "read_only")
            )
        {
            return Err(backend(
                "Blender scalar getter does not match its fresh binding",
            ));
        }
        data.get("value")
            .cloned()
            .ok_or_else(|| backend("Blender scalar getter omitted its value"))
    }

    async fn product_custom_identity(
        &mut self,
        epoch: &mut ProductReadEpoch,
        target: &ObservedRna,
        required: &[(&str, &str)],
    ) -> NativeResult<()> {
        epoch.reference(&target.reference, target.generation)?;
        let (generation, pages) = self
            .pages(
                "driver.blender.semantic.custom.list",
                json!({"ref": target.reference}),
            )
            .await?;
        epoch.observe(generation)?;
        if pages
            .iter()
            .any(|page| page.get("ref").and_then(Value::as_str) != Some(target.reference.as_str()))
        {
            return Err(backend("Blender custom-property inventory changed target"));
        }
        let mut values = BTreeMap::new();
        for row in Self::page_items(&pages) {
            let key = native_string(row, "key")?;
            if values.insert(key, row).is_some() {
                return Err(backend("Blender custom-property identity is ambiguous"));
            }
        }
        for (key, expected) in required {
            let row = values.get(key).ok_or_else(|| {
                backend("Blender object lacks its observed SDK ownership property")
            })?;
            if row.get("status").and_then(Value::as_str) != Some("managed")
                || row.get("value").and_then(Value::as_str) != Some(*expected)
            {
                return Err(backend(
                    "Blender SDK ownership differs from the issued composition",
                ));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone)]
struct ProductRootAnchor {
    root: &'static str,
    native_name: String,
    required_identity: Vec<(String, String)>,
}
#[derive(Debug, Default, Clone)]
struct ProductAnchors {
    roots: BTreeMap<Binding, ProductRootAnchor>,
    collection: Option<ProductRootAnchor>,
}

#[derive(Debug, Clone)]
enum RnaSelection {
    Only,
    Type(&'static str),
    Name(&'static str),
    Index(u64),
    Properties(Vec<(&'static str, Value)>),
    KeyFrame(u32),
}
#[derive(Debug, Clone)]
struct RnaHop {
    property: &'static str,
    selection: RnaSelection,
}

fn only(property: &'static str) -> RnaHop {
    RnaHop {
        property,
        selection: RnaSelection::Only,
    }
}
fn of_type(property: &'static str, kind: &'static str) -> RnaHop {
    RnaHop {
        property,
        selection: RnaSelection::Type(kind),
    }
}
fn scalar_selection(property: &'static str, scalar: &'static str, value: Value) -> RnaHop {
    RnaHop {
        property,
        selection: RnaSelection::Properties(vec![(scalar, value)]),
    }
}
fn socket_path(kind: &'static str, side: &'static str, identifier: &'static str) -> Vec<RnaHop> {
    vec![
        only("node_tree"),
        of_type("nodes", kind),
        scalar_selection(side, "identifier", json!(identifier)),
    ]
}
fn action_key_path(path: &'static str, axis: u8, frame: u32) -> Vec<RnaHop> {
    vec![
        only("animation_data"),
        only("action"),
        only("layers"),
        only("strips"),
        only("channelbags"),
        RnaHop {
            property: "fcurves",
            selection: RnaSelection::Properties(vec![
                ("data_path", json!(path)),
                ("array_index", json!(axis)),
            ]),
        },
        RnaHop {
            property: "keyframe_points",
            selection: RnaSelection::KeyFrame(frame),
        },
    ]
}

fn product_binding_path(binding: &Binding) -> NativeResult<(Binding, Vec<RnaHop>)> {
    use Binding::*;
    let path = match binding {
        Scene | World | FontData | FontObject | FontInk | CoatingMaterial | Entity(_) => {
            (binding.clone(), vec![])
        }
        RenderSettings => (Scene, vec![only("render")]),
        ImageSettings => (Scene, vec![only("render"), only("image_settings")]),
        ViewSettings => (Scene, vec![only("view_settings")]),
        UnitSettings => (Scene, vec![only("unit_settings")]),
        Cycles => (Scene, vec![only("cycles")]),
        WorldBackgroundStrength => (
            World,
            socket_path("ShaderNodeBackground", "inputs", "Strength"),
        ),
        CameraData(entity) => (Entity(entity), vec![only("data")]),
        CameraDof(entity) => (Entity(entity), vec![only("data"), only("dof")]),
        BodyBevel => (
            Entity("vessel_body"),
            vec![of_type("modifiers", "BevelModifier")],
        ),
        CylinderCap { entity, index } => {
            if !matches!((*entity, *index), ("vessel_floor" | "vessel_base", 0 | 1)) {
                return Err(invalid(
                    "Product cylinder-cap binding is outside its fixed profile",
                ));
            }
            (
                Entity(entity),
                vec![
                    only("data"),
                    RnaHop {
                        property: "polygons",
                        selection: RnaSelection::Index(u64::from(*index)),
                    },
                ],
            )
        }
        CameraKey {
            entity,
            axis,
            frame,
        } => {
            if *axis > 2
                || !matches!(
                    (*entity, *frame),
                    ("camera_form", 1 | 300 | 600) | ("camera_surface", 601 | 1200)
                )
            {
                return Err(invalid(
                    "Product camera-key binding is outside its fixed profile",
                ));
            }
            (Entity(entity), action_key_path("location", *axis, *frame))
        }
        Marker(index) => {
            let name = match *index {
                0 => "Form",
                1 => "Surface",
                _ => {
                    return Err(invalid(
                        "Product camera-marker binding is outside its fixed profile",
                    ));
                }
            };
            (
                Scene,
                vec![RnaHop {
                    property: "timeline_markers",
                    selection: RnaSelection::Name(name),
                }],
            )
        }
        FontInkTree => (FontInk, vec![only("node_tree")]),
        FontInkPrincipled => (
            FontInk,
            vec![
                only("node_tree"),
                of_type("nodes", "ShaderNodeBsdfPrincipled"),
            ],
        ),
        FontInkEmission => (
            FontInk,
            vec![only("node_tree"), of_type("nodes", "ShaderNodeEmission")],
        ),
        FontInkEmissionColor => (
            FontInk,
            socket_path("ShaderNodeEmission", "inputs", "Color"),
        ),
        FontInkEmissionStrength => (
            FontInk,
            socket_path("ShaderNodeEmission", "inputs", "Strength"),
        ),
        FontInkEmissionOutput => (
            FontInk,
            socket_path("ShaderNodeEmission", "outputs", "Emission"),
        ),
        FontInkSurfaceInput => (
            FontInk,
            socket_path("ShaderNodeOutputMaterial", "inputs", "Surface"),
        ),
        FontVisibilityKey(frame) => {
            if !matches!(*frame, 1 | 600 | 601 | 1200) {
                return Err(invalid(
                    "Product FONT visibility key is outside its fixed profile",
                ));
            }
            (FontObject, action_key_path("hide_render", 0, *frame))
        }
        CoatingBaseColor => (
            CoatingMaterial,
            socket_path("ShaderNodeBsdfPrincipled", "inputs", "Base Color"),
        ),
        CoatingRoughness => (
            CoatingMaterial,
            socket_path("ShaderNodeBsdfPrincipled", "inputs", "Roughness"),
        ),
        CoatingMetallic => (
            CoatingMaterial,
            socket_path("ShaderNodeBsdfPrincipled", "inputs", "Metallic"),
        ),
    };
    Ok(path)
}

impl<R: OriginalPhaseScope, P, J: OriginalCallJournal> OriginalSession<'_, R, P, J> {
    async fn product_select(
        &mut self,
        epoch: &mut ProductReadEpoch,
        parent: &ObservedRna,
        hop: &RnaHop,
    ) -> NativeResult<ObservedRna> {
        let rows = self.product_relations(epoch, parent, hop.property).await?;
        let mut selected = Vec::new();
        for row in rows {
            let matches = match &hop.selection {
                RnaSelection::Only => true,
                RnaSelection::Type(kind) => row.rna_type.as_deref() == Some(*kind),
                RnaSelection::Name(name) => row.native_name == *name,
                RnaSelection::Index(index) => row.index == Some(*index),
                RnaSelection::Properties(fields) => {
                    let mut matches = true;
                    for (field, expected) in fields {
                        let actual = self.product_property(epoch, &row, field).await?;
                        matches &= original_value_matches(expected, &actual);
                    }
                    matches
                }
                RnaSelection::KeyFrame(frame) => {
                    let value = self.product_property(epoch, &row, "co").await?;
                    let coordinates = value
                        .as_array()
                        .filter(|items| items.len() == 2)
                        .ok_or_else(|| backend("Blender key does not expose two coordinates"))?;
                    let observed_frame = coordinates[0]
                        .as_f64()
                        .filter(|v| v.is_finite())
                        .ok_or_else(|| backend("Blender key frame is not finite"))?;
                    if coordinates[1].as_f64().filter(|v| v.is_finite()).is_none() {
                        return Err(backend("Blender key value is not finite"));
                    }
                    observed_frame == f64::from(*frame)
                }
            };
            if matches {
                selected.push(row);
            }
        }
        if selected.len() != 1 {
            return Err(backend(
                "Blender product relation selector is absent or ambiguous",
            ));
        }
        Ok(selected.remove(0))
    }

    async fn product_binding(
        &mut self,
        epoch: &mut ProductReadEpoch,
        anchors: &ProductAnchors,
        binding: &Binding,
    ) -> NativeResult<ObservedRna> {
        if let Some(observed) = epoch.bindings.get(binding).cloned() {
            epoch.reference(&observed.reference, observed.generation)?;
            return Ok(observed);
        }
        let (root, hops) = product_binding_path(binding)?;
        let mut current = if let Some(observed) = epoch.bindings.get(&root).cloned() {
            epoch.reference(&observed.reference, observed.generation)?;
            observed
        } else {
            let anchor = anchors.roots.get(&root).ok_or_else(|| {
                backend("Blender product root has no authenticated source observation")
            })?;
            let observed = self
                .product_root(epoch, anchor.root, &anchor.native_name)
                .await?;
            let identity = anchor
                .required_identity
                .iter()
                .map(|(key, value)| (key.as_str(), value.as_str()))
                .collect::<Vec<_>>();
            if !identity.is_empty() {
                self.product_custom_identity(epoch, &observed, &identity)
                    .await?;
            }
            epoch.bindings.insert(root, observed.clone());
            observed
        };
        for hop in hops {
            current = self.product_select(epoch, &current, &hop).await?;
        }
        if let Binding::CylinderCap { entity, index } = binding {
            let depth = match *entity {
                "vessel_floor" => 0.012,
                "vessel_base" => 0.008,
                _ => return Err(invalid("Product cap entity is unsupported")),
            };
            let sign = if *index == 0 { -1.0 } else { 1.0 };
            for (property, expected) in [
                ("loop_total", json!(SHELL_SEGMENTS)),
                ("normal", json!([0.0, 0.0, sign])),
                ("center", json!([0.0, 0.0, sign * depth / 2.0])),
            ] {
                let actual = self.product_property(epoch, &current, property).await?;
                if !original_value_matches(&expected, &actual) {
                    return Err(backend(
                        "Blender cap index does not identify the expected native polygon",
                    ));
                }
            }
        }
        epoch.bindings.insert(binding.clone(), current.clone());
        Ok(current)
    }
}

type ProductSdkPlan = composition::PreparedPlan<AuthoringIntent, NativeOperation>;
struct ValidatedProductSpec(BlenderAuthoringSpec);

impl ValidatedProductSpec {
    fn decode(projection: &ProductProjection) -> NativeResult<Self> {
        let spec: BlenderAuthoringSpec =
            serde_json::from_value(projection.composition_spec.clone()).map_err(|_| {
                Error::new(
                    ErrorCode::Unsupported,
                    "Product projection is not accepted by the pinned Blender SDK decoder",
                )
            })?;
        spec.validate().map_err(|_| {
            Error::new(
                ErrorCode::Unsupported,
                "Product projection is not accepted by the pinned Blender SDK validator",
            )
        })?;
        Ok(Self(spec))
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ProductIssuedPlan {
    plan_ref: String,
    plan: ProductSdkPlan,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ProductAppliedPlan {
    plan_ref: String,
    island: String,
    snapshot: NativeSnapshot,
    report: composition::VerificationReport,
}
struct ProductCompositionObservation {
    issued: ProductIssuedPlan,
    island: String,
    snapshot: NativeSnapshot,
    verification: composition::VerificationReport,
}
fn product_sdk_error() -> Error {
    backend("Blender SDK composition receipt does not match its issued product plan")
}

fn product_plan_island(plan: &ProductSdkPlan) -> NativeResult<String> {
    let island = match plan.body.changes.operations.first().map(|op| &op.payload) {
        Some(NativeOperation::Collection { island, .. }) => island.clone(),
        _ => return Err(product_sdk_error()),
    };
    composition::bounded_id(&island).map_err(|_| product_sdk_error())?;
    for operation in &plan.body.changes.operations {
        let observed = match &operation.payload {
            NativeOperation::Collection { island, .. }
            | NativeOperation::Texture { island, .. }
            | NativeOperation::Material { island, .. }
            | NativeOperation::Entity { island, .. }
            | NativeOperation::Relation { island, .. }
            | NativeOperation::Animation { island, .. } => island,
            NativeOperation::Transform { .. } | NativeOperation::MaterialSlots { .. } => {
                return Err(product_sdk_error());
            }
        };
        if observed != &island {
            return Err(product_sdk_error());
        }
    }
    Ok(island)
}

fn check_product_issued_plan(
    issued: &ProductIssuedPlan,
    spec: &ValidatedProductSpec,
    before: &NativeSnapshot,
) -> NativeResult<String> {
    composition::bounded_id(&issued.plan_ref).map_err(|_| product_sdk_error())?;
    let plan = &issued.plan;
    plan.body
        .owner
        .validate()
        .map_err(|_| product_sdk_error())?;
    plan.body
        .budget
        .validate()
        .map_err(|_| product_sdk_error())?;
    let expected_intent = serde_json::to_value(AuthoringIntent::Create {
        spec: spec.0.clone(),
    })
    .map_err(|_| product_sdk_error())?;
    let actual_intent = serde_json::to_value(&plan.body.intent).map_err(|_| product_sdk_error())?;
    if expected_intent != actual_intent
        || plan.body.contract_version != composition::CONTRACT_VERSION
        || plan.body.profile.id != "blender-native-authoring"
        || plan.body.profile.version != 1
        || plan.body.profile.intent_schema
            != composition::schema_digest::<AuthoringIntent>().map_err(|_| product_sdk_error())?
        || plan.body.profile.operation_schema
            != composition::schema_digest::<NativeOperation>().map_err(|_| product_sdk_error())?
        || plan.body.intent_digest
            != composition::canonical_digest(&plan.body.intent).map_err(|_| product_sdk_error())?
        || plan.digest
            != composition::canonical_digest(&plan.body).map_err(|_| product_sdk_error())?
        || plan.body.base
            != before
                .base(&plan.body.owner)
                .map_err(|_| product_sdk_error())?
        || plan.body.required_rules.is_empty()
        || plan.body.required_rules.len() > 256
        || plan.body.changes.operations.is_empty()
        || plan.body.changes.operations.len() > 512
        || plan.body.changes.operations.len() > plan.body.budget.max_operations as usize
    {
        return Err(product_sdk_error());
    }
    product_plan_island(plan)
}

fn check_product_snapshot(
    snapshot: &NativeSnapshot,
    owner: &composition::Owner,
    island: &str,
    native_session: &str,
) -> NativeResult<()> {
    snapshot.base(owner).map_err(|_| product_sdk_error())?;
    if snapshot.island.as_deref() != Some(island)
        || snapshot.native_session != native_session
        || snapshot.drift
    {
        return Err(product_sdk_error());
    }
    Ok(())
}

fn check_product_composition_report(
    report: &composition::VerificationReport,
    plan: &ProductSdkPlan,
    snapshot: &NativeSnapshot,
) -> NativeResult<()> {
    if report.validation.plan_digest != plan.digest
        || report.validation.required_rules != plan.body.required_rules
        || report.validation.base
            != snapshot
                .base(&plan.body.owner)
                .map_err(|_| product_sdk_error())?
        || report.execution_status != composition::ExecutionStatus::Completed
        || report.support_level != composition::SupportLevel::Native
        || !report.effects_unobservable.is_empty()
        || report.verdict().map_err(|_| product_sdk_error())? != composition::Verdict::Pass
    {
        return Err(product_sdk_error());
    }
    Ok(())
}

impl<R: OriginalPhaseScope, P, J: OriginalCallJournal> OriginalSession<'_, R, P, J> {
    async fn product_create_composition(
        &mut self,
        spec: &ValidatedProductSpec,
    ) -> NativeResult<ProductCompositionObservation> {
        let before: NativeSnapshot = serde_json::from_value(
            self.call("driver.blender.composition.inspect", json!({}), false)
                .await?,
        )
        .map_err(|_| product_sdk_error())?;
        let intent = AuthoringIntent::Create {
            spec: spec.0.clone(),
        };
        let issued: ProductIssuedPlan = serde_json::from_value(
            self.call(
                "driver.blender.composition.plan",
                json!({"intent": intent}),
                false,
            )
            .await?,
        )
        .map_err(|_| product_sdk_error())?;
        let island = check_product_issued_plan(&issued, spec, &before)?;
        let applied_data = self
            .call(
                "driver.blender.composition.apply",
                json!({"plan_ref": issued.plan_ref}),
                true,
            )
            .await?;
        let applied: ProductAppliedPlan =
            serde_json::from_value(applied_data).map_err(|_| product_sdk_error().uncertain())?;
        if applied.plan_ref != issued.plan_ref || applied.island != island {
            return Err(product_sdk_error().uncertain());
        }
        check_product_snapshot(
            &applied.snapshot,
            &issued.plan.body.owner,
            &island,
            &before.native_session,
        )
        .map_err(Error::uncertain)?;
        check_product_composition_report(&applied.report, &issued.plan, &applied.snapshot)
            .map_err(Error::uncertain)?;
        let verification: composition::VerificationReport = serde_json::from_value(
            self.call(
                "driver.blender.composition.verify",
                json!({"plan_ref": issued.plan_ref}),
                false,
            )
            .await?,
        )
        .map_err(|_| product_sdk_error())?;
        let snapshot: NativeSnapshot = serde_json::from_value(
            self.call(
                "driver.blender.composition.inspect",
                json!({"island": island}),
                false,
            )
            .await?,
        )
        .map_err(|_| product_sdk_error())?;
        check_product_snapshot(
            &snapshot,
            &issued.plan.body.owner,
            &island,
            &before.native_session,
        )?;
        check_product_composition_report(&verification, &issued.plan, &snapshot)?;
        Ok(ProductCompositionObservation {
            issued,
            island,
            snapshot,
            verification,
        })
    }
}

const PRODUCT_ENTITY_IDS: &[&str] = &[
    "vessel_body",
    "vessel_floor",
    "vessel_base",
    "studio_floor",
    "light_key",
    "target_key",
    "light_fill",
    "target_fill",
    "light_rim",
    "target_rim",
    "camera_form",
    "focus_form",
    "camera_surface",
    "focus_surface",
];

fn product_observed_name(value: &Value, key: &str) -> NativeResult<String> {
    let name = native_string(value, key)?;
    if name.chars().count() > 128 || name.contains('\0') {
        return Err(backend(
            "Blender product observation has an invalid native name",
        ));
    }
    Ok(name.to_owned())
}

fn install_composition_anchors(
    anchors: &mut ProductAnchors,
    spec: &ValidatedProductSpec,
    observation: &ProductCompositionObservation,
) -> NativeResult<()> {
    let expected = PRODUCT_ENTITY_IDS.iter().copied().collect::<BTreeSet<_>>();
    let declared = spec
        .0
        .entities
        .iter()
        .map(|entity| entity.id.as_str())
        .collect::<BTreeSet<_>>();
    if declared != expected
        || spec.0.entities.len() != expected.len()
        || observation.snapshot.items.len() != expected.len()
        || anchors.collection.is_some()
    {
        return Err(product_sdk_error());
    }
    let mut seen_ids = BTreeSet::new();
    let mut seen_names = BTreeSet::new();
    let mut collection_names = BTreeSet::new();
    let mut coating_names = BTreeSet::new();
    for row in &observation.snapshot.items {
        let id = native_string(row, "entity")?;
        let id = PRODUCT_ENTITY_IDS
            .iter()
            .copied()
            .find(|known| *known == id)
            .ok_or_else(product_sdk_error)?;
        let name = product_observed_name(row, "name")?;
        if !seen_ids.insert(id)
            || !seen_names.insert(name.clone())
            || row.get("native_island").and_then(Value::as_str) != Some(observation.island.as_str())
            || row.get("library").and_then(Value::as_bool) != Some(false)
        {
            return Err(product_sdk_error());
        }
        let collections = row
            .get("collections")
            .and_then(Value::as_array)
            .filter(|values| values.len() == 1)
            .ok_or_else(product_sdk_error)?;
        let collection = collections[0]
            .as_str()
            .filter(|name| !name.is_empty())
            .ok_or_else(product_sdk_error)?;
        if collection.chars().count() > 128 || collection.contains('\0') {
            return Err(product_sdk_error());
        }
        collection_names.insert(collection.to_owned());
        if let Some(materials) = row.get("materials").and_then(Value::as_array) {
            for material in materials {
                if material.get("id").and_then(Value::as_str) == Some("coating") {
                    if material.get("library").and_then(Value::as_bool) != Some(false) {
                        return Err(product_sdk_error());
                    }
                    coating_names.insert(product_observed_name(material, "name")?);
                }
            }
        }
        let anchor = ProductRootAnchor {
            root: "objects",
            native_name: name,
            required_identity: vec![
                ("sw_authoring_island".into(), observation.island.clone()),
                ("sw_authoring_entity".into(), id.into()),
            ],
        };
        if anchors.roots.insert(Binding::Entity(id), anchor).is_some() {
            return Err(product_sdk_error());
        }
    }
    if seen_ids != expected || collection_names.len() != 1 || coating_names.len() != 1 {
        return Err(product_sdk_error());
    }
    anchors.collection = Some(ProductRootAnchor {
        root: "collections",
        native_name: collection_names
            .into_iter()
            .next()
            .ok_or_else(product_sdk_error)?,
        required_identity: vec![("sw_authoring_island".into(), observation.island.clone())],
    });
    let coating = ProductRootAnchor {
        root: "materials",
        native_name: coating_names
            .into_iter()
            .next()
            .ok_or_else(product_sdk_error)?,
        required_identity: vec![
            ("sw_authoring_island".into(), observation.island.clone()),
            ("sw_authoring_material".into(), "coating".into()),
        ],
    };
    if anchors
        .roots
        .insert(Binding::CoatingMaterial, coating)
        .is_some()
    {
        return Err(product_sdk_error());
    }
    Ok(())
}

#[derive(Debug, Serialize)]
struct ProductMutationReceipt {
    sequence: u32,
    command: String,
    input: Value,
    semantic_generation_before: u64,
    semantic_generation_after: Option<u64>,
    native_data: Value,
}

fn same_observed_target(current: &str, previous: &str, generation: u64) -> NativeResult<()> {
    checked_observed_reference(current, generation).map_err(|_| mutation_ack_error())?;
    if current.rsplit_once('@').map(|(target, _)| target)
        != previous.rsplit_once('@').map(|(target, _)| target)
    {
        return Err(backend("Blender mutation ACK changed its bound RNA target").uncertain());
    }
    Ok(())
}

fn product_step_is_mutating(command: &str) -> bool {
    matches!(
        command,
        "driver.blender.render.settings"
            | "driver.blender.semantic.property.set"
            | "driver.blender.semantic.relation.set"
            | "driver.blender.semantic.datablock.create"
            | "driver.blender.semantic.object.create"
            | "driver.blender.animation.keyframe.insert"
            | "driver.blender.scene.marker.add"
            | "driver.blender.node.add"
            | "driver.blender.node.remove"
            | "driver.blender.node.link"
            | "driver.blender.material.assign"
    )
}

fn mutation_ack_error() -> Error {
    backend("Blender mutation ACK does not match the dispatched product operation").uncertain()
}

fn check_product_mutation_ack(
    call: &NativeCall,
    data: &Value,
    before: u64,
    bindings: &BTreeMap<Binding, ObservedRna>,
) -> NativeResult<Option<u64>> {
    if data.get("changed").and_then(Value::as_bool) != Some(true) {
        return Err(mutation_ack_error());
    }
    if call.operation == "driver.blender.render.settings" {
        if data.get("engine").and_then(Value::as_str) != Some("CYCLES")
            || data.get("device").and_then(Value::as_str) != Some("CPU")
            || data.get("backend") != Some(&Value::Null)
            || data.get("devices") != Some(&json!([]))
            || data.get("fallback") != Some(&Value::Null)
        {
            return Err(mutation_ack_error());
        }
        return Ok(None);
    }
    if call.operation == "driver.blender.material.assign" {
        return Ok(None);
    }
    let generation = native_generation(data).map_err(|_| mutation_ack_error())?;
    if generation == before || generation > i32::MAX as u64 {
        return Err(mutation_ack_error());
    }
    let same_ref = |output_key: &str, input_key: &str| -> NativeResult<()> {
        let current = native_string(data, output_key).map_err(|_| mutation_ack_error())?;
        let previous = native_string(&call.input, input_key).map_err(|_| mutation_ack_error())?;
        same_observed_target(current, previous, generation)
    };
    let equal_field = |output_key: &str, input_key: &str| -> bool {
        call.input.get(input_key).is_some() && data.get(output_key) == call.input.get(input_key)
    };
    match call.operation {
        "driver.blender.semantic.property.set" => {
            same_ref("ref", "ref")?;
            let expected = call.input.get("value").ok_or_else(mutation_ack_error)?;
            let actual = data.get("value").ok_or_else(mutation_ack_error)?;
            if !equal_field("property", "property") || !original_value_matches(expected, actual) {
                return Err(mutation_ack_error());
            }
        }
        "driver.blender.semantic.relation.set" | "driver.blender.semantic.relation.unlink" => {
            same_ref("ref", "ref")?;
            same_ref("target_ref", "target_ref")?;
            if !equal_field("property", "property") {
                return Err(mutation_ack_error());
            }
        }
        "driver.blender.semantic.datablock.create" => {
            checked_observed_reference(
                native_string(data, "ref").map_err(|_| mutation_ack_error())?,
                generation,
            )
            .map_err(|_| mutation_ack_error())?;
            if !equal_field("root", "root")
                || !equal_field("name", "name")
                || data
                    .get("rna_type")
                    .and_then(Value::as_str)
                    .is_none_or(str::is_empty)
            {
                return Err(mutation_ack_error());
            }
        }
        "driver.blender.semantic.object.create" => {
            checked_observed_reference(
                native_string(data, "ref").map_err(|_| mutation_ack_error())?,
                generation,
            )
            .map_err(|_| mutation_ack_error())?;
            same_ref("data_ref", "data_ref")?;
            if !equal_field("name", "name")
                || data.get("object_type").and_then(Value::as_str) != Some("FONT")
            {
                return Err(mutation_ack_error());
            }
        }
        "driver.blender.animation.keyframe.insert" => {
            same_ref("ref", "ref")?;
            let expected_frame = call
                .input
                .get("frame")
                .and_then(Value::as_f64)
                .filter(|value| value.is_finite())
                .ok_or_else(mutation_ack_error)?;
            let actual_frame = data
                .get("frame")
                .and_then(Value::as_f64)
                .filter(|value| value.is_finite())
                .ok_or_else(mutation_ack_error)?;
            if !equal_field("property", "property")
                || !equal_field("index", "index")
                || actual_frame != expected_frame
            {
                return Err(mutation_ack_error());
            }
        }
        "driver.blender.scene.marker.add" => {
            checked_observed_reference(
                native_string(data, "ref").map_err(|_| mutation_ack_error())?,
                generation,
            )
            .map_err(|_| mutation_ack_error())?;
            if !equal_field("name", "name") || !equal_field("frame", "frame") {
                return Err(mutation_ack_error());
            }
        }
        "driver.blender.node.add" => {
            checked_observed_reference(
                native_string(data, "ref").map_err(|_| mutation_ack_error())?,
                generation,
            )
            .map_err(|_| mutation_ack_error())?;
            if !equal_field("name", "name") || !equal_field("node_type", "type") {
                return Err(mutation_ack_error());
            }
        }
        "driver.blender.node.remove" => {
            let tree = bindings
                .get(&Binding::FontInkTree)
                .ok_or_else(mutation_ack_error)?;
            same_observed_target(
                native_string(data, "tree_ref").map_err(|_| mutation_ack_error())?,
                &tree.reference,
                generation,
            )?;
        }
        "driver.blender.node.link" => {
            checked_observed_reference(
                native_string(data, "ref").map_err(|_| mutation_ack_error())?,
                generation,
            )
            .map_err(|_| mutation_ack_error())?;
        }
        _ => return Err(mutation_ack_error()),
    }
    Ok(Some(generation))
}

fn record_product_created_root(
    anchors: &mut ProductAnchors,
    step: &SemanticStep,
    call: &NativeCall,
    data: &Value,
) -> NativeResult<()> {
    let Some(binding) = step.result_binding() else {
        return Ok(());
    };
    let root = match binding {
        Binding::World => "worlds",
        Binding::FontData => "curves",
        Binding::FontObject => "objects",
        Binding::FontInk => "materials",
        Binding::Marker(_) | Binding::FontInkEmission => return Ok(()),
        _ => return Err(mutation_ack_error()),
    };
    if !matches!(
        call.operation,
        "driver.blender.semantic.datablock.create" | "driver.blender.semantic.object.create"
    ) {
        return Err(mutation_ack_error());
    }
    let name = product_observed_name(data, "name").map_err(|_| mutation_ack_error())?;
    let anchor = ProductRootAnchor {
        root,
        native_name: name,
        required_identity: vec![],
    };
    if anchors.roots.insert(binding.clone(), anchor).is_some() {
        return Err(mutation_ack_error());
    }
    Ok(())
}

impl<R: OriginalPhaseScope, P, J: OriginalCallJournal> OriginalSession<'_, R, P, J> {
    async fn product_active_scene(
        &mut self,
        epoch: &mut ProductReadEpoch,
        anchors: &ProductAnchors,
    ) -> NativeResult<ObservedRna> {
        let scene = self
            .product_binding(epoch, anchors, &Binding::Scene)
            .await?;
        let inspected = self
            .call("driver.blender.scene.inspect", json!({}), false)
            .await?;
        if inspected.get("name").and_then(Value::as_str) != Some(scene.native_name.as_str()) {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "The active Blender Scene differs from the owned product Scene",
            ));
        }
        Ok(scene)
    }

    async fn product_run_step(
        &mut self,
        anchors: &mut ProductAnchors,
        step: &SemanticStep,
    ) -> NativeResult<ProductMutationReceipt> {
        if !product_step_is_mutating(step.operation()) {
            return Err(invalid(
                "Product mutation step is outside the closed projection",
            ));
        }
        let mut epoch = ProductReadEpoch::default();
        let scene = self.product_active_scene(&mut epoch, anchors).await?;
        let mut bindings = BTreeMap::from([(Binding::Scene, scene)]);
        let mut required = step
            .required_bindings()
            .into_iter()
            .cloned()
            .collect::<BTreeSet<_>>();
        if step.operation() == "driver.blender.node.remove" {
            required.insert(Binding::FontInkTree);
        }
        for binding in required {
            if let std::collections::btree_map::Entry::Vacant(entry) =
                bindings.entry(binding.clone())
            {
                let observed = self.product_binding(&mut epoch, anchors, &binding).await?;
                entry.insert(observed);
            }
        }
        let call = step
            .bind(|binding| bindings.get(binding).map(ObservedRna::projection_reference))
            .map_err(|_| backend("Product step could not bind its current native observations"))?;
        let before = epoch
            .generation
            .ok_or_else(|| backend("Product step omitted its semantic generation"))?;
        let data = self.call(call.operation, call.input.clone(), true).await?;
        let after = check_product_mutation_ack(&call, &data, before, &bindings)?;
        record_product_created_root(anchors, step, &call, &data)?;
        Ok(ProductMutationReceipt {
            sequence: self.sequence,
            command: call.operation.into(),
            input: call.input,
            semantic_generation_before: before,
            semantic_generation_after: after,
            native_data: data,
        })
    }

    async fn product_run_steps(
        &mut self,
        anchors: &mut ProductAnchors,
        steps: &[SemanticStep],
    ) -> NativeResult<Vec<ProductMutationReceipt>> {
        let mut receipts = Vec::with_capacity(steps.len());
        for step in steps {
            receipts.push(self.product_run_step(anchors, step).await?);
        }
        Ok(receipts)
    }
}

#[derive(Debug, Serialize)]
struct ProductBootstrapObservation {
    runtime: Value,
    native_session: String,
    initial_scene: Value,
    empty_scene: Value,
    empty_semantic_generation: u64,
}

impl<R: OriginalPhaseScope, P, J: OriginalCallJournal> OriginalSession<'_, R, P, J> {
    // Replaces the two duplicated loops in OriginalSession::isolate_scene.
    // The caller owns the fresh private native context, exactly as in the original path.
    pub(super) async fn isolate_named_scene_membership(
        &mut self,
        scene_name: &str,
        allowed_collections: &BTreeSet<String>,
        allowed_objects: &BTreeSet<String>,
    ) -> NativeResult<()> {
        for (property, target_root, allowed) in [
            ("children", "collections", allowed_collections),
            ("objects", "objects", allowed_objects),
        ] {
            let mut epoch = ProductReadEpoch::default();
            let scene = self.product_root(&mut epoch, "scenes", scene_name).await?;
            let master = self
                .product_only_relation(&mut epoch, &scene, "collection", Some("Collection"))
                .await?;
            let rows = self
                .product_relations(&mut epoch, &master, property)
                .await?;
            let names = rows
                .iter()
                .map(|row| row.native_name.clone())
                .collect::<BTreeSet<_>>();
            if names.len() != rows.len() || !allowed.is_subset(&names) {
                return Err(backend(
                    "Blender Scene membership is ambiguous or omits an owned member",
                ));
            }
            for name in names.difference(allowed) {
                let mut current = ProductReadEpoch::default();
                let scene = self
                    .product_root(&mut current, "scenes", scene_name)
                    .await?;
                let inspected = self
                    .call("driver.blender.scene.inspect", json!({}), false)
                    .await?;
                if inspected.get("name").and_then(Value::as_str) != Some(scene_name) {
                    return Err(Error::new(
                        ErrorCode::StaleReference,
                        "Active Blender Scene changed during explicit isolation",
                    ));
                }
                let master = self
                    .product_only_relation(&mut current, &scene, "collection", Some("Collection"))
                    .await?;
                let target = self.product_root(&mut current, target_root, name).await?;
                let before = current
                    .generation
                    .ok_or_else(|| backend("Blender isolation omitted its semantic generation"))?;
                let call = NativeCall {
                    operation: "driver.blender.semantic.relation.unlink",
                    input: json!({"ref": master.reference, "property": property,
                        "target_ref": target.reference}),
                };
                let data = self.call(call.operation, call.input.clone(), true).await?;
                check_product_mutation_ack(&call, &data, before, &BTreeMap::new())?;
            }
            let mut epoch = ProductReadEpoch::default();
            let scene = self.product_root(&mut epoch, "scenes", scene_name).await?;
            let master = self
                .product_only_relation(&mut epoch, &scene, "collection", Some("Collection"))
                .await?;
            let rows = self
                .product_relations(&mut epoch, &master, property)
                .await?;
            let observed = rows
                .iter()
                .map(|row| row.native_name.clone())
                .collect::<BTreeSet<_>>();
            if observed != *allowed || rows.len() != allowed.len() {
                return Err(backend(
                    "Blender Scene isolation did not yield its declared membership",
                ));
            }
        }
        Ok(())
    }

    async fn product_bootstrap(
        &mut self,
        anchors: &mut ProductAnchors,
    ) -> NativeResult<ProductBootstrapObservation> {
        if !anchors.roots.is_empty() || anchors.collection.is_some() {
            return Err(invalid("Product bootstrap requires an unused binding set"));
        }
        let runtime = self.product_runtime_summary().await?;
        let initial_snapshot: NativeSnapshot = serde_json::from_value(
            self.call("driver.blender.composition.inspect", json!({}), false)
                .await?,
        )
        .map_err(|_| product_sdk_error())?;
        composition::bounded_id(&initial_snapshot.native_session)
            .map_err(|_| product_sdk_error())?;
        if initial_snapshot.island.is_some()
            || !initial_snapshot.source_only
            || !initial_snapshot.exhaustive
            || initial_snapshot.total != initial_snapshot.items.len()
            || initial_snapshot.total > 512
        {
            return Err(product_sdk_error());
        }
        let initial_scene = self
            .call("driver.blender.scene.inspect", json!({}), false)
            .await?;
        let scene_name = product_observed_name(&initial_scene, "name")?;
        anchors.roots.insert(
            Binding::Scene,
            ProductRootAnchor {
                root: "scenes",
                native_name: scene_name.clone(),
                required_identity: vec![],
            },
        );
        let mut initial_epoch = ProductReadEpoch::default();
        let initial_binding = self
            .product_active_scene(&mut initial_epoch, anchors)
            .await?;
        for (property, expected) in [("frame_current", json!(1)), ("frame_subframe", json!(0.0))] {
            let actual = self
                .product_property(&mut initial_epoch, &initial_binding, property)
                .await?;
            if !original_value_matches(&expected, &actual) {
                return Err(backend(
                    "Product creation requires native frame 1 and subframe 0",
                ));
            }
        }
        if initial_snapshot.items.iter().any(|row| {
            row.get("native_island")
                .is_some_and(|value| !value.is_null())
        }) {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Product creation requires a fresh context without an existing SDK-owned island",
            ));
        }
        self.isolate_named_scene_membership(&scene_name, &BTreeSet::new(), &BTreeSet::new())
            .await?;
        let mut epoch = ProductReadEpoch::default();
        let scene = self.product_active_scene(&mut epoch, anchors).await?;
        for (property, expected) in [("frame_current", json!(1)), ("frame_subframe", json!(0.0))] {
            let actual = self.product_property(&mut epoch, &scene, property).await?;
            if !original_value_matches(&expected, &actual) {
                return Err(backend(
                    "Product creation requires native frame 1 and subframe 0",
                ));
            }
        }
        let empty_scene = self
            .call("driver.blender.scene.inspect", json!({}), false)
            .await?;
        if empty_scene.get("object_count").and_then(Value::as_u64) != Some(0) {
            return Err(backend(
                "Product Scene is not empty after explicit isolation",
            ));
        }
        Ok(ProductBootstrapObservation {
            runtime,
            native_session: initial_snapshot.native_session,
            initial_scene,
            empty_scene,
            empty_semantic_generation: epoch
                .generation
                .ok_or_else(|| backend("Empty product Scene omitted semantic generation"))?,
        })
    }
}

#[derive(Debug, Serialize)]
struct ProductReadbackObservation {
    semantic_generation: u64,
    scalar_readbacks: Vec<Value>,
    relation_readbacks: Vec<Value>,
    node_graphs: Vec<Value>,
    camera_handles: Vec<Value>,
    observed_configuration: Vec<Value>,
}

fn product_expectation(
    target: Binding,
    property: &'static str,
    expected: Value,
) -> PropertyExpectation {
    PropertyExpectation {
        target,
        property,
        expected,
        absolute_tolerance: RNA_FLOAT_TOLERANCE,
    }
}

fn product_root_name<'a>(anchors: &'a ProductAnchors, binding: &Binding) -> NativeResult<&'a str> {
    anchors
        .roots
        .get(binding)
        .map(|root| root.native_name.as_str())
        .ok_or_else(|| backend("Product readback has no observed native root name"))
}

impl<R: OriginalPhaseScope, P, J: OriginalCallJournal> OriginalSession<'_, R, P, J> {
    async fn product_read_expectation(
        &mut self,
        epoch: &mut ProductReadEpoch,
        anchors: &ProductAnchors,
        expectation: &PropertyExpectation,
    ) -> NativeResult<Value> {
        let observed = self
            .product_binding(epoch, anchors, &expectation.target)
            .await?;
        let call = expectation
            .getter()
            .bind(|binding| {
                (binding == &expectation.target).then(|| observed.projection_reference())
            })
            .map_err(|_| backend("Product scalar readback could not bind its fresh target"))?;
        let data = self.call(call.operation, call.input, false).await?;
        // Recreate the request value from the same observed binding, never a native result.
        let request = NativeCall {
            operation: "driver.blender.semantic.property.get",
            input: json!({"ref": observed.reference, "property": expectation.property}),
        };
        if !expectation.matches_reply(&request, &data)
            || data.pointer("/descriptor/id").and_then(Value::as_str) != Some(expectation.property)
            || !matches!(
                data.pointer("/descriptor/status").and_then(Value::as_str),
                Some("managed" | "read_only")
            )
        {
            return Err(backend(
                "Blender product scalar differs from the closed source projection",
            ));
        }
        Ok(data)
    }

    async fn product_pointer_named(
        &mut self,
        epoch: &mut ProductReadEpoch,
        owner: &ObservedRna,
        property: &str,
        expected_name: &str,
        expected_type: &str,
    ) -> NativeResult<Value> {
        let observed = self
            .product_only_relation(epoch, owner, property, Some(expected_type))
            .await?;
        if observed.native_name != expected_name {
            return Err(backend(
                "Blender pointer does not preserve its expected native relationship",
            ));
        }
        Ok(json!({"owner_ref": owner.reference, "property": property,
            "observed_ref": observed.reference, "name": observed.native_name,
            "rna_type": observed.rna_type}))
    }

    async fn product_exact_members(
        &mut self,
        epoch: &mut ProductReadEpoch,
        owner: &ObservedRna,
        property: &str,
        expected_names: &BTreeSet<String>,
        expected_type: &str,
    ) -> NativeResult<Value> {
        let rows = self.product_relations(epoch, owner, property).await?;
        let observed = rows
            .iter()
            .map(|row| row.native_name.clone())
            .collect::<BTreeSet<_>>();
        if observed != *expected_names
            || rows.len() != expected_names.len()
            || rows
                .iter()
                .any(|row| row.rna_type.as_deref() != Some(expected_type))
        {
            return Err(backend(
                "Blender collection membership differs from the closed source",
            ));
        }
        Ok(json!({"owner_ref": owner.reference, "property": property,
            "names": observed, "count": rows.len()}))
    }

    async fn product_shader_graph(
        &mut self,
        epoch: &mut ProductReadEpoch,
        anchors: &ProductAnchors,
        binding: &Binding,
        shader_type: &str,
        output_type: &str,
        output_identifier: &str,
    ) -> NativeResult<Value> {
        let material = self.product_binding(epoch, anchors, binding).await?;
        let tree = self
            .product_only_relation(epoch, &material, "node_tree", None)
            .await?;
        let nodes = self.product_relations(epoch, &tree, "nodes").await?;
        let shaders = nodes
            .iter()
            .filter(|node| node.rna_type.as_deref() == Some(shader_type))
            .collect::<Vec<_>>();
        let outputs = nodes
            .iter()
            .filter(|node| node.rna_type.as_deref() == Some(output_type))
            .collect::<Vec<_>>();
        if nodes.len() != 2 || shaders.len() != 1 || outputs.len() != 1 {
            return Err(backend(
                "Blender product shader graph has absent or extra nodes",
            ));
        }
        let links = self.product_relations(epoch, &tree, "links").await?;
        if links.len() != 1 {
            return Err(backend(
                "Blender product shader graph requires exactly one observed link",
            ));
        }
        let link = &links[0];
        let from_node = self
            .product_only_relation(epoch, link, "from_node", Some(shader_type))
            .await?;
        let to_node = self
            .product_only_relation(epoch, link, "to_node", Some(output_type))
            .await?;
        if from_node.native_name != shaders[0].native_name
            || to_node.native_name != outputs[0].native_name
        {
            return Err(backend(
                "Blender shader link endpoints do not belong to its observed nodes",
            ));
        }
        let from_socket = self
            .product_only_relation(epoch, link, "from_socket", None)
            .await?;
        let to_socket = self
            .product_only_relation(epoch, link, "to_socket", None)
            .await?;
        let from_identifier = self
            .product_property(epoch, &from_socket, "identifier")
            .await?;
        let to_identifier = self
            .product_property(epoch, &to_socket, "identifier")
            .await?;
        if from_identifier != json!(output_identifier) || to_identifier != json!("Surface") {
            return Err(backend(
                "Blender shader link connects different native sockets",
            ));
        }
        Ok(
            json!({"owner_ref": material.reference, "tree_ref": tree.reference,
            "link_ref": link.reference, "from_node": from_node.native_name,
            "from_identifier": from_identifier, "to_node": to_node.native_name,
            "to_identifier": to_identifier}),
        )
    }

    async fn product_readback(
        &mut self,
        anchors: &ProductAnchors,
        source: &ProductScene,
        projection: &ProductProjection,
    ) -> NativeResult<ProductReadbackObservation> {
        let mut epoch = ProductReadEpoch::default();
        let scene = self.product_active_scene(&mut epoch, anchors).await?;
        let mut scalars = Vec::new();
        for expectation in &projection.readback {
            scalars.push(
                self.product_read_expectation(&mut epoch, anchors, expectation)
                    .await?,
            );
        }
        let extra = [
            product_expectation(Binding::Scene, "frame_subframe", json!(0.0)),
            product_expectation(Binding::Scene, "use_nodes", json!(false)),
            product_expectation(Binding::RenderSettings, "engine", json!("CYCLES")),
            product_expectation(Binding::RenderSettings, "resolution_x", json!(1920)),
            product_expectation(Binding::RenderSettings, "resolution_y", json!(1080)),
            product_expectation(Binding::RenderSettings, "resolution_percentage", json!(100)),
            product_expectation(Binding::RenderSettings, "pixel_aspect_x", json!(1.0)),
            product_expectation(Binding::RenderSettings, "pixel_aspect_y", json!(1.0)),
            product_expectation(Binding::RenderSettings, "threads_mode", json!("FIXED")),
            product_expectation(Binding::RenderSettings, "threads", json!(2)),
            product_expectation(Binding::RenderSettings, "film_transparent", json!(false)),
            product_expectation(Binding::RenderSettings, "use_file_extension", json!(true)),
            product_expectation(Binding::ImageSettings, "file_format", json!("PNG")),
            product_expectation(Binding::ImageSettings, "color_mode", json!("RGBA")),
            product_expectation(Binding::ImageSettings, "color_depth", json!("8")),
            product_expectation(Binding::Cycles, "device", json!("CPU")),
            product_expectation(Binding::Cycles, "samples", json!(source.samples)),
            product_expectation(Binding::Cycles, "seed", json!(73)),
            product_expectation(Binding::Cycles, "use_animated_seed", json!(false)),
            product_expectation(Binding::Cycles, "use_denoising", json!(true)),
            product_expectation(Binding::ViewSettings, "view_transform", json!("AgX")),
            product_expectation(Binding::ViewSettings, "exposure", json!(0.0)),
            product_expectation(Binding::ViewSettings, "gamma", json!(1.0)),
            product_expectation(Binding::UnitSettings, "system", json!("METRIC")),
            product_expectation(Binding::UnitSettings, "scale_length", json!(1.0)),
            product_expectation(Binding::FontObject, "type", json!("FONT")),
            product_expectation(
                Binding::FontObject,
                "rotation_euler",
                json!([0.0, 0.0, 0.0]),
            ),
            product_expectation(Binding::FontObject, "scale", json!([1.0, 1.0, 1.0])),
            product_expectation(Binding::FontObject, "parent_type", json!("OBJECT")),
            product_expectation(Binding::World, "use_nodes", json!(true)),
            product_expectation(Binding::FontInk, "use_nodes", json!(true)),
            product_expectation(Binding::CoatingMaterial, "use_nodes", json!(true)),
            product_expectation(
                Binding::CoatingMaterial,
                "roughness",
                json!(source.roughness),
            ),
            product_expectation(Binding::CoatingMaterial, "metallic", json!(source.metallic)),
            product_expectation(Binding::CameraData("camera_form"), "type", json!("PERSP")),
            product_expectation(
                Binding::CameraData("camera_surface"),
                "type",
                json!("PERSP"),
            ),
        ];
        for expectation in extra {
            scalars.push(
                self.product_read_expectation(&mut epoch, anchors, &expectation)
                    .await?,
            );
        }
        let mut relations = Vec::new();
        let world_name = product_root_name(anchors, &Binding::World)?;
        relations.push(
            self.product_pointer_named(&mut epoch, &scene, "world", world_name, "World")
                .await?,
        );
        let font = self
            .product_binding(&mut epoch, anchors, &Binding::FontObject)
            .await?;
        let font_data = self
            .product_binding(&mut epoch, anchors, &Binding::FontData)
            .await?;
        let linked_data = self
            .product_only_relation(&mut epoch, &font, "data", Some("TextCurve"))
            .await?;
        if linked_data.native_name != font_data.native_name {
            return Err(backend(
                "Blender FONT object does not use its created editable text datablock",
            ));
        }
        relations.push(json!({"owner_ref": font.reference, "property": "data",
            "observed_ref": linked_data.reference, "name": linked_data.native_name,
            "rna_type": linked_data.rna_type}));
        relations.push(
            self.product_pointer_named(
                &mut epoch,
                &font,
                "parent",
                product_root_name(anchors, &Binding::Entity("camera_surface"))?,
                "Object",
            )
            .await?,
        );
        relations.push(
            self.product_exact_members(
                &mut epoch,
                &font_data,
                "materials",
                &BTreeSet::from([product_root_name(anchors, &Binding::FontInk)?.to_owned()]),
                "Material",
            )
            .await?,
        );
        for (camera, focus) in [
            ("camera_form", "focus_form"),
            ("camera_surface", "focus_surface"),
        ] {
            let dof = self
                .product_binding(&mut epoch, anchors, &Binding::CameraDof(camera))
                .await?;
            relations.push(
                self.product_pointer_named(
                    &mut epoch,
                    &dof,
                    "focus_object",
                    product_root_name(anchors, &Binding::Entity(focus))?,
                    "Object",
                )
                .await?,
            );
        }
        for (index, camera) in [(0, "camera_form"), (1, "camera_surface")] {
            let marker = self
                .product_binding(&mut epoch, anchors, &Binding::Marker(index))
                .await?;
            relations.push(
                self.product_pointer_named(
                    &mut epoch,
                    &marker,
                    "camera",
                    product_root_name(anchors, &Binding::Entity(camera))?,
                    "Object",
                )
                .await?,
            );
        }
        let markers = self
            .product_relations(&mut epoch, &scene, "timeline_markers")
            .await?;
        let marker_names = markers
            .iter()
            .map(|row| row.native_name.as_str())
            .collect::<BTreeSet<_>>();
        if markers.len() != 2 || marker_names != BTreeSet::from(["Form", "Surface"]) {
            return Err(backend(
                "Blender product Scene has missing or extra camera-cut markers",
            ));
        }
        let sequencer = self
            .product_relations(&mut epoch, &scene, "sequence_editor")
            .await?;
        if !sequencer.is_empty() {
            return Err(backend(
                "Blender product render has an unexpected sequence editor",
            ));
        }
        let entity_names = PRODUCT_ENTITY_IDS
            .iter()
            .map(|id| product_root_name(anchors, &Binding::Entity(id)).map(str::to_owned))
            .collect::<NativeResult<BTreeSet<_>>>()?;
        let collection_anchor = anchors
            .collection
            .as_ref()
            .ok_or_else(|| backend("Blender product collection has no SDK observation"))?;
        let collection = self
            .product_root(&mut epoch, "collections", &collection_anchor.native_name)
            .await?;
        let identity = collection_anchor
            .required_identity
            .iter()
            .map(|(key, value)| (key.as_str(), value.as_str()))
            .collect::<Vec<_>>();
        self.product_custom_identity(&mut epoch, &collection, &identity)
            .await?;
        relations.push(
            self.product_exact_members(&mut epoch, &collection, "objects", &entity_names, "Object")
                .await?,
        );
        relations.push(
            self.product_exact_members(
                &mut epoch,
                &collection,
                "children",
                &BTreeSet::new(),
                "Collection",
            )
            .await?,
        );
        let master = self
            .product_only_relation(&mut epoch, &scene, "collection", Some("Collection"))
            .await?;
        relations.push(
            self.product_exact_members(
                &mut epoch,
                &master,
                "children",
                &BTreeSet::from([collection.native_name.clone()]),
                "Collection",
            )
            .await?,
        );
        relations.push(
            self.product_exact_members(
                &mut epoch,
                &master,
                "objects",
                &BTreeSet::from([font.native_name.clone()]),
                "Object",
            )
            .await?,
        );
        let mut scene_objects = entity_names;
        scene_objects.insert(font.native_name.clone());
        relations.push(
            self.product_exact_members(&mut epoch, &scene, "objects", &scene_objects, "Object")
                .await?,
        );
        for (entity, vertex_count, polygon_count) in [
            ("vessel_body", 768, 768),
            ("vessel_floor", 192, 98),
            ("vessel_base", 192, 98),
            ("studio_floor", 4, 1),
        ] {
            let object = self
                .product_binding(&mut epoch, anchors, &Binding::Entity(entity))
                .await?;
            let mesh = self
                .product_only_relation(&mut epoch, &object, "data", Some("Mesh"))
                .await?;
            let vertices = self
                .product_relations(&mut epoch, &mesh, "vertices")
                .await?;
            let polygons = self
                .product_relations(&mut epoch, &mesh, "polygons")
                .await?;
            if vertices.len() != vertex_count || polygons.len() != polygon_count {
                return Err(backend(
                    "Blender product mesh topology counts differ from the typed source",
                ));
            }
            relations.push(json!({"entity": entity, "mesh_ref": mesh.reference,
                "vertices": vertices.len(), "polygons": polygons.len()}));
        }
        for (entity, target) in [
            ("light_key", "target_key"),
            ("light_fill", "target_fill"),
            ("light_rim", "target_rim"),
            ("camera_form", "focus_form"),
            ("camera_surface", "focus_surface"),
        ] {
            let object = self
                .product_binding(&mut epoch, anchors, &Binding::Entity(entity))
                .await?;
            let constraint = self
                .product_only_relation(
                    &mut epoch,
                    &object,
                    "constraints",
                    Some("TrackToConstraint"),
                )
                .await?;
            for (property, expected) in [
                ("track_axis", "TRACK_NEGATIVE_Z"),
                ("up_axis", "UP_Y"),
                ("owner_space", "WORLD"),
                ("target_space", "WORLD"),
            ] {
                let actual = self
                    .product_property(&mut epoch, &constraint, property)
                    .await?;
                if actual != json!(expected) {
                    return Err(backend(
                        "Blender product look-at constraint differs from its SDK source",
                    ));
                }
                scalars.push(
                    json!({"ref": constraint.reference, "property": property, "value": actual}),
                );
            }
            relations.push(
                self.product_pointer_named(
                    &mut epoch,
                    &constraint,
                    "target",
                    product_root_name(anchors, &Binding::Entity(target))?,
                    "Object",
                )
                .await?,
            );
        }
        let node_graphs = vec![
            self.product_shader_graph(
                &mut epoch,
                anchors,
                &Binding::FontInk,
                "ShaderNodeEmission",
                "ShaderNodeOutputMaterial",
                "Emission",
            )
            .await?,
            self.product_shader_graph(
                &mut epoch,
                anchors,
                &Binding::World,
                "ShaderNodeBackground",
                "ShaderNodeOutputWorld",
                "Background",
            )
            .await?,
            self.product_shader_graph(
                &mut epoch,
                anchors,
                &Binding::CoatingMaterial,
                "ShaderNodeBsdfPrincipled",
                "ShaderNodeOutputMaterial",
                "BSDF",
            )
            .await?,
        ];
        let mut handles = Vec::new();
        for (entity, frames) in [
            ("camera_form", &[1, 300, 600][..]),
            ("camera_surface", &[601, 1200][..]),
        ] {
            for frame in frames {
                for axis in 0..3 {
                    let point = self
                        .product_binding(
                            &mut epoch,
                            anchors,
                            &Binding::CameraKey {
                                entity,
                                axis,
                                frame: *frame,
                            },
                        )
                        .await?;
                    let left = self
                        .product_property(&mut epoch, &point, "handle_left")
                        .await?;
                    let right = self
                        .product_property(&mut epoch, &point, "handle_right")
                        .await?;
                    for handle in [&left, &right] {
                        let coordinates = handle
                            .as_array()
                            .filter(|values| values.len() == 2)
                            .ok_or_else(|| {
                                backend("Blender camera handle omitted its actual coordinates")
                            })?;
                        if coordinates
                            .iter()
                            .any(|value| value.as_f64().is_none_or(|v| !v.is_finite()))
                        {
                            return Err(backend(
                                "Blender camera handle is not a finite native observation",
                            ));
                        }
                    }
                    handles.push(json!({"entity": entity, "axis": axis, "frame": frame,
                        "ref": point.reference, "handle_left": left, "handle_right": right}));
                }
            }
        }
        let mut configuration = Vec::new();
        let view = self
            .product_binding(&mut epoch, anchors, &Binding::ViewSettings)
            .await?;
        configuration.push(json!({"ref": view.reference, "property": "look",
            "value": self.product_property(&mut epoch, &view, "look").await?}));
        for property in ["font", "font_bold", "font_italic", "font_bold_italic"] {
            let face = self
                .product_only_relation(&mut epoch, &font_data, property, Some("VectorFont"))
                .await?;
            configuration.push(json!({"owner_ref": font_data.reference, "property": property,
                "observed_ref": face.reference, "name": face.native_name, "rna_type": face.rna_type}));
        }
        Ok(ProductReadbackObservation {
            semantic_generation: epoch
                .generation
                .ok_or_else(|| backend("Product readback omitted its semantic generation"))?,
            scalar_readbacks: scalars,
            relation_readbacks: relations,
            node_graphs,
            camera_handles: handles,
            observed_configuration: configuration,
        })
    }
}

const PRODUCT_FRAME_600: &str = "blender-product-frame600.png";
const PRODUCT_FRAME_601: &str = "blender-product-frame601.png";
const PRODUCT_PNG_MAX_BYTES: u64 = 32 * 1024 * 1024;

#[derive(Debug, Serialize)]
struct ProductPngObservation {
    relative_path: String,
    sha256: String,
    bytes: u64,
    width: u32,
    height: u32,
    png_header_checked: bool,
    decoded_media_verified: bool,
}

#[derive(Debug, Serialize)]
struct ProductCutObservation {
    frame: u32,
    expected_camera: String,
    render_ack: Value,
    scene_after_render: Value,
    annotation_hidden_after_render: bool,
    artifact: ProductPngObservation,
}

fn product_png_must_be_new(root: &Path, relative: &str) -> NativeResult<PathBuf> {
    if !matches!(relative, PRODUCT_FRAME_600 | PRODUCT_FRAME_601) {
        return Err(invalid(
            "Product probe path is outside the two fixed PNG artifacts",
        ));
    }
    if !safe_metadata(root)?.is_dir() {
        return Err(invalid("Product output root is not a directory"));
    }
    let path = root.join(relative);
    match fs::symlink_metadata(&path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(path),
        Err(_) => Err(backend("Product PNG output path could not be inspected")),
        Ok(_) => Err(Error::new(
            ErrorCode::Conflict,
            "Product PNG output already exists",
        )),
    }
}

fn inspect_product_png(root: &Path, relative: &str) -> NativeResult<ProductPngObservation> {
    use std::io::Read;
    if !matches!(relative, PRODUCT_FRAME_600 | PRODUCT_FRAME_601) {
        return Err(invalid(
            "Product PNG artifact name is outside its fixed profile",
        ));
    }
    let path = output_artifact_path(root, relative, PRODUCT_PNG_MAX_BYTES)?;
    let bytes = safe_metadata(&path)?.len();
    if bytes < 33 {
        return Err(backend("Blender render produced a truncated PNG header"));
    }
    let mut header = [0_u8; 33];
    fs::File::open(&path)
        .and_then(|mut file| file.read_exact(&mut header))
        .map_err(|_| backend("Blender PNG header could not be read"))?;
    let width = u32::from_be_bytes(
        header[16..20]
            .try_into()
            .map_err(|_| backend("Blender PNG width is unavailable"))?,
    );
    let height = u32::from_be_bytes(
        header[20..24]
            .try_into()
            .map_err(|_| backend("Blender PNG height is unavailable"))?,
    );
    if &header[..8] != b"\x89PNG\r\n\x1a\n"
        || header[8..12] != 13_u32.to_be_bytes()
        || &header[12..16] != b"IHDR"
        || width != 1920
        || height != 1080
        || header[24] != 8
        || header[25] != 6
        || header[26..29] != [0, 0, 0]
    {
        return Err(backend(
            "Blender PNG header differs from the 1920x1080 RGBA8 profile",
        ));
    }
    Ok(ProductPngObservation {
        relative_path: relative.into(),
        sha256: sha256_file(&path)?,
        bytes,
        width,
        height,
        png_header_checked: true,
        decoded_media_verified: false,
    })
}

impl<R: OriginalPhaseScope, P, J: ProductEvidenceJournal> OriginalSession<'_, R, P, J> {
    async fn product_set_frame(
        &mut self,
        anchors: &ProductAnchors,
        frame: u32,
    ) -> NativeResult<Value> {
        if !matches!(frame, 1 | 600 | 601) {
            return Err(invalid(
                "Product frame setter is outside its fixed probe/canonical frames",
            ));
        }
        let mut epoch = ProductReadEpoch::default();
        let scene = self.product_active_scene(&mut epoch, anchors).await?;
        let call = NativeCall {
            operation: "driver.blender.semantic.property.set",
            input: json!({"ref": scene.reference, "property": "frame_current", "value": frame}),
        };
        let data = self.call(call.operation, call.input.clone(), true).await?;
        check_product_mutation_ack(&call, &data, scene.generation, &BTreeMap::new())?;
        Ok(data)
    }

    async fn product_render_cut(
        &mut self,
        anchors: &ProductAnchors,
        output_root: &Path,
        frame: u32,
    ) -> NativeResult<ProductCutObservation> {
        let (relative, camera, hidden) = match frame {
            600 => (PRODUCT_FRAME_600, "camera_form", true),
            601 => (PRODUCT_FRAME_601, "camera_surface", false),
            _ => {
                return Err(invalid(
                    "Product render probe requires native frame 600 or 601",
                ));
            }
        };
        product_png_must_be_new(output_root, relative)?;
        self.product_set_frame(anchors, frame).await?;
        let mut before = ProductReadEpoch::default();
        let scene = self.product_active_scene(&mut before, anchors).await?;
        let observed_frame = self
            .product_property(&mut before, &scene, "frame_current")
            .await?;
        let observed_subframe = self
            .product_property(&mut before, &scene, "frame_subframe")
            .await?;
        if observed_frame.as_u64() != Some(u64::from(frame))
            || observed_subframe.as_f64() != Some(0.0)
        {
            return Err(backend(
                "Blender render probe is not at its exact native frame/subframe",
            ));
        }
        // No composition.measure or other frame evaluation is allowed before this render.
        product_png_must_be_new(output_root, relative)?;
        let render_ack = self
            .call("driver.blender.render", json!({"path": relative}), true)
            .await?;
        if render_ack.get("changed").and_then(Value::as_bool) != Some(true)
            || render_ack.get("path").and_then(Value::as_str) != Some(relative)
            || render_ack.get("format").and_then(Value::as_str) != Some("png")
        {
            return Err(
                backend("Blender render ACK does not match its new PNG artifact").uncertain(),
            );
        }
        let artifact = inspect_product_png(output_root, relative)?;
        self.journal.record_product_evidence(
            "rendered_png",
            json!({"frame": frame, "artifact": artifact}),
        );
        let scene_after_render = self
            .call("driver.blender.scene.inspect", json!({}), false)
            .await?;
        let expected_camera = product_root_name(anchors, &Binding::Entity(camera))?.to_owned();
        let mut after = ProductReadEpoch::default();
        let annotation = self
            .product_binding(&mut after, anchors, &Binding::FontObject)
            .await?;
        let observed_hidden = self
            .product_property(&mut after, &annotation, "hide_render")
            .await?;
        self.journal.record_product_evidence(
            "render_cut_state",
            json!({
                "frame": frame, "expected_camera": expected_camera, "scene": scene_after_render,
                "annotation_hidden": observed_hidden
            }),
        );
        if scene_after_render.get("name").and_then(Value::as_str)
            != Some(product_root_name(anchors, &Binding::Scene)?)
            || scene_after_render.get("frame").and_then(Value::as_u64) != Some(u64::from(frame))
            || scene_after_render.get("camera").and_then(Value::as_str)
                != Some(expected_camera.as_str())
            || scene_after_render.get("engine").and_then(Value::as_str) != Some("CYCLES")
            || scene_after_render.get("resolution") != Some(&json!([1920, 1080]))
        {
            return Err(backend(
                "Actual Blender render did not observe the expected frame/camera cut",
            ));
        }
        if observed_hidden.as_bool() != Some(hidden) {
            return Err(backend(
                "Actual Blender render did not observe the expected annotation visibility",
            ));
        }
        Ok(ProductCutObservation {
            frame,
            expected_camera,
            render_ack,
            scene_after_render,
            annotation_hidden_after_render: hidden,
            artifact,
        })
    }

    async fn product_measure_non_keyframe(
        &mut self,
        anchors: &ProductAnchors,
        composition: &ProductCompositionObservation,
        cuts: &[ProductCutObservation],
    ) -> NativeResult<Value> {
        if cuts.len() != 2
            || cuts[0].frame != 600
            || cuts[1].frame != 601
            || cuts[0].artifact.relative_path != PRODUCT_FRAME_600
            || cuts[1].artifact.relative_path != PRODUCT_FRAME_601
        {
            return Err(invalid(
                "Product non-keyframe measurement requires both completed native PNG probes",
            ));
        }
        self.product_set_frame(anchors, 1).await?;
        // SDK measure is Mutating: it calls frame_set(150), evaluates and restores entry frame 1.
        let data = self
            .call(
                "driver.blender.composition.measure",
                json!({
                    "island": composition.island, "evaluated": true, "pairs": [],
                    "sample": {"frame": 150, "rate": {"num": 30, "den": 1}}
                }),
                true,
            )
            .await?;
        self.journal
            .record_product_evidence("native_non_keyframe_measurement", data.clone());
        if data.get("island").and_then(Value::as_str) != Some(composition.island.as_str())
            || data.get("native_session").and_then(Value::as_str)
                != Some(composition.snapshot.native_session.as_str())
            || data
                .pointer("/sample_observed/frame")
                .and_then(Value::as_u64)
                != Some(150)
            || data
                .pointer("/sample_observed/subframe")
                .and_then(Value::as_f64)
                != Some(0.0)
            || data.pointer("/sample_observed/fps").and_then(Value::as_u64) != Some(30)
            || data
                .pointer("/sample_observed/fps_base")
                .and_then(Value::as_f64)
                != Some(1.0)
            || data.get("coverage").and_then(Value::as_str) != Some("single_frame")
            || data.get("method_version").and_then(Value::as_u64) != Some(3)
        {
            return Err(backend(
                "Blender non-keyframe measurement has a different native observation",
            )
            .uncertain());
        }
        let rows = data.get("items").and_then(Value::as_array).ok_or_else(|| {
            backend("Blender non-keyframe measurement omitted its items").uncertain()
        })?;
        let names = rows
            .iter()
            .filter_map(|row| row.get("entity").and_then(Value::as_str))
            .collect::<BTreeSet<_>>();
        if rows.len() != PRODUCT_ENTITY_IDS.len()
            || names != PRODUCT_ENTITY_IDS.iter().copied().collect::<BTreeSet<_>>()
            || data.get("total").and_then(Value::as_u64) != Some(rows.len() as u64)
            || rows.iter().any(|row| {
                row.get("frame").and_then(Value::as_u64) != Some(150)
                    || row.get("method").and_then(Value::as_str) != Some("evaluated-depsgraph")
            })
        {
            return Err(
                backend("Blender non-keyframe measurement is partial or not evaluated").uncertain(),
            );
        }
        let camera = rows
            .iter()
            .find(|row| row.get("entity").and_then(Value::as_str) == Some("camera_form"))
            .ok_or_else(|| {
                backend("Blender non-keyframe measurement omitted camera_form").uncertain()
            })?;
        let origin = camera
            .get("origin_world_meters")
            .and_then(Value::as_array)
            .filter(|values| values.len() == 3)
            .ok_or_else(|| backend("Blender evaluated camera origin is unavailable").uncertain())?;
        for (value, low, high) in [
            (origin[0].as_f64(), 0.22, 0.28),
            (origin[1].as_f64(), -0.30, -0.24),
            (origin[2].as_f64(), 0.18, 0.19),
        ] {
            if value.is_none_or(|value| !value.is_finite() || value <= low || value >= high) {
                return Err(backend(
                    "Blender frame150 camera did not move inside its source key interval",
                ));
            }
        }
        let mut epoch = ProductReadEpoch::default();
        let scene = self.product_active_scene(&mut epoch, anchors).await?;
        if self
            .product_property(&mut epoch, &scene, "frame_current")
            .await?
            .as_u64()
            != Some(1)
            || self
                .product_property(&mut epoch, &scene, "frame_subframe")
                .await?
                .as_f64()
                != Some(0.0)
        {
            return Err(backend(
                "Blender native measurement did not restore frame1/subframe0",
            ));
        }
        let annotation = self
            .product_binding(&mut epoch, anchors, &Binding::FontObject)
            .await?;
        if self
            .product_property(&mut epoch, &annotation, "hide_render")
            .await?
            .as_bool()
            != Some(true)
        {
            return Err(backend(
                "Blender native measurement did not restore the canonical FONT visibility",
            ));
        }
        Ok(data)
    }
}

const PRODUCT_ATTEMPT_SCHEMA: &str = "motionwright.blender.product-create-probe-save/1";
const PRODUCT_RECIPE_MAX_BYTES: u64 = 64 * 1024;

// Internal source phase; the public cycle caller supplies the fixed source codec.
#[derive(Debug, Clone, Serialize)]
pub(crate) struct BlenderProductCreateRequest {
    pub project_id: Uuid,
    pub expected: RevisionStamp,
    pub scene_id: Uuid,
    pub capsule_id: Uuid,
    pub request_id: String,
}

impl OriginalPhaseScope for BlenderProductCreateRequest {
    fn project_id(&self) -> Uuid {
        self.project_id
    }
    fn expected(&self) -> &RevisionStamp {
        &self.expected
    }
    fn request_id(&self) -> &str {
        &self.request_id
    }
    fn phase_label(&self) -> &'static str {
        "product-create-probe-save"
    }
}

#[derive(Debug, Serialize, Default)]
pub(crate) struct BlenderProductJournal {
    pub schema: String,
    pub source_binding: Option<Value>,
    pub calls: Vec<Value>,
    pub evidence: Vec<Value>,
}

impl OriginalCallJournal for BlenderProductJournal {
    fn calls_mut(&mut self) -> &mut Vec<Value> {
        &mut self.calls
    }
}

pub(super) trait ProductEvidenceJournal: OriginalCallJournal {
    fn record_product_evidence(&mut self, stage: &'static str, evidence: Value);
}

impl ProductEvidenceJournal for BlenderProductJournal {
    fn record_product_evidence(&mut self, stage: &'static str, evidence: Value) {
        self.evidence
            .push(json!({"stage": stage, "evidence": evidence}));
    }
}

// No Deserialize: source authority comes only from the saved Project and CAS read below.
struct SavedProductSource {
    project: motionwright_domain::Project,
    capsule_id: Uuid,
    scene_id: Uuid,
    source_asset_id: Uuid,
    source_sha256: String,
    source: ProductScene,
}

fn product_request_valid(request: &BlenderProductCreateRequest) -> NativeResult<()> {
    if request.request_id.is_empty()
        || request.request_id.len() > 48
        || !request
            .request_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"-_".contains(&byte))
    {
        return Err(invalid("Product attempt request identity is invalid"));
    }
    Ok(())
}

// Read a capsule-bound source using the fixed trusted caller codec.
// The admitted MIME and decoder are fixed trusted-code arguments, never request fields.
fn read_saved_product_source(
    service: &motionwright_service::StudioService,
    request: &BlenderProductCreateRequest,
    admitted_media_type: &'static str,
    decode: fn(&[u8]) -> NativeResult<ProductScene>,
) -> NativeResult<SavedProductSource> {
    product_request_valid(request)?;
    if admitted_media_type.is_empty() {
        return Err(invalid(
            "Product source requires its fixed trusted source codec",
        ));
    }
    let project = service.project(request.project_id).map_err(storage_error)?;
    if project.stamp() != request.expected {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Product source requires the current full Project stamp",
        ));
    }
    let scene = project
        .scenes
        .iter()
        .find(|scene| scene.id == request.scene_id)
        .ok_or_else(|| invalid("Product source Scene is not present in the saved Project"))?;
    validate_product_source_scene(&project, scene)?;
    let capsules = project
        .production_design
        .capsules
        .iter()
        .filter(|capsule| capsule.scene_id == request.scene_id)
        .collect::<Vec<_>>();
    if capsules.len() != 1 || capsules[0].id != request.capsule_id {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Product source requires one unambiguous source-bound native capsule",
        ));
    }
    let capsule = capsules[0];
    capsule
        .validate(&project.scenes, &project.assets)
        .map_err(|_| invalid("Product capsule source binding is invalid"))?;
    let asset = project
        .assets
        .iter()
        .find(|asset| asset.id == capsule.source_asset_id)
        .ok_or_else(|| invalid("Product source asset is not attached to the Project"))?;
    if asset.media_type != admitted_media_type {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Product source asset is not accepted by the fixed trusted source codec",
        ));
    }
    let digest = asset
        .content_sha256
        .as_ref()
        .filter(|digest| digest.as_str() == capsule.source_sha256.as_str())
        .ok_or_else(|| invalid("Product capsule and source asset digests differ"))?;
    let bytes = {
        let handle = service.store();
        let store = handle.lock();
        if store.load_project(project.id).map_err(storage_error)? != project {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Product Project changed before source CAS read",
            ));
        }
        store
            .read_blob(digest, PRODUCT_RECIPE_MAX_BYTES)
            .map_err(storage_error)?
    };
    if hex::encode(sha2::Sha256::digest(&bytes)) != *digest {
        return Err(invalid(
            "Product source CAS bytes do not match their bound SHA256",
        ));
    }
    let source = decode(&bytes)?;
    source.validate().map_err(|_| {
        Error::new(
            ErrorCode::Unsupported,
            "Saved product source is outside the closed product profile",
        )
    })?;
    if service.project(request.project_id).map_err(storage_error)? != project {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Product Project changed during source decoding",
        ));
    }
    let source_asset_id = asset.id;
    let source_sha256 = digest.clone();
    Ok(SavedProductSource {
        project,
        capsule_id: request.capsule_id,
        scene_id: request.scene_id,
        source_asset_id,
        source_sha256,
        source,
    })
}

impl ProductionCoordinator {
    // Called only through the fixed source decoder and saved capsule binding.
    // This does not render the 1200-frame production or attach/replace a capsule.
    async fn realize_saved_blender_product_source(
        &self,
        request: &BlenderProductCreateRequest,
        source: &SavedProductSource,
        journal: &mut BlenderProductJournal,
        witness: &mut Option<ProductNativeSourceWitness>,
    ) -> NativeResult<Value> {
        product_request_valid(request)?;
        if witness.is_some() {
            return Err(invalid(
                "Product source continuation slot must be empty before creation",
            ));
        }
        if !journal.calls.is_empty()
            || !journal.evidence.is_empty()
            || journal.source_binding.is_some()
        {
            return Err(invalid(
                "Product journal must belong to one new explicit attempt",
            ));
        }
        let project = self
            .service
            .project(request.project_id)
            .map_err(storage_error)?;
        if project != source.project
            || project.stamp() != request.expected
            || project.id != request.project_id
            || source.scene_id != request.scene_id
            || source.capsule_id != request.capsule_id
            || self.client.connection().resource != project.resource_key()
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Saved product source authority changed",
            ));
        }
        let projection = crate::blender_product_scene::project_product_scene(&source.source)
            .map_err(|_| {
                Error::new(
                    ErrorCode::Unsupported,
                    "Product source projection is unsupported",
                )
            })?;
        let spec = ValidatedProductSpec::decode(&projection)?;
        let first_request = format!("{}:{}:000001", request.request_id, request.phase_label());
        if self
            .service
            .latest_production_receipt(request.project_id, &first_request)
            .map_err(storage_error)?
            .is_some()
        {
            return Err(Error::new(ErrorCode::Conflict,
                "Product phase was already attempted; inspect original receipts instead of replaying it").uncertain());
        }
        let output_root = self.client.connection().output_root.clone();
        source_output_must_be_new(&output_root, ORIGINAL_FILE)?;
        product_png_must_be_new(&output_root, PRODUCT_FRAME_600)?;
        product_png_must_be_new(&output_root, PRODUCT_FRAME_601)?;
        let scene = project
            .scenes
            .iter()
            .find(|scene| scene.id == request.scene_id)
            .ok_or_else(|| invalid("Saved product Scene is absent"))?;
        journal.schema = PRODUCT_ATTEMPT_SCHEMA.into();
        journal.source_binding = Some(json!({
            "project_id": project.id, "expected": request.expected,
            "scene_id": request.scene_id, "capsule_id": request.capsule_id,
            "source_asset_id": source.source_asset_id, "source_sha256": source.source_sha256,
            "canonical_scene_start": scene.start, "canonical_scene_duration": scene.duration,
            "native_frame_origin": 1, "native_frame_count": 1200,
            "rate": {"num": 30, "den": 1},
            "internal_cuts": [{"camera":"camera_form","first_native_frame":1},
                {"camera":"camera_surface","first_native_frame":601}],
            "production_clock": project.production_design.plan.as_ref().map(|plan| &plan.clock),
            "phase_scope": "editable_native_source",
            "delivery_profile_selected": false, "delivery_clock_conformance_verified": false,
            "source_authority_imported_from_payload": false,
        }));
        let mut session = OriginalSession {
            coordinator: self,
            request,
            plan: &spec,
            journal,
            sequence: 0,
        };
        let mut anchors = ProductAnchors::default();
        let bootstrap = session.product_bootstrap(&mut anchors).await?;
        session
            .journal
            .record_product_evidence("native_bootstrap", json!(bootstrap));
        let before_steps = session
            .product_run_steps(&mut anchors, &projection.before_create)
            .await?;
        session
            .journal
            .record_product_evidence("before_composition_mutations", json!(before_steps));
        let composition = session.product_create_composition(&spec).await?;
        if composition.snapshot.native_session != bootstrap.native_session {
            return Err(backend(
                "Blender native process changed during product creation",
            ));
        }
        session.journal.record_product_evidence(
            "sdk_composition_before_augmentation",
            json!({
                "plan_ref": composition.issued.plan_ref, "plan": composition.issued.plan,
                "island": composition.island, "snapshot": composition.snapshot,
                "verification": composition.verification,
            }),
        );
        install_composition_anchors(&mut anchors, &spec, &composition)?;
        let after_steps = session
            .product_run_steps(&mut anchors, &projection.after_create)
            .await?;
        session
            .journal
            .record_product_evidence("after_composition_mutations", json!(after_steps));
        let before_probes = session
            .product_readback(&anchors, &source.source, &projection)
            .await?;
        session
            .journal
            .record_product_evidence("readback_before_native_probes", json!(before_probes));
        let mut cuts = Vec::with_capacity(2);
        cuts.push(
            session
                .product_render_cut(&anchors, &output_root, 600)
                .await?,
        );
        cuts.push(
            session
                .product_render_cut(&anchors, &output_root, 601)
                .await?,
        );
        let measurement = session
            .product_measure_non_keyframe(&anchors, &composition, &cuts)
            .await?;
        let final_readback = session
            .product_readback(&anchors, &source.source, &projection)
            .await?;
        session
            .journal
            .record_product_evidence("readback_after_native_probes", json!(final_readback));
        let final_snapshot: NativeSnapshot = serde_json::from_value(
            session
                .call(
                    "driver.blender.composition.inspect",
                    json!({"island": composition.island}),
                    false,
                )
                .await?,
        )
        .map_err(|_| product_sdk_error())?;
        final_snapshot
            .base(&composition.issued.plan.body.owner)
            .map_err(|_| product_sdk_error())?;
        if final_snapshot.native_session != composition.snapshot.native_session
            || final_snapshot.island.as_deref() != Some(composition.island.as_str())
            || final_snapshot.fingerprint == composition.snapshot.fingerprint
        {
            return Err(backend(
                "Final Blender snapshot is not a fresh observation of the augmented source",
            ));
        }
        // The SDK marker belongs to the pre-augmentation projection. Preserve drift as observed.
        session
            .journal
            .record_product_evidence("augmented_sdk_source_snapshot", json!(final_snapshot));
        let global_snapshot: NativeSnapshot = serde_json::from_value(
            session
                .call("driver.blender.composition.inspect", json!({}), false)
                .await?,
        )
        .map_err(|_| product_sdk_error())?;
        global_snapshot
            .base(&composition.issued.plan.body.owner)
            .map_err(|_| product_sdk_error())?;
        if global_snapshot.native_session != composition.snapshot.native_session
            || global_snapshot.island.is_some()
        {
            return Err(backend(
                "Whole-scene Blender snapshot changed native context",
            ));
        }
        session
            .journal
            .record_product_evidence("whole_scene_sdk_source_snapshot", json!(global_snapshot));
        source_output_must_be_new(&output_root, ORIGINAL_FILE)?;
        let saved = session
            .call(
                "driver.blender.file.save",
                json!({"path": ORIGINAL_FILE}),
                true,
            )
            .await?;
        if saved != json!({"changed": true, "path": ORIGINAL_FILE, "copy": true}) {
            return Err(backend(
                "Product .blend save did not preserve the existing scoped contract",
            )
            .uncertain());
        }
        let output = inspect_original_file(&output_root, ORIGINAL_FILE)?;
        session
            .journal
            .record_product_evidence("native_blend_saved", json!(output));
        if self
            .service
            .project(request.project_id)
            .map_err(storage_error)?
            != source.project
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Project changed before product source custody handoff",
            ));
        }
        *witness = Some(product_saved_create_witness(
            source,
            &anchors,
            &composition,
            &final_snapshot,
            &final_readback,
            &output,
            &cuts,
        )?);
        Ok(json!({
            "schema": PRODUCT_ATTEMPT_SCHEMA, "phase": request.phase_label(),
            "project_id": request.project_id, "expected": request.expected,
            "scene_id": request.scene_id, "capsule_id": request.capsule_id,
            "recipe_source_asset_id": source.source_asset_id, "recipe_source_sha256": source.source_sha256,
            "island": composition.island, "native_session": composition.snapshot.native_session,
            "output": output, "cut_probes": cuts, "non_keyframe_measurement": measurement,
            "readback": final_readback, "source_snapshot": final_snapshot,
            "coordinator_outcomes_retained": true,
            "broker_terminal_custody_requires_completed_receipts": true,
            "source_cas_committed": false, "fresh_session_reopen_performed": false,
            "full_production_render_performed": false, "visual_acceptance": false,
            "formal_acceptance": false,
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::blender_product_scene::{CameraCurve, project_product_scene};

    fn fixture_spec() -> (ProductProjection, ValidatedProductSpec) {
        let projection =
            project_product_scene(&ProductScene::vessel("fixture", CameraCurve::Linear))
                .expect("closed product projection");
        let spec = ValidatedProductSpec::decode(&projection).expect("pinned SDK decoder");
        (projection, spec)
    }

    fn fixture_issued_plan() -> (ProductIssuedPlan, ValidatedProductSpec, NativeSnapshot) {
        let (_, spec) = fixture_spec();
        let before = NativeSnapshot {
            native_session: "fixture-native-session".into(),
            island: None,
            fingerprint: composition::canonical_digest(&json!({"fixture": "empty"})).unwrap(),
            drift: false,
            total: 0,
            items: vec![],
            source_only: true,
            exhaustive: true,
        };
        let owner = composition::Owner {
            session: "fixture-owner".into(),
            principal: composition::PrincipalBinding::HostSession,
        };
        // An inert binding for the SDK's pure planner, never an execution grant or descriptor.
        let bindings = vec![composition::CapabilityBinding {
            phase: composition::Phase::Plan,
            command: "fixture.product.plan".into(),
            descriptor: composition::canonical_digest(&json!({"fixture_only": true})).unwrap(),
            effects: BTreeSet::from([composition::EffectClass::Inspect]),
        }];
        let prepared = semwright_driver_blender::authoring::prepare(
            owner,
            AuthoringIntent::Create {
                spec: spec.0.clone(),
            },
            &before,
            "fixture_product".into(),
            bindings,
        )
        .expect("actual SDK pure planner accepts the fixed projection");
        (
            ProductIssuedPlan {
                plan_ref: "fixture-issued-plan".into(),
                plan: prepared.plan,
            },
            spec,
            before,
        )
    }

    fn fixture_report(
        plan: &ProductSdkPlan,
        snapshot: &NativeSnapshot,
    ) -> composition::VerificationReport {
        let base = snapshot.base(&plan.body.owner).unwrap();
        composition::VerificationReport {
            execution_status: composition::ExecutionStatus::Completed,
            support_level: composition::SupportLevel::Native,
            effects_observed: vec![],
            effects_unobservable: vec![],
            validation: composition::ValidationReport {
                plan_digest: plan.digest.clone(),
                base: base.clone(),
                required_rules: plan.body.required_rules.clone(),
                checks: plan
                    .body
                    .required_rules
                    .iter()
                    .map(|rule| composition::RuleResult {
                        rule: rule.clone(),
                        version: 1,
                        verdict: composition::Verdict::Pass,
                        evidence_class: composition::EvidenceClass::Deterministic,
                        reason: None,
                        evidence: vec![composition::ObservationRef {
                            id: format!("fixture-{rule}"),
                            base: base.clone(),
                            source: composition::EvidenceSource::Fixture,
                            method: "test-fixture".into(),
                            method_version: 1,
                            scope: vec![],
                            artifact: None,
                            exhaustive: true,
                        }],
                    })
                    .collect(),
            },
        }
    }

    #[test]
    fn product_projection_is_decoded_by_the_actual_pinned_sdk() {
        for camera_curve in [CameraCurve::Linear, CameraCurve::BezierAutoClamped] {
            let projection =
                project_product_scene(&ProductScene::vessel("fixture", camera_curve)).unwrap();
            let spec = ValidatedProductSpec::decode(&projection).unwrap();
            assert_eq!(spec.0.entities.len(), PRODUCT_ENTITY_IDS.len());
        }
    }

    #[test]
    fn sdk_decoder_rejects_foreign_or_invalid_projection_before_dispatch() {
        let (mut projection, _) = fixture_spec();
        projection.composition_spec["unexpected_executor"] = json!("shell");
        assert!(ValidatedProductSpec::decode(&projection).is_err());
        let (mut projection, _) = fixture_spec();
        projection.composition_spec["meters_per_unit"] = json!(0.0);
        assert!(ValidatedProductSpec::decode(&projection).is_err());
        let (mut projection, _) = fixture_spec();
        projection.composition_spec["entities"] = json!([]);
        assert!(ValidatedProductSpec::decode(&projection).is_err());
    }

    #[test]
    fn semantic_read_epoch_rejects_mixed_generation_and_unbound_reference_suffix() {
        let mut epoch = ProductReadEpoch::default();
        epoch
            .reference("blender-rna/v1/scenes/Rml4dHVyZQ@7", 7)
            .unwrap();
        epoch
            .reference("blender-rna/v1/objects/Q2FtZXJh@7", 7)
            .unwrap();
        assert!(
            epoch
                .reference("blender-rna/v1/objects/Q2FtZXJh@8", 8)
                .is_err()
        );
        assert!(
            ProductReadEpoch::default()
                .reference("blender-rna/v1/objects/Q2FtZXJh@7", 8)
                .is_err()
        );
        for generation in [0, i32::MAX as u64 + 1] {
            assert!(ProductReadEpoch::default().observe(generation).is_err());
        }
        assert!(
            ProductReadEpoch::default()
                .reference("foreign/v1/name@7", 7)
                .is_err()
        );
    }

    #[test]
    fn product_relation_paths_reject_out_of_profile_key_and_cap_addresses() {
        for binding in [
            Binding::CameraKey {
                entity: "camera_form",
                axis: 3,
                frame: 300,
            },
            Binding::CameraKey {
                entity: "camera_form",
                axis: 0,
                frame: 601,
            },
            Binding::CameraKey {
                entity: "camera_surface",
                axis: 0,
                frame: 300,
            },
            Binding::CameraKey {
                entity: "foreign_camera",
                axis: 0,
                frame: 300,
            },
            Binding::CylinderCap {
                entity: "vessel_body",
                index: 0,
            },
            Binding::CylinderCap {
                entity: "vessel_floor",
                index: 2,
            },
            Binding::Marker(2),
            Binding::FontVisibilityKey(150),
        ] {
            assert!(product_binding_path(&binding).is_err(), "{binding:?}");
        }
        let (root, hops) = product_binding_path(&Binding::CameraKey {
            entity: "camera_form",
            axis: 2,
            frame: 300,
        })
        .unwrap();
        assert_eq!(root, Binding::Entity("camera_form"));
        assert_eq!(hops.len(), 7);
        assert!(matches!(
            hops.last().map(|hop| &hop.selection),
            Some(RnaSelection::KeyFrame(300))
        ));
    }

    #[test]
    fn mutation_ack_rejects_wrong_target_generation_property_or_value() {
        let call = NativeCall {
            operation: "driver.blender.semantic.property.set",
            input: json!({"ref":"blender-rna/v1/scenes/Rml4dHVyZQ@7",
                "property":"frame_current","value":600}),
        };
        let valid = json!({"ref":"blender-rna/v1/scenes/Rml4dHVyZQ@8",
            "property":"frame_current","value":600,"changed":true,"generation":8});
        assert_eq!(
            check_product_mutation_ack(&call, &valid, 7, &BTreeMap::new()).unwrap(),
            Some(8)
        );
        for (key, wrong) in [
            ("ref", json!("blender-rna/v1/scenes/T3RoZXI@8")),
            ("ref", json!("blender-rna/v1/scenes/Rml4dHVyZQ@7")),
            ("generation", json!(7)),
            ("property", json!("frame_end")),
            ("value", json!(601)),
            ("changed", json!(false)),
        ] {
            let mut data = valid.clone();
            data[key] = wrong;
            assert!(
                check_product_mutation_ack(&call, &data, 7, &BTreeMap::new()).is_err(),
                "{key}"
            );
        }
    }

    #[test]
    fn keyframe_ack_accepts_sdk_float_frame_and_rejects_a_different_key() {
        let call = NativeCall {
            operation: "driver.blender.animation.keyframe.insert",
            input: json!({"ref":"blender-rna/v1/objects/Rm9udA@7",
                "property":"hide_render","frame":600,"index":0}),
        };
        let mut data = json!({"ref":"blender-rna/v1/objects/Rm9udA@8",
            "property":"hide_render","frame":600.0,"index":0,"changed":true,"generation":8});
        assert_eq!(
            check_product_mutation_ack(&call, &data, 7, &BTreeMap::new()).unwrap(),
            Some(8)
        );
        data["frame"] = json!(599.0);
        assert!(check_product_mutation_ack(&call, &data, 7, &BTreeMap::new()).is_err());
    }

    #[test]
    fn node_add_ack_matches_the_existing_input_type_and_output_node_type() {
        let call = NativeCall {
            operation: "driver.blender.node.add",
            input: json!({"tree_ref":"blender-rna/v1/materials/SW5r@7",
                "type":"ShaderNodeEmission","name":"AnnotationEmission"}),
        };
        let mut data = json!({"ref":"blender-rna/v1/materials/SW5r/bm9kZQ@8",
            "name":"AnnotationEmission","node_type":"ShaderNodeEmission",
            "changed":true,"generation":8});
        assert!(check_product_mutation_ack(&call, &data, 7, &BTreeMap::new()).is_ok());
        data["node_type"] = json!("ShaderNodeBsdfPrincipled");
        assert!(check_product_mutation_ack(&call, &data, 7, &BTreeMap::new()).is_err());
    }

    #[test]
    fn relation_ack_requires_both_fresh_observed_endpoints() {
        let call = NativeCall {
            operation: "driver.blender.semantic.relation.set",
            input: json!({"ref":"blender-rna/v1/scenes/Rml4dHVyZQ@7",
                "property":"camera","target_ref":"blender-rna/v1/objects/Q2FtZXJh@7"}),
        };
        let mut data = json!({"ref":"blender-rna/v1/scenes/Rml4dHVyZQ@8",
            "property":"camera","target_ref":"blender-rna/v1/objects/Q2FtZXJh@8",
            "changed":true,"generation":8});
        assert!(check_product_mutation_ack(&call, &data, 7, &BTreeMap::new()).is_ok());
        data["target_ref"] = json!("blender-rna/v1/objects/T3RoZXI@8");
        assert!(check_product_mutation_ack(&call, &data, 7, &BTreeMap::new()).is_err());
    }

    #[test]
    fn issued_plan_from_actual_sdk_is_bound_to_original_source_snapshot_and_intent() {
        let (issued, spec, before) = fixture_issued_plan();
        assert_eq!(
            check_product_issued_plan(&issued, &spec, &before).unwrap(),
            "fixture_product"
        );
        let mut changed_snapshot = before.clone();
        changed_snapshot.native_session = "different-native-session".into();
        assert!(check_product_issued_plan(&issued, &spec, &changed_snapshot).is_err());
        changed_snapshot = before.clone();
        changed_snapshot.fingerprint = composition::canonical_digest(&json!("changed")).unwrap();
        assert!(check_product_issued_plan(&issued, &spec, &changed_snapshot).is_err());

        let projection = project_product_scene(&ProductScene {
            roughness: 0.7,
            ..ProductScene::vessel("fixture", CameraCurve::Linear)
        })
        .unwrap();
        let other_spec = ValidatedProductSpec::decode(&projection).unwrap();
        assert!(check_product_issued_plan(&issued, &other_spec, &before).is_err());
    }

    #[test]
    fn issued_plan_rejects_tampered_digest_and_cross_island_operations() {
        let (mut issued, spec, before) = fixture_issued_plan();
        issued.plan.digest = composition::canonical_digest(&json!("different-plan")).unwrap();
        assert!(check_product_issued_plan(&issued, &spec, &before).is_err());
        let (mut issued, spec, before) = fixture_issued_plan();
        match &mut issued.plan.body.changes.operations[1].payload {
            NativeOperation::Material { island, .. } => *island = "foreign_island".into(),
            _ => panic!("fixed profile's first non-collection operation is a material"),
        }
        issued.plan.digest = composition::canonical_digest(&issued.plan.body).unwrap();
        assert!(check_product_issued_plan(&issued, &spec, &before).is_err());
    }

    #[test]
    fn fixture_evidence_and_incomplete_native_execution_cannot_certify_the_plan() {
        let (issued, _, snapshot) = fixture_issued_plan();
        let mut report = fixture_report(&issued.plan, &snapshot);
        assert_eq!(report.verdict().unwrap(), composition::Verdict::Unknown);
        assert!(check_product_composition_report(&report, &issued.plan, &snapshot).is_err());
        report.execution_status = composition::ExecutionStatus::Unknown;
        assert!(check_product_composition_report(&report, &issued.plan, &snapshot).is_err());
        report.execution_status = composition::ExecutionStatus::Completed;
        report.validation.required_rules.clear();
        assert!(check_product_composition_report(&report, &issued.plan, &snapshot).is_err());
    }

    fn fixture_composition() -> (ValidatedProductSpec, ProductCompositionObservation) {
        let (issued, spec, mut snapshot) = fixture_issued_plan();
        snapshot.island = Some("fixture_product".into());
        snapshot.items = PRODUCT_ENTITY_IDS
            .iter()
            .map(|entity| {
                json!({
                    "entity": entity, "name": format!("Fixture_{entity}"),
                    "native_island": "fixture_product", "library": false,
                    "collections": ["FixtureProductCollection"],
                    "materials": if *entity == "vessel_body" {
                        vec![json!({"id":"coating","name":"FixtureCoating","library":false})]
                    } else { vec![] },
                })
            })
            .collect();
        snapshot.total = snapshot.items.len();
        snapshot.fingerprint = composition::canonical_digest(&snapshot.items).unwrap();
        let verification = fixture_report(&issued.plan, &snapshot);
        (
            spec,
            ProductCompositionObservation {
                issued,
                island: "fixture_product".into(),
                snapshot,
                verification,
            },
        )
    }

    #[test]
    fn source_anchor_parser_uses_observed_names_and_rejects_ambiguous_ownership() {
        let (spec, observation) = fixture_composition();
        let mut anchors = ProductAnchors::default();
        install_composition_anchors(&mut anchors, &spec, &observation).unwrap();
        assert_eq!(
            anchors.collection.as_ref().unwrap().native_name,
            "FixtureProductCollection"
        );
        assert_eq!(
            product_root_name(&anchors, &Binding::CoatingMaterial).unwrap(),
            "FixtureCoating"
        );
        assert_eq!(anchors.roots.len(), PRODUCT_ENTITY_IDS.len() + 1);

        let (spec, mut observation) = fixture_composition();
        observation.snapshot.items[1]["name"] = observation.snapshot.items[0]["name"].clone();
        assert!(
            install_composition_anchors(&mut ProductAnchors::default(), &spec, &observation)
                .is_err()
        );
        let (spec, mut observation) = fixture_composition();
        observation.snapshot.items[0]["native_island"] = json!("foreign_island");
        assert!(
            install_composition_anchors(&mut ProductAnchors::default(), &spec, &observation)
                .is_err()
        );
        let (spec, mut observation) = fixture_composition();
        observation.snapshot.items[0]["materials"] = json!([]);
        assert!(
            install_composition_anchors(&mut ProductAnchors::default(), &spec, &observation)
                .is_err()
        );
    }
}

const PRODUCT_CYCLE_SCHEMA: &str = "motionwright.blender.product-editable-cycle/1";

#[derive(Debug, Clone, Serialize)]
pub(crate) enum BlenderProductReopenPhase {
    EditAnnotation { body: String },
    VerifyEdited,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct BlenderProductReopenRequest {
    pub project_id: Uuid,
    pub expected: RevisionStamp,
    pub scene_id: Uuid,
    pub capsule_id: Uuid,
    pub request_id: String,
    pub phase: BlenderProductReopenPhase,
}

impl OriginalPhaseScope for BlenderProductReopenRequest {
    fn project_id(&self) -> Uuid {
        self.project_id
    }
    fn expected(&self) -> &RevisionStamp {
        &self.expected
    }
    fn request_id(&self) -> &str {
        &self.request_id
    }
    fn phase_label(&self) -> &'static str {
        match &self.phase {
            BlenderProductReopenPhase::EditAnnotation { .. } => "product-reopen-edit-save",
            BlenderProductReopenPhase::VerifyEdited => "product-reopen-verify",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ProductSavedStage {
    Original,
    EditedAnnotation,
}

// This is a same-process continuation, not a serializable/importable native grant.
// Fields and constructors remain private. Losing it requires the controller's durable
// custody restoration; imported JSON is never promoted back into this type.
pub(crate) struct ProductNativeSourceWitness {
    origin_project: motionwright_domain::Project,
    scene_id: Uuid,
    capsule_id: Uuid,
    recipe_asset_id: Uuid,
    recipe_sha256: String,
    source: ProductScene,
    anchors: ProductAnchors,
    island: String,
    island_fingerprint: String,
    expected_readback: Value,
    previous_native_sessions: BTreeSet<String>,
    file: BlenderOriginalFile,
    original_cut_601_sha256: String,
    original_annotation: String,
    stage: ProductSavedStage,
}

fn source_readback_error() -> Error {
    backend("Reopened Blender source differs from its independently observed saved state")
}

fn scalar_source_row(row: &Value) -> NativeResult<Value> {
    let property = row
        .get("property")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(source_readback_error)?;
    let value = row.get("value").ok_or_else(source_readback_error)?;
    // Values remain intact, including an object field named "ref".
    // Target ordering is the fixed order of product_readback, never a caller list.
    Ok(json!({"property": property, "value": value}))
}

fn source_structural_row(row: &Value, reference_fields: &[&str]) -> NativeResult<Value> {
    let mut object = row.as_object().cloned().ok_or_else(source_readback_error)?;
    for key in reference_fields {
        object.remove(*key);
    }
    Ok(Value::Object(object))
}

fn product_source_readback(readback: &ProductReadbackObservation) -> NativeResult<Value> {
    let structural = |rows: &[Value], keys: &[&str]| -> NativeResult<Vec<Value>> {
        rows.iter()
            .map(|row| source_structural_row(row, keys))
            .collect()
    };
    Ok(json!({
        "scalar_readbacks": readback.scalar_readbacks.iter()
            .map(scalar_source_row).collect::<NativeResult<Vec<_>>>()?,
        "relation_readbacks": structural(&readback.relation_readbacks,
            &["owner_ref", "observed_ref", "mesh_ref"])?,
        "node_graphs": structural(&readback.node_graphs,
            &["owner_ref", "tree_ref", "link_ref"])?,
        "camera_handles": structural(&readback.camera_handles, &["ref"])?,
        "observed_configuration": structural(&readback.observed_configuration,
            &["ref", "owner_ref", "observed_ref"])?,
    }))
}

fn product_annotation_expectation(before: &Value, old: &str, new: &str) -> NativeResult<Value> {
    if old == new {
        return Err(invalid("The product annotation edit is a no-op"));
    }
    let mut expected = before.clone();
    let rows = expected
        .get_mut("scalar_readbacks")
        .and_then(Value::as_array_mut)
        .ok_or_else(source_readback_error)?;
    let indices = rows
        .iter()
        .enumerate()
        .filter(|(_, row)| row.get("property").and_then(Value::as_str) == Some("body"))
        .map(|(index, _)| index)
        .collect::<Vec<_>>();
    if indices.len() != 1 || rows[indices[0]].get("value") != Some(&json!(old)) {
        return Err(source_readback_error());
    }
    rows[indices[0]]["value"] = json!(new);
    Ok(expected)
}

fn product_reopen_request_valid(
    request: &BlenderProductReopenRequest,
    witness: &ProductNativeSourceWitness,
) -> NativeResult<()> {
    product_request_valid(&BlenderProductCreateRequest {
        project_id: request.project_id,
        expected: request.expected.clone(),
        scene_id: request.scene_id,
        capsule_id: request.capsule_id,
        request_id: request.request_id.clone(),
    })?;
    if request.project_id != witness.origin_project.id
        || request.scene_id != witness.scene_id
        || request.capsule_id != witness.capsule_id
        || witness.previous_native_sessions.is_empty()
        || witness.previous_native_sessions.len() > 2
        || !is_sha256(&witness.file.sha256)
        || witness.file.bytes == 0
        || witness.file.bytes > ORIGINAL_MAX_BYTES
    {
        return Err(invalid(
            "Product reopen request does not bind its saved source witness",
        ));
    }
    match (
        &request.phase,
        witness.stage,
        witness.file.relative_path.as_str(),
    ) {
        (
            BlenderProductReopenPhase::EditAnnotation { body },
            ProductSavedStage::Original,
            ORIGINAL_FILE,
        ) if witness.previous_native_sessions.len() == 1 => {
            let (next, steps) = crate::blender_product_scene::project_product_edit(
                &witness.source,
                &crate::blender_product_scene::ProductEdit::Annotation(body.clone()),
            )
            .map_err(|_| invalid("Product annotation edit is outside the closed source profile"))?;
            if next.annotation == witness.source.annotation
                || steps.len() != 1
                || steps[0].operation() != "driver.blender.semantic.property.set"
                || steps[0].required_bindings() != vec![&Binding::FontData]
            {
                return Err(invalid(
                    "Product edit is not one explicit FONT body mutation",
                ));
            }
        }
        (
            BlenderProductReopenPhase::VerifyEdited,
            ProductSavedStage::EditedAnnotation,
            EDITED_FILE,
        ) if witness.previous_native_sessions.len() == 2 => (),
        _ => {
            return Err(invalid(
                "Product reopen phase does not follow its saved source stage",
            ));
        }
    }
    Ok(())
}

fn product_native_snapshot(data: Value) -> NativeResult<NativeSnapshot> {
    let snapshot: NativeSnapshot = serde_json::from_value(data).map_err(|_| product_sdk_error())?;
    composition::bounded_id(&snapshot.native_session).map_err(|_| product_sdk_error())?;
    if let Some(island) = &snapshot.island {
        composition::bounded_id(island).map_err(|_| product_sdk_error())?;
    }
    if !snapshot.source_only
        || !snapshot.exhaustive
        || snapshot.total != snapshot.items.len()
        || snapshot.total > 512
        || !is_sha256(snapshot.fingerprint.as_str())
    {
        return Err(product_sdk_error());
    }
    Ok(snapshot)
}

fn check_reopened_product_snapshot(
    snapshot: &NativeSnapshot,
    witness: &ProductNativeSourceWitness,
    native_session: &str,
) -> NativeResult<()> {
    if snapshot.native_session != native_session
        || snapshot.island.as_deref() != Some(witness.island.as_str())
        || snapshot.fingerprint.as_str() != witness.island_fingerprint.as_str()
        || snapshot.total != PRODUCT_ENTITY_IDS.len()
    {
        return Err(source_readback_error());
    }
    // Preserve drift as observed. Semantic augmentation legitimately changed
    // the pre-augmentation SDK marker; a matching fingerprint is not a new PASS.
    Ok(())
}

fn check_product_fresh_context(
    snapshot: &NativeSnapshot,
    previous: &BTreeSet<String>,
) -> NativeResult<()> {
    if !(1..=2).contains(&previous.len())
        || snapshot.island.is_some()
        || previous.contains(&snapshot.native_session)
        || snapshot.items.iter().any(|row| {
            row.get("native_island")
                .is_some_and(|value| !value.is_null())
        })
    {
        return Err(Error::new(
            ErrorCode::Conflict,
            "Product reopen requires a different empty native runtime context",
        ));
    }
    Ok(())
}

fn product_source_scope_project(
    service: &motionwright_service::StudioService,
    request: &BlenderProductReopenRequest,
    witness: &ProductNativeSourceWitness,
) -> NativeResult<motionwright_domain::Project> {
    product_reopen_request_valid(request, witness)?;
    let project = service.project(request.project_id).map_err(storage_error)?;
    let scene = project
        .scenes
        .iter()
        .find(|scene| scene.id == witness.scene_id);
    let original_scene = witness
        .origin_project
        .scenes
        .iter()
        .find(|scene| scene.id == witness.scene_id);
    if project.stamp() != request.expected
        || scene.is_none()
        || scene != original_scene
        || project.production_design.plan != witness.origin_project.production_design.plan
        || project
            .production_design
            .camera_reframes
            .iter()
            .any(|row| row.scene_id == witness.scene_id)
        || project
            .production_design
            .heroes
            .iter()
            .any(|row| row.scene_id == witness.scene_id)
        || project
            .production_design
            .stages
            .iter()
            .any(|row| row.scene_id == witness.scene_id)
        || project
            .production_design
            .procedural_fields
            .iter()
            .any(|row| row.scene_id == witness.scene_id)
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Saved product Scene, clock, source features or full Project stamp changed",
        ));
    }
    validate_product_source_scene(
        &project,
        scene.ok_or_else(|| invalid("Saved product Scene is absent"))?,
    )?;
    let recipe = project
        .assets
        .iter()
        .find(|asset| asset.id == witness.recipe_asset_id)
        .ok_or_else(|| invalid("Product origin recipe is no longer attached"))?;
    let original_recipe = witness
        .origin_project
        .assets
        .iter()
        .find(|asset| asset.id == witness.recipe_asset_id)
        .ok_or_else(|| invalid("Product witness has no original attached recipe"))?;
    if recipe.content_sha256.as_deref() != Some(witness.recipe_sha256.as_str())
        || recipe.media_type != original_recipe.media_type
    {
        return Err(invalid("Saved product origin recipe identity changed"));
    }
    Ok(project)
}

fn product_asset_cas_matches(
    service: &motionwright_service::StudioService,
    project: &motionwright_domain::Project,
    witness: &ProductNativeSourceWitness,
    native_asset_id: Uuid,
    admitted_native_media_type: &'static str,
) -> NativeResult<()> {
    // The source caller supplies this fixed codec identity, never request data.
    if admitted_native_media_type.is_empty() || native_asset_id == witness.recipe_asset_id {
        return Err(invalid(
            "Product native source requires a separate admitted asset",
        ));
    }
    let asset = project
        .assets
        .iter()
        .find(|asset| asset.id == native_asset_id)
        .ok_or_else(|| invalid("Product native source is not attached to the saved Project"))?;
    if asset.media_type != admitted_native_media_type
        || asset.content_sha256.as_deref() != Some(witness.file.sha256.as_str())
    {
        return Err(invalid(
            "Product native asset does not match the completed source save",
        ));
    }
    {
        let handle = service.store();
        let store = handle.lock();
        if store.load_project(project.id).map_err(storage_error)? != *project {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Project changed before product source CAS inspection",
            ));
        }
        let recipe = store
            .read_blob(&witness.recipe_sha256, PRODUCT_RECIPE_MAX_BYTES)
            .map_err(storage_error)?;
        if hex::encode(sha2::Sha256::digest(&recipe)) != witness.recipe_sha256 {
            return Err(invalid(
                "Original product recipe CAS bytes no longer match their digest",
            ));
        }
        let native = store
            .read_blob(&witness.file.sha256, ORIGINAL_MAX_BYTES)
            .map_err(storage_error)?;
        if native.len() as u64 != witness.file.bytes
            || hex::encode(sha2::Sha256::digest(&native)) != witness.file.sha256
        {
            return Err(invalid(
                "Native product CAS bytes do not match the completed .blend save",
            ));
        }
    }
    if service.project(project.id).map_err(storage_error)? != *project {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Project changed during product source CAS inspection",
        ));
    }
    Ok(())
}

fn read_saved_product_native_source(
    service: &motionwright_service::StudioService,
    request: &BlenderProductReopenRequest,
    witness: &ProductNativeSourceWitness,
    admitted_native_media_type: &'static str,
) -> NativeResult<(motionwright_domain::Project, Uuid)> {
    let project = product_source_scope_project(service, request, witness)?;
    let capsules = project
        .production_design
        .capsules
        .iter()
        .filter(|capsule| capsule.scene_id == witness.scene_id)
        .collect::<Vec<_>>();
    if capsules.len() != 1 || capsules[0].id != witness.capsule_id {
        return Err(invalid(
            "Product reopen requires the same unambiguous saved native capsule",
        ));
    }
    let capsule = capsules[0];
    capsule
        .validate(&project.scenes, &project.assets)
        .map_err(|_| invalid("Saved product native capsule is invalid"))?;
    if capsule.source_sha256 != witness.file.sha256
        || !capsule
            .editable_parameters
            .iter()
            .any(|path| path == "product.annotation.body")
    {
        return Err(invalid(
            "Saved product capsule does not bind the requested editable .blend",
        ));
    }
    let source_asset_id = capsule.source_asset_id;
    product_asset_cas_matches(
        service,
        &project,
        witness,
        source_asset_id,
        admitted_native_media_type,
    )?;
    Ok((project, source_asset_id))
}

/// Historical two-step capsule shape used only by retained contract fixtures.
/// Production source saves use the existing atomic service boundary.
#[cfg(test)]
fn product_repoint_capsule(
    prior: &motionwright_domain::NativeCapsule,
    imported_asset_id: Uuid,
    source_sha256: &str,
) -> motionwright_domain::NativeCapsule {
    use motionwright_domain::{Fidelity, FidelityReport};
    let mut capsule = prior.clone();
    capsule.source_asset_id = imported_asset_id;
    capsule.source_sha256 = source_sha256.into();
    // The fixed runtime version was observed before this native file was saved.
    // No classification or acceptance for earlier bytes transfers to this source.
    capsule.fidelity = FidelityReport {
        renderer: "Blender".into(),
        renderer_version: "4.5.14".into(),
        visual: Fidelity::Unavailable,
        temporal: Fidelity::Unavailable,
        structural: Fidelity::Unavailable,
        editable: Fidelity::Unavailable,
        losses: vec![
            "Source bytes changed; fidelity for this exact saved source has not been established."
                .into(),
        ],
        evidence_sha256: None,
    };
    capsule.property_fidelity.clear();
    capsule.editable_parameters = vec!["product.annotation.body".into()];
    capsule.native_editor_hint = "Blender".into();
    capsule
}

fn check_product_staged_source(
    root: &Path,
    witness: &ProductNativeSourceWitness,
) -> NativeResult<()> {
    verify_output_artifact(
        root,
        &witness.file.relative_path,
        &witness.file.sha256,
        ORIGINAL_MAX_BYTES,
    )?;
    let actual = inspect_original_file(root, &witness.file.relative_path)?;
    if actual.bytes != witness.file.bytes || actual.sha256 != witness.file.sha256 {
        return Err(invalid(
            "Staged Blender source differs from its attached CAS asset",
        ));
    }
    Ok(())
}

impl<R: OriginalPhaseScope, P, J: OriginalCallJournal> OriginalSession<'_, R, P, J> {
    async fn product_runtime_summary(&mut self) -> NativeResult<Value> {
        let runtime = self
            .call("driver.blender.introspect.summary", json!({}), false)
            .await?;
        let runtime_keys: BTreeSet<_> = runtime
            .as_object()
            .ok_or_else(|| backend("Blender runtime summary is not an object"))?
            .keys()
            .map(String::as_str)
            .collect();
        let expected_keys = BTreeSet::from([
            "version",
            "version_string",
            "rna_types",
            "operator_categories",
            "operators",
            "available_addons",
            "enabled_addons",
            "arbitrary_python",
            "generic_operator_invoke",
        ]);
        if runtime_keys != expected_keys
            || runtime.get("version") != Some(&json!([4, 5, 14]))
            || runtime.get("arbitrary_python").and_then(Value::as_bool) != Some(false)
            || runtime
                .get("generic_operator_invoke")
                .and_then(Value::as_bool)
                != Some(false)
            || runtime
                .get("version_string")
                .and_then(Value::as_str)
                .is_none_or(|value| value.is_empty() || value.chars().count() > 128)
        {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "The native product profile requires observed Blender 4.5.14",
            ));
        }
        for (key, max) in [
            ("rna_types", 100_000),
            ("operator_categories", 4096),
            ("operators", 100_000),
            ("available_addons", 10_000),
            ("enabled_addons", 10_000),
        ] {
            if runtime
                .get(key)
                .and_then(Value::as_u64)
                .is_none_or(|value| value > max)
            {
                return Err(backend(
                    "Blender runtime summary exceeds its fixed contract",
                ));
            }
        }
        Ok(runtime)
    }

    async fn product_fresh_reopen_context(
        &mut self,
        witness: &ProductNativeSourceWitness,
    ) -> NativeResult<(Value, NativeSnapshot)> {
        let runtime = self.product_runtime_summary().await?;
        let before = product_native_snapshot(
            self.call("driver.blender.composition.inspect", json!({}), false)
                .await?,
        )?;
        check_product_fresh_context(&before, &witness.previous_native_sessions)?;
        Ok((runtime, before))
    }

    async fn product_canonical_reopen_frame(
        &mut self,
        anchors: &ProductAnchors,
    ) -> NativeResult<Value> {
        let mut epoch = ProductReadEpoch::default();
        let scene = self.product_active_scene(&mut epoch, anchors).await?;
        for (property, expected) in [("frame_current", json!(1)), ("frame_subframe", json!(0.0))] {
            let actual = self.product_property(&mut epoch, &scene, property).await?;
            if !original_value_matches(&expected, &actual) {
                return Err(backend(
                    "Reopened product source is not saved at native frame 1",
                ));
            }
        }
        let camera = self
            .product_only_relation(&mut epoch, &scene, "camera", Some("Object"))
            .await?;
        if camera.native_name != product_root_name(anchors, &Binding::Entity("camera_form"))? {
            return Err(backend(
                "Reopened product Scene does not use the canonical first camera",
            ));
        }
        let font = self
            .product_binding(&mut epoch, anchors, &Binding::FontObject)
            .await?;
        let hidden = self
            .product_property(&mut epoch, &font, "hide_render")
            .await?;
        if hidden.as_bool() != Some(true) {
            return Err(backend(
                "Reopened product annotation visibility differs at frame 1",
            ));
        }
        Ok(
            json!({"frame": 1, "subframe": 0, "camera": camera.native_name,
            "annotation_hidden": hidden, "semantic_generation": epoch.generation}),
        )
    }

    async fn product_exclusive_font_data(
        &mut self,
        anchors: &ProductAnchors,
    ) -> NativeResult<Value> {
        let mut epoch = ProductReadEpoch::default();
        let font = self
            .product_binding(&mut epoch, anchors, &Binding::FontObject)
            .await?;
        let data = self
            .product_binding(&mut epoch, anchors, &Binding::FontData)
            .await?;
        let linked = self
            .product_only_relation(&mut epoch, &font, "data", Some("TextCurve"))
            .await?;
        let users = self.product_property(&mut epoch, &data, "users").await?;
        if linked.reference != data.reference || users.as_u64() != Some(1) {
            return Err(backend(
                "Product annotation FONT datablock is shared or no longer linked",
            ));
        }
        Ok(
            json!({"object_ref": font.reference, "data_ref": data.reference,
            "users": users, "semantic_generation": epoch.generation}),
        )
    }
}

fn product_saved_create_witness(
    source: &SavedProductSource,
    anchors: &ProductAnchors,
    observation: &ProductCompositionObservation,
    final_snapshot: &NativeSnapshot,
    readback: &ProductReadbackObservation,
    file: &BlenderOriginalFile,
    cuts: &[ProductCutObservation],
) -> NativeResult<ProductNativeSourceWitness> {
    if file.relative_path != ORIGINAL_FILE
        || !is_sha256(&file.sha256)
        || file.bytes == 0
        || file.bytes > ORIGINAL_MAX_BYTES
        || final_snapshot.native_session != observation.snapshot.native_session
        || final_snapshot.island.as_deref() != Some(observation.island.as_str())
        || !is_sha256(final_snapshot.fingerprint.as_str())
    {
        return Err(invalid(
            "Completed product save cannot produce a source continuation witness",
        ));
    }
    let surface = cuts
        .iter()
        .filter(|cut| cut.frame == 601)
        .collect::<Vec<_>>();
    if surface.len() != 1 || !is_sha256(&surface[0].artifact.sha256) {
        return Err(invalid(
            "Product source witness requires its observed original surface frame",
        ));
    }
    Ok(ProductNativeSourceWitness {
        origin_project: source.project.clone(),
        scene_id: source.scene_id,
        capsule_id: source.capsule_id,
        recipe_asset_id: source.source_asset_id,
        recipe_sha256: source.source_sha256.clone(),
        source: source.source.clone(),
        anchors: anchors.clone(),
        island: observation.island.clone(),
        island_fingerprint: final_snapshot.fingerprint.as_str().to_owned(),
        expected_readback: product_source_readback(readback)?,
        previous_native_sessions: BTreeSet::from([final_snapshot.native_session.clone()]),
        file: file.clone(),
        original_cut_601_sha256: surface[0].artifact.sha256.clone(),
        original_annotation: source.source.annotation.clone(),
        stage: ProductSavedStage::Original,
    })
}

impl ProductionCoordinator {
    /// One explicit continuation in an owner-isolated fresh runtime and staging
    /// directory. The caller supplies the attached/materialized source and fixed
    /// MIME. No file copying, source import, capsule apply or retry occurs here.
    async fn reopen_saved_blender_product_source(
        &self,
        request: &BlenderProductReopenRequest,
        witness: &ProductNativeSourceWitness,
        admitted_native_media_type: &'static str,
        journal: &mut BlenderProductJournal,
        next_witness: &mut Option<ProductNativeSourceWitness>,
    ) -> NativeResult<Value> {
        product_reopen_request_valid(request, witness)?;
        if next_witness.is_some()
            || !journal.calls.is_empty()
            || !journal.evidence.is_empty()
            || journal.source_binding.is_some()
        {
            return Err(invalid(
                "Product reopen requires a new journal and empty continuation slot",
            ));
        }
        let (project, native_asset_id) = read_saved_product_native_source(
            &self.service,
            request,
            witness,
            admitted_native_media_type,
        )?;
        if self.client.connection().resource != project.resource_key() {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Native product connection resource changed",
            ));
        }
        let projection = crate::blender_product_scene::project_product_scene(&witness.source)
            .map_err(|_| invalid("Saved product source is outside its closed projection"))?;
        let spec = ValidatedProductSpec::decode(&projection)?;
        // Fix the entire post-edit expectation before any runtime invocation.
        let proposed = match &request.phase {
            BlenderProductReopenPhase::EditAnnotation { body } => {
                let (source, steps) = crate::blender_product_scene::project_product_edit(
                    &witness.source,
                    &crate::blender_product_scene::ProductEdit::Annotation(body.clone()),
                )
                .map_err(|_| invalid("Product annotation edit is unsupported"))?;
                let expected = product_annotation_expectation(
                    &witness.expected_readback,
                    &witness.source.annotation,
                    body,
                )?;
                Some((source, steps, expected))
            }
            BlenderProductReopenPhase::VerifyEdited => None,
        };
        let first_request = format!("{}:{}:000001", request.request_id, request.phase_label());
        if self
            .service
            .latest_production_receipt(request.project_id, &first_request)
            .map_err(storage_error)?
            .is_some()
        {
            return Err(Error::new(ErrorCode::Conflict,
                "Product reopen phase was already attempted; inspect receipts instead of replaying it").uncertain());
        }
        let output_root = self.client.connection().output_root.clone();
        check_product_staged_source(&output_root, witness)?;
        if proposed.is_some() {
            source_output_must_be_new(&output_root, EDITED_FILE)?;
        } else {
            product_png_must_be_new(&output_root, PRODUCT_FRAME_601)?;
        }
        journal.schema = PRODUCT_CYCLE_SCHEMA.into();
        journal.source_binding = Some(json!({
            "project_id": request.project_id, "expected": request.expected,
            "scene_id": request.scene_id, "capsule_id": request.capsule_id,
            "source_asset_id": native_asset_id, "source_sha256": witness.file.sha256,
            "source_bytes": witness.file.bytes, "relative_path": witness.file.relative_path,
            "origin_recipe_asset_id": witness.recipe_asset_id,
            "origin_recipe_sha256": witness.recipe_sha256,
            "input_source_cas_verified": true, "imported_source_authority_used": false,
            "path_open_requires_owner_isolated_staging": true,
            "phase": request.phase,
        }));
        let mut session = OriginalSession {
            coordinator: self,
            request,
            plan: &spec,
            journal,
            sequence: 0,
        };
        let (runtime, empty) = session.product_fresh_reopen_context(witness).await?;
        session.journal.record_product_evidence(
            "fresh_context_before_open",
            json!({
                "runtime": runtime, "snapshot": empty,
                "previous_native_sessions": witness.previous_native_sessions,
            }),
        );
        check_product_staged_source(&output_root, witness)?;
        let opened = session
            .call(
                "driver.blender.file.open",
                json!({"path": witness.file.relative_path}),
                true,
            )
            .await?;
        if opened
            != json!({"changed": true, "path": witness.file.relative_path, "use_scripts": false})
        {
            return Err(
                backend("Product .blend open violated its existing scoped contract").uncertain(),
            );
        }
        check_product_staged_source(&output_root, witness).map_err(Error::uncertain)?;
        let opened_snapshot = product_native_snapshot(
            session
                .call(
                    "driver.blender.composition.inspect",
                    json!({"island": witness.island}),
                    false,
                )
                .await?,
        )?;
        check_reopened_product_snapshot(&opened_snapshot, witness, &empty.native_session)?;
        session
            .journal
            .record_product_evidence("reopened_sdk_source", json!(opened_snapshot));
        let mut anchors = witness.anchors.clone(); // names/identities only; no stored RNA references
        let canonical = session.product_canonical_reopen_frame(&anchors).await?;
        session
            .journal
            .record_product_evidence("reopened_canonical_frame", canonical);
        let exclusive_font = session.product_exclusive_font_data(&anchors).await?;
        session
            .journal
            .record_product_evidence("exclusive_annotation_data", exclusive_font);
        let before = session
            .product_readback(&anchors, &witness.source, &projection)
            .await?;
        session
            .journal
            .record_product_evidence("independent_reopen_readback", json!(before));
        if product_source_readback(&before)? != witness.expected_readback {
            return Err(source_readback_error());
        }
        if let Some((edited_source, steps, expected_after)) = proposed {
            let calls = session.product_run_steps(&mut anchors, &steps).await?;
            session
                .journal
                .record_product_evidence("one_annotation_mutation", json!(calls));
            let edited_projection =
                crate::blender_product_scene::project_product_scene(&edited_source)
                    .map_err(|_| invalid("Edited product projection is unsupported"))?;
            let after = session
                .product_readback(&anchors, &edited_source, &edited_projection)
                .await?;
            session
                .journal
                .record_product_evidence("independent_edited_readback", json!(after));
            if product_source_readback(&after)? != expected_after {
                return Err(source_readback_error());
            }
            let after_snapshot = product_native_snapshot(
                session
                    .call(
                        "driver.blender.composition.inspect",
                        json!({"island": witness.island}),
                        false,
                    )
                    .await?,
            )?;
            check_reopened_product_snapshot(&after_snapshot, witness, &empty.native_session)?;
            session
                .journal
                .record_product_evidence("edited_sdk_source", json!(after_snapshot));
            source_output_must_be_new(&output_root, EDITED_FILE)?;
            let saved = session
                .call(
                    "driver.blender.file.save",
                    json!({"path": EDITED_FILE}),
                    true,
                )
                .await?;
            if saved != json!({"changed": true, "path": EDITED_FILE, "copy": true}) {
                return Err(
                    backend("Edited product .blend save violated its scoped contract").uncertain(),
                );
            }
            let output = inspect_original_file(&output_root, EDITED_FILE)?;
            session
                .journal
                .record_product_evidence("edited_native_source_saved", json!(output));
            if output.sha256 == witness.file.sha256 {
                return Err(backend(
                    "The saved product source did not preserve different edited bytes",
                ));
            }
            check_product_staged_source(&output_root, witness)?;
            if self
                .service
                .project(request.project_id)
                .map_err(storage_error)?
                != project
            {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Project changed before edited source custody handoff",
                ));
            }
            let mut previous_native_sessions = witness.previous_native_sessions.clone();
            if !previous_native_sessions.insert(empty.native_session.clone())
                || previous_native_sessions.len() != 2
            {
                return Err(source_readback_error());
            }
            *next_witness = Some(ProductNativeSourceWitness {
                origin_project: witness.origin_project.clone(),
                scene_id: witness.scene_id,
                capsule_id: witness.capsule_id,
                recipe_asset_id: witness.recipe_asset_id,
                recipe_sha256: witness.recipe_sha256.clone(),
                source: edited_source.clone(),
                anchors,
                island: witness.island.clone(),
                island_fingerprint: witness.island_fingerprint.clone(),
                expected_readback: expected_after,
                previous_native_sessions,
                file: output.clone(),
                original_cut_601_sha256: witness.original_cut_601_sha256.clone(),
                original_annotation: witness.original_annotation.clone(),
                stage: ProductSavedStage::EditedAnnotation,
            });
            Ok(json!({
                "schema": PRODUCT_CYCLE_SCHEMA, "phase": request.phase_label(),
                "project_id": project.id, "expected": request.expected,
                "source_asset_id": native_asset_id, "input_source": witness.file,
                "output": output, "native_session": empty.native_session,
                "edit": {"parameter": "product.annotation.body",
                    "before": witness.source.annotation, "after": edited_source.annotation},
                "before": before, "after": after,
                "reopened_source_cas_verified": true, "fresh_session_reopen_performed": true,
                "one_field_edit_readback_verified": true, "edited_source_cas_committed": false,
                "edited_source_fresh_reopen_performed": false,
                "full_production_render_performed": false,
                "visual_acceptance": false, "formal_acceptance": false,
            }))
        } else {
            // This is an actual render of the third-session reopened edited file.
            // It is not a replacement for pixel decoding or visual acceptance.
            let cut = session
                .product_render_cut(&anchors, &output_root, 601)
                .await?;
            session
                .journal
                .record_product_evidence("reopened_edited_surface_render", json!(cut));
            check_product_staged_source(&output_root, witness)?;
            if self
                .service
                .project(request.project_id)
                .map_err(storage_error)?
                != project
            {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Project changed during edited-source verification",
                ));
            }
            let mut sessions = witness.previous_native_sessions.clone();
            if !sessions.insert(empty.native_session.clone()) || sessions.len() != 3 {
                return Err(source_readback_error());
            }
            Ok(json!({
                "schema": PRODUCT_CYCLE_SCHEMA, "phase": request.phase_label(),
                "project_id": project.id, "expected": request.expected,
                "source_asset_id": native_asset_id, "reopened_source": witness.file,
                "observed_native_sessions": sessions,
                "annotation": {"original": witness.original_annotation,
                    "persisted": witness.source.annotation},
                "persisted_source_readback": before, "surface_probe": cut,
                "surface_png_bytes_changed": cut.artifact.sha256 != witness.original_cut_601_sha256,
                "original_surface_png_sha256": witness.original_cut_601_sha256,
                "reopened_source_cas_verified": true, "fresh_session_reopen_performed": true,
                "edited_source_fresh_reopen_performed": true,
                "persisted_source_verified_at_native_frame": 1, "runtime_ends_at_native_frame": 601,
                "independent_pixel_comparison_performed": false,
                "external_dependency_content_verified": false,
                "full_production_render_performed": false,
                "visual_acceptance": false, "formal_acceptance": false,
            }))
        }
    }
}

#[cfg(test)]
mod editable_cycle_tests {
    use super::*;
    use crate::blender_product_scene::{CameraCurve, ProductEdit, project_product_edit};

    fn fixture_readback() -> ProductReadbackObservation {
        ProductReadbackObservation {
            semantic_generation: 7,
            scalar_readbacks: vec![
                json!({"ref":"old-text@7","property":"body","value":"EDITABLE\nSURFACE"}),
                json!({"ref":"old-material@7","property":"roughness","value":0.28}),
                json!({"ref":"old-object@7","property":"fixture_nested_value",
                    "value":{"ref":"semantic text, not a transport reference"}}),
            ],
            relation_readbacks: vec![json!({"owner_ref":"old-scene@7","property":"objects",
                    "names":["Product","Annotation"],"count":2})],
            node_graphs: vec![
                json!({"owner_ref":"old-material@7","tree_ref":"old-tree@7","link_ref":"old-link@7",
                    "from_node":"Emission","from_identifier":"Emission",
                    "to_node":"Material Output","to_identifier":"Surface"}),
            ],
            camera_handles: vec![
                json!({"ref":"old-key@7","entity":"camera_form","axis":0,"frame":300,
                    "handle_left":[200.0,0.24],"handle_right":[400.0,0.30]}),
            ],
            observed_configuration: vec![
                json!({"ref":"old-view@7","property":"look","value":"fixture-look"}),
                json!({"owner_ref":"old-font@7","observed_ref":"old-fontface@7",
                    "property":"font","name":"fixture-native-font","rna_type":"VectorFont"}),
            ],
        }
    }

    fn fixture_snapshot() -> Value {
        json!({"native_session":"fixture-new-process","island":null,"fingerprint":"ab".repeat(32),
            "drift":false,"total":0,"items":[],"source_only":true,"exhaustive":true})
    }

    #[test]
    fn cycle_normalization_preserves_nested_values_and_significant_order() {
        let first = fixture_readback();
        let mut reopened = fixture_readback();
        reopened.semantic_generation = 1;
        reopened.scalar_readbacks[0]["ref"] = json!("fresh-text@1");
        reopened.relation_readbacks[0]["owner_ref"] = json!("fresh-scene@1");
        reopened.node_graphs[0]["tree_ref"] = json!("fresh-tree@1");
        reopened.camera_handles[0]["ref"] = json!("fresh-key@1");
        reopened.observed_configuration[1]["observed_ref"] = json!("fresh-face@1");
        assert_eq!(
            product_source_readback(&first).unwrap(),
            product_source_readback(&reopened).unwrap()
        );
        reopened.scalar_readbacks[2]["value"]["ref"] = json!("changed actual source value");
        assert_ne!(
            product_source_readback(&first).unwrap(),
            product_source_readback(&reopened).unwrap()
        );
        let mut duplicated = fixture_readback();
        duplicated.relation_readbacks[0]["names"] = json!(["Product", "Annotation", "Annotation"]);
        assert_ne!(
            product_source_readback(&first).unwrap(),
            product_source_readback(&duplicated).unwrap()
        );
        let mut reordered = fixture_readback();
        reordered.camera_handles[0]["handle_left"] = json!([0.24, 200.0]);
        assert_ne!(
            product_source_readback(&first).unwrap(),
            product_source_readback(&reordered).unwrap()
        );
    }

    #[test]
    fn annotation_cycle_expectation_is_original_plus_only_the_requested_body() {
        let original = product_source_readback(&fixture_readback()).unwrap();
        let expected =
            product_annotation_expectation(&original, "EDITABLE\nSURFACE", "EDITABLE\nFINISH")
                .unwrap();
        assert_eq!(
            original["scalar_readbacks"][0]["value"],
            json!("EDITABLE\nSURFACE")
        );
        let mut actual = fixture_readback();
        actual.scalar_readbacks[0]["value"] = json!("EDITABLE\nFINISH");
        assert_eq!(product_source_readback(&actual).unwrap(), expected);
        actual.scalar_readbacks[1]["value"] = json!(0.9);
        assert_ne!(product_source_readback(&actual).unwrap(), expected);
        assert_eq!(expected["camera_handles"], original["camera_handles"]);
        assert_eq!(expected["node_graphs"], original["node_graphs"]);
        assert_eq!(
            expected["observed_configuration"],
            original["observed_configuration"]
        );
    }

    #[test]
    fn annotation_expectation_rejects_noop_wrong_preimage_and_ambiguous_body() {
        let original = product_source_readback(&fixture_readback()).unwrap();
        assert!(
            product_annotation_expectation(&original, "EDITABLE\nSURFACE", "EDITABLE\nSURFACE")
                .is_err()
        );
        assert!(
            product_annotation_expectation(&original, "different original", "EDITABLE\nFINISH")
                .is_err()
        );
        let mut ambiguous = original.clone();
        ambiguous["scalar_readbacks"]
            .as_array_mut()
            .unwrap()
            .push(json!({"property":"body","value":"EDITABLE\nSURFACE"}));
        assert!(
            product_annotation_expectation(&ambiguous, "EDITABLE\nSURFACE", "EDITABLE\nFINISH")
                .is_err()
        );
        let mut missing = original.clone();
        missing["scalar_readbacks"]
            .as_array_mut()
            .unwrap()
            .remove(0);
        assert!(
            product_annotation_expectation(&missing, "EDITABLE\nSURFACE", "EDITABLE\nFINISH")
                .is_err()
        );
    }

    #[test]
    fn native_source_snapshot_rejects_incomplete_malformed_and_unknown_evidence() {
        assert!(product_native_snapshot(fixture_snapshot()).is_ok());
        for (field, value) in [
            ("total", json!(1)),
            ("source_only", json!(false)),
            ("exhaustive", json!(false)),
            ("fingerprint", json!("not-a-digest")),
            ("native_session", json!("")),
        ] {
            let mut candidate = fixture_snapshot();
            candidate[field] = value;
            assert!(product_native_snapshot(candidate).is_err(), "{field}");
        }
        let mut extra = fixture_snapshot();
        extra["fixture_grant"] = json!(true);
        assert!(product_native_snapshot(extra).is_err());
    }

    #[test]
    fn fresh_process_check_rejects_reused_or_already_populated_context() {
        let prior = BTreeSet::from([
            "fixture-first-process".into(),
            "fixture-second-process".into(),
        ]);
        let fresh = product_native_snapshot(fixture_snapshot()).unwrap();
        assert!(check_product_fresh_context(&fresh, &prior).is_ok());
        let mut same = fixture_snapshot();
        same["native_session"] = json!("fixture-first-process");
        assert!(
            check_product_fresh_context(&product_native_snapshot(same).unwrap(), &prior).is_err()
        );
        let mut occupied = fixture_snapshot();
        occupied["items"] = json!([{"native_island":"owned_before_open"}]);
        occupied["total"] = json!(1);
        assert!(
            check_product_fresh_context(&product_native_snapshot(occupied).unwrap(), &prior)
                .is_err()
        );
        assert!(check_product_fresh_context(&fresh, &BTreeSet::new()).is_err());
    }

    #[test]
    fn existing_projection_emits_exactly_one_editable_font_body_setter() {
        let source = ProductScene::vessel("fixture", CameraCurve::Linear);
        let (edited, steps) =
            project_product_edit(&source, &ProductEdit::Annotation("EDITABLE\nFINISH".into()))
                .unwrap();
        assert_eq!(steps.len(), 1);
        assert_eq!(steps[0].required_bindings(), vec![&Binding::FontData]);
        let call = steps[0]
            .bind(|binding| {
                (binding == &Binding::FontData).then(|| ObservedReference {
                    reference: "blender-rna/v1/fixture-font@9".into(),
                    native_name: Some("fixture-annotation".into()),
                })
            })
            .unwrap();
        assert_eq!(call.operation, "driver.blender.semantic.property.set");
        assert_eq!(
            call.input,
            json!({"ref":"blender-rna/v1/fixture-font@9",
            "property":"body","value":"EDITABLE\nFINISH"})
        );
        let mut only_one_field = source;
        only_one_field.annotation = "EDITABLE\nFINISH".into();
        assert_eq!(edited, only_one_field);
    }
}

fn validate_product_source_scene(
    project: &motionwright_domain::Project,
    scene: &motionwright_domain::Scene,
) -> NativeResult<()> {
    if scene.renderer != motionwright_domain::RendererKind::Blender
        || scene.camera != motionwright_domain::CameraState::default()
        || !scene.nodes.is_empty()
        || !scene.beats.is_empty()
        || project
            .production_design
            .camera_reframes
            .iter()
            .any(|item| item.scene_id == scene.id)
        || project
            .production_design
            .heroes
            .iter()
            .any(|item| item.scene_id == scene.id)
        || project
            .production_design
            .stages
            .iter()
            .any(|item| item.scene_id == scene.id)
        || project
            .production_design
            .procedural_fields
            .iter()
            .any(|item| item.scene_id == scene.id)
        || project
            .production_design
            .semantic_graphs
            .iter()
            .any(|graph| graph.scene_id == scene.id)
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Product source requires the default Canvas camera and does not project Canvas nodes, beats or other authored Scene features",
        ));
    }
    if project
        .production_design
        .plan
        .as_ref()
        .is_some_and(|plan| !matches!(&plan.clock, motionwright_domain::ProductionClock::Timeline))
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Product source does not project a voice or music production clock",
        ));
    }
    scene
        .duration
        .validate()
        .map_err(|_| invalid("Product Scene duration is invalid"))?;
    scene
        .start
        .validate()
        .map_err(|_| invalid("Product Scene start is invalid"))?;
    if scene.start < motionwright_domain::RationalTime::ZERO
        || i128::from(scene.duration.num) != 40 * i128::from(scene.duration.den)
        || (i128::from(scene.start.num) * 30) % i128::from(scene.start.den) != 0
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Product source requires one nonnegative frame-aligned canonical Scene of exactly 40 seconds at native 30 fps",
        ));
    }
    // This phase creates an editable source at a fixed native rate. The FPS field
    // belongs to DeliverableProfile.frame_rate, not ProductionClock. No delivery
    // profile is implicitly chosen, adapted or certified by this source phase.
    Ok(())
}

#[cfg(test)]
mod source_scope_tests {
    use super::*;
    use motionwright_domain::{
        CameraState, NarrativeEvidenceKind, PlannedShot, ProductionClock, ProductionPlan, Project,
        RationalTime, RendererKind, Scene, SceneStatus,
    };

    fn fixture_project() -> Project {
        let mut project = Project::new("Product source scope fixture").unwrap();
        project.scenes.push(Scene {
            id: Uuid::from_u128(1),
            name: "Product form and surface".into(),
            objective: "Exercise the closed source profile".into(),
            start: RationalTime::ZERO,
            duration: RationalTime { num: 40, den: 1 },
            renderer: RendererKind::Blender,
            status: SceneStatus::Draft,
            beats: vec![],
            nodes: vec![],
            camera: CameraState::default(),
        });
        project
    }

    fn plan_for(project: &Project, clock: ProductionClock) -> ProductionPlan {
        ProductionPlan {
            objective: "Product source scope fixture".into(),
            audience: "Fixture reader".into(),
            concept: "One canonical source Scene with two internal camera cuts".into(),
            reference_constraints: vec![],
            exclusions: vec![],
            shots: vec![PlannedShot {
                scene_id: project.scenes[0].id,
                purpose: "Show the editable product".into(),
                claim_ids: vec![],
                asset_ids: vec![],
                evidence_kind: NarrativeEvidenceKind::SyntheticLabeled,
            }],
            clock,
            approval: None,
        }
    }

    #[test]
    fn product_source_rejects_every_unprojected_canvas_camera_field() {
        let project = fixture_project();
        let original = project.clone();
        validate_product_source_scene(&project, &project.scenes[0]).unwrap();
        let mut variants = Vec::new();
        for field in 0..5 {
            let mut camera = CameraState::default();
            match field {
                0 => camera.center_x += 1.0,
                1 => camera.center_y += 1.0,
                2 => camera.zoom = 1.1,
                3 => camera.rotation_deg = 1.0,
                4 => camera.safe_margin = 0.06,
                _ => unreachable!(),
            }
            camera.validate().unwrap();
            variants.push(camera);
        }
        for camera in variants {
            let mut changed = project.clone();
            changed.scenes[0].camera = camera;
            assert!(validate_product_source_scene(&changed, &changed.scenes[0]).is_err());
        }
        assert_eq!(project, original);
    }

    #[test]
    fn product_source_does_not_silently_project_voice_or_music_clocks() {
        let mut project = fixture_project();
        validate_product_source_scene(&project, &project.scenes[0]).unwrap();
        project.production_design.plan = Some(plan_for(&project, ProductionClock::Timeline));
        validate_product_source_scene(&project, &project.scenes[0]).unwrap();
        for clock in [
            ProductionClock::Voice {
                voice_track_id: Uuid::from_u128(2),
            },
            ProductionClock::Music {
                asset_id: Uuid::from_u128(3),
                beats: vec![RationalTime::ZERO, RationalTime { num: 20, den: 1 }],
            },
        ] {
            project.production_design.plan = Some(plan_for(&project, clock));
            let original = project.clone();
            assert!(validate_product_source_scene(&project, &project.scenes[0]).is_err());
            assert_eq!(project, original);
        }
    }

    #[test]
    fn native_source_rate_requires_exact_duration_and_frame_aligned_start() {
        let mut project = fixture_project();
        project.scenes[0].start = RationalTime { num: 31, den: 30 };
        validate_product_source_scene(&project, &project.scenes[0]).unwrap();
        project.scenes[0].start = RationalTime { num: 1, den: 60 };
        assert!(validate_product_source_scene(&project, &project.scenes[0]).is_err());
        project.scenes[0].start = RationalTime { num: -1, den: 30 };
        assert!(validate_product_source_scene(&project, &project.scenes[0]).is_err());
        project.scenes[0].start = RationalTime::ZERO;
        for duration in [
            RationalTime { num: 1199, den: 30 },
            RationalTime { num: 1201, den: 30 },
            RationalTime::ZERO,
        ] {
            project.scenes[0].duration = duration;
            assert!(validate_product_source_scene(&project, &project.scenes[0]).is_err());
        }
    }

    fn attach_source_guard_graph(project: &mut Project, scene_index: usize, profile_index: usize) {
        let mut selected = project.clone();
        selected.scenes.swap(0, scene_index);
        selected.deliverables.swap(0, profile_index);
        let mut graph = graph_source_guard_fixture::graph_for(&selected, false);
        graph.time_range.start = motionwright_domain::RationalTime::new(0, 1).unwrap();
        graph.time_range.duration = motionwright_domain::RationalTime::new(1, 1).unwrap();
        project
            .apply_change(&motionwright_domain::Change::UpsertSemanticGraph {
                graph: Box::new(graph),
            })
            .unwrap();
        project.validate().unwrap();
    }

    #[test]
    fn product_source_rejects_selected_scene_graph() {
        let mut project = fixture_project();
        validate_product_source_scene(&project, &project.scenes[0]).unwrap();
        attach_source_guard_graph(&mut project, 0, 0);
        let before = project.clone();
        assert_eq!(
            validate_product_source_scene(&project, &project.scenes[0])
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );
        assert_eq!(project, before);
    }

    #[test]
    fn product_source_preserves_other_scene_graph_scope() {
        let mut project = fixture_project();
        let mut other_scene = project.scenes[0].clone();
        other_scene.id = Uuid::new_v4();
        other_scene.start = motionwright_domain::RationalTime::new(40, 1).unwrap();
        other_scene.renderer = RendererKind::ManimCommunity;
        other_scene.nodes.clear();
        other_scene.beats.clear();
        project.scenes.push(other_scene);
        project.validate().unwrap();
        validate_product_source_scene(&project, &project.scenes[0]).unwrap();
        attach_source_guard_graph(&mut project, 1, 0);
        let before = project.clone();
        validate_product_source_scene(&project, &project.scenes[0]).unwrap();
        assert_eq!(project, before);
    }
}
