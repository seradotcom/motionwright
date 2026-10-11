//! A measured source, binding, and invalidation belong to the existing durable
//! SQLite/project CAS service, not a parallel data dashboard or spreadsheet backend.
use motionwright_domain::*;
use motionwright_service::StudioService;
use std::collections::BTreeSet;
use uuid::Uuid;

const SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

fn apply(service: &StudioService, p: &Project, key: &str, change: Change) -> Project {
    service
        .apply(p.id, &p.stamp(), key, &change)
        .unwrap()
        .project
}
fn node() -> CanvasNode {
    CanvasNode {
        id: Uuid::new_v4(),
        name: "Source measured figure".into(),
        kind: "text".into(),
        parent_id: None,
        x: 300.0,
        y: 270.0,
        width: 540.0,
        height: 95.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: Some("Author-supplied original".into()),
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 0,
        style: NodeStyle::default(),
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}
#[test]
fn native_owner_local_cas_persists_scoped_metrics_and_rejects_stale_source_writes() {
    let tmp = tempfile::tempdir().unwrap();
    let path = tmp.path().join("measured.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut p = service.create_project("Source-bound counts").unwrap();
    p = apply(
        &service,
        &p,
        "measured-scene",
        Change::AddScene {
            name: "Measured counter".into(),
            objective: "Source-bound counts, no invented data".into(),
            duration_seconds: 6,
        },
    );
    let scene_id = p.scenes[0].id;
    let text = node();
    p = apply(
        &service,
        &p,
        "measured-node",
        Change::AddCanvasNode {
            scene_id,
            node: text.clone(),
        },
    );
    let asset = Asset {
        id: Uuid::new_v4(),
        name: "User supplied CSV".into(),
        media_type: "text/csv".into(),
        content_sha256: Some(SHA.into()),
        source_revision: None,
    };
    p = apply(
        &service,
        &p,
        "source-asset",
        Change::AddAsset {
            asset: asset.clone(),
        },
    );
    let table = MeasuredTable {
        schema: MEASURED_DATA_SCHEMA.into(),
        id: Uuid::new_v4(),
        label: "Owner's measured counts".into(),
        version: 1,
        source_asset_id: asset.id,
        source_sha256: SHA.into(),
        rows: vec![MeasuredDatum {
            key: "orders".into(),
            label: "Orders".into(),
            milli: 123000,
            unit: MeasuredUnit::Count,
        }],
    };
    let stale = p.stamp();
    p = apply(
        &service,
        &p,
        "source-table",
        Change::UpsertMeasuredTable {
            table: table.clone(),
        },
    );
    assert!(
        service
            .apply(
                p.id,
                &stale,
                "stale-bound-write",
                &Change::UpsertMeasuredBinding {
                    binding_id: Uuid::new_v4(),
                    scene_id,
                    node_id: text.id,
                    table_id: table.id,
                    datum_key: "orders".into(),
                    view: MeasuredView::Label { decimals: 0 },
                }
            )
            .is_err()
    );
    let binding_id = Uuid::new_v4();
    p = apply(
        &service,
        &p,
        "binding",
        Change::UpsertMeasuredBinding {
            binding_id,
            scene_id,
            node_id: text.id,
            table_id: table.id,
            datum_key: "orders".into(),
            view: MeasuredView::Label { decimals: 0 },
        },
    );
    assert_eq!(p.scenes[0].nodes[0].text.as_deref(), Some("Orders · 123"));
    assert_eq!(p.production_design.measured_bindings[0].id, binding_id);
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(p.id).unwrap(), p);
    let invalid = Change::UpsertMeasuredTable {
        table: MeasuredTable {
            source_sha256: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                .into(),
            version: 2,
            ..table
        },
    };
    assert!(
        reopened
            .apply(p.id, &p.stamp(), "forged-source", &invalid)
            .is_err()
    );
    assert_eq!(reopened.project(p.id).unwrap(), p);
    let detached = apply(
        &reopened,
        &p,
        "detach-source-only",
        Change::DetachMeasuredBinding { binding_id },
    );
    assert_eq!(
        detached.scenes[0].nodes[0].text.as_deref(),
        Some("Orders · 123")
    );
    assert!(detached.production_design.measured_bindings.is_empty());
}
