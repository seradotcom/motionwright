//! Native HyperFrames source adapter; all execution is delegated to Driver Host.
pub use motionwright_hyperframes_profile as model;
pub mod driver;
pub mod store;
pub use semwright_types::{Error, ErrorCode, Result};

#[cfg(test)]
use model::{Clip, Composition, SCHEMA, SourceBinding};
#[cfg(test)]
pub(crate) fn fixture() -> Composition {
    Composition {
        schema: SCHEMA.into(),
        binding: SourceBinding {
            project_resource: "project:00000000-0000-4000-8000-000000000001".into(),
            generation: "00000000-0000-4000-8000-000000000002".into(),
            revision: 4,
            deliverable_id: "00000000-0000-4000-8000-000000000003".into(),
        },
        width: 640,
        height: 360,
        fps: 30,
        frame_count: 60,
        background: "#101820".into(),
        clips: vec![
            Clip {
                id: "red-original".into(),
                start_frame: 0,
                end_frame_exclusive: 30,
                x: 40,
                y: 40,
                width: 240,
                height: 120,
                fill: "#E53935".into(),
                opacity_milli: 1000,
                z_index: 0,
            },
            Clip {
                id: "blue-original".into(),
                start_frame: 30,
                end_frame_exclusive: 60,
                x: 320,
                y: 180,
                width: 240,
                height: 120,
                fill: "#1E88E5".into(),
                opacity_milli: 1000,
                z_index: 1,
            },
        ],
    }
}
