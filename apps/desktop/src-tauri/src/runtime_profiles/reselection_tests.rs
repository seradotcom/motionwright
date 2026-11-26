//! Actual SQLite/retained-file preparation replacement; no packaged tool executes.
use super::*;

#[test]
fn replacement_reviews_release_unsent_plans_and_reject_their_old_permission() {
    let f = Fixture::new();
    let first = f.plan();
    let old_request = f.request(&first);
    let original = f.service.project(f.scope.project_id).unwrap();
    let mut current = first;
    for _ in 0..6 {
        let next = f.plan();
        assert_ne!(next.plan_id, current.plan_id);
        current = next;
        assert_eq!(f.profiles.state.lock().unwrap().plans.len(), 1);
    }
    let refused = f
        .profiles
        .install(&f.service, &f.grants, old_request, PIN)
        .unwrap_err();
    assert_eq!(refused.certainty, "not_started");
    assert!(
        f.service
            .production_receipts(f.scope.project_id, 256)
            .unwrap()
            .is_empty()
    );
    let installed = f.install(&current);
    assert_eq!(installed.state, "installed");
    assert_eq!(f.service.project(f.scope.project_id).unwrap(), original);
    assert_eq!(
        fs::read(f.source.join("tools/node")).unwrap(),
        executable_header()
    );
}

#[test]
fn invalid_reselection_retires_only_this_projects_unsent_review() {
    let f = Fixture::new();
    let first = f.plan();
    let other = f
        .service
        .create_project("Other pending owner review")
        .unwrap();
    let other_scope = Scope {
        project_id: other.id,
        generation: other.generation,
        revision: other.revision,
    };
    let other_plan = f
        .profiles
        .inspect(&f.service, other_scope, &f.source, PIN)
        .unwrap();
    let invalid = f.dir.path().join("invalid-owner-selected-package");
    fs::create_dir(&invalid).unwrap();
    fs::write(invalid.join(bundle::MANIFEST), b"{}").unwrap();
    assert!(
        f.profiles
            .inspect(&f.service, f.scope, &invalid, PIN)
            .is_err()
    );
    let state = f.profiles.state.lock().unwrap();
    assert!(!state.plans.contains_key(&first.plan_id));
    assert!(state.plans.contains_key(&other_plan.plan_id));
    assert_eq!(state.plans.len(), 1);
    drop(state);
    let refused = f
        .profiles
        .install(&f.service, &f.grants, f.request(&first), PIN)
        .unwrap_err();
    assert_eq!(refused.certainty, "not_started");
    assert!(!Path::new(&first.destination).exists());
    assert!(
        f.service
            .production_receipts(f.scope.project_id, 256)
            .unwrap()
            .is_empty()
    );
    assert!(
        f.service
            .production_receipts(other.id, 256)
            .unwrap()
            .is_empty()
    );
    assert_eq!(fs::read(invalid.join(bundle::MANIFEST)).unwrap(), b"{}");
}
